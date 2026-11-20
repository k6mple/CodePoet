"""X1 隔离校验 worker：在独立子进程中校验 LLM 产出的提案与候选 IR。

只通过 ``python -B -m app.engine.assistant_worker`` 由父进程拉起：
- 不持有数据库连接，不继承 ``CODEPOET_*`` 环境变量与模型密钥；
- 自带 CPU / 内存 / 墙钟看门狗，配合父进程 Job Object / 进程组超时回收；
- 校验 schema、子树替换 / 临时模板实例化、全局 ``validate_ir`` 限额及 ``sample_scene`` 采样。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import defaultdict
from typing import Any

from pydantic import ValidationError

MAX_INPUT_BYTES = 1024 * 1024
PROTOCOL = 1

EXIT_OK = 0
EXIT_CPU = 70
EXIT_MEMORY = 71
EXIT_PROTOCOL = 72
EXIT_WALL = 73

WATCHDOG_INTERVAL_SECONDS = 0.05


def _rss_mib() -> float:
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(ProcessMemoryCounters)
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.GetCurrentProcess.argtypes = []
            psapi.GetProcessMemoryInfo.restype = ctypes.c_int
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ProcessMemoryCounters),
                ctypes.c_uint32,
            ]
            handle = kernel32.GetCurrentProcess()
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return 0.0
            return counters.WorkingSetSize / (1024 * 1024)
        with open("/proc/self/statm", "r", encoding="ascii") as handle:
            fields = handle.read().split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:  # noqa: BLE001
        return 0.0


class _Watchdog(threading.Thread):
    def __init__(self, cpu_seconds: float, memory_mib: float, wall_seconds: float) -> None:
        super().__init__(name="assistant-watchdog", daemon=True)
        self._cpu_limit = cpu_seconds
        self._memory_limit = memory_mib
        self._wall_limit = wall_seconds
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        started_cpu = time.process_time()
        started_wall = time.monotonic()
        while not self._stop.is_set():
            if self._cpu_limit > 0 and (time.process_time() - started_cpu) > self._cpu_limit:
                os._exit(EXIT_CPU)
            rss = _rss_mib()
            if self._memory_limit > 0 and rss > self._memory_limit:
                os._exit(EXIT_MEMORY)
            if self._wall_limit > 0 and (time.monotonic() - started_wall) > self._wall_limit:
                os._exit(EXIT_WALL)
            time.sleep(WATCHDOG_INTERVAL_SECONDS)


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _issue(
    code: str, message: str, path: str | None = None, node_ids: list[str] | None = None
) -> dict[str, Any]:
    return {
        "code": code[:80],
        "message": message[:500],
        "path": path[:200] if path else None,
        "nodeIds": list(node_ids or [])[:20],
    }


def _pydantic_issues(exc: ValidationError, prefix: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for err in exc.errors()[:20]:
        loc = ".".join(str(part) for part in err.get("loc", ()))
        path = f"{prefix}.{loc}" if prefix and loc else (loc or prefix or None)
        items.append(
            _issue(
                "ASSISTANT_SCHEMA_INVALID",
                f"字段校验失败：{err.get('msg', '不合法')}",
                path=path,
            )
        )
    return items


def _collect_subtree(nodes: list[Any], root_id: str) -> set[str]:
    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.parentId is not None:
            children[node.parentId].append(node.id)
    seen: set[str] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children.get(current, ()))
    return seen


def _validate_ir_fragment(payload: dict[str, Any]) -> dict[str, Any]:
    from app.engine.sample import sample_scene
    from app.engine.validate import MAX_NODE_DEPTH, MAX_TOTAL_VERTICES, validate_ir
    from app.schemas.assistant import IRFragmentProposal
    from app.schemas.ir import DrawingIR

    base_ir = DrawingIR.model_validate(payload["baseIr"])
    expected_root = str(payload["rootNodeId"])
    policy = payload.get("policy") or {}
    max_nodes = int(policy.get("maxIRNodes", 200))
    max_vertices = int(policy.get("maxVertices", MAX_TOTAL_VERTICES))

    try:
        proposal = IRFragmentProposal.model_validate(payload["proposal"])
    except ValidationError as exc:
        return {"protocol": PROTOCOL, "ok": False, "issues": _pydantic_issues(exc)}

    issues: list[dict[str, Any]] = []
    if proposal.rootNodeId != expected_root:
        issues.append(
            _issue(
                "ASSISTANT_ROOT_MISMATCH",
                f"提案的 rootNodeId「{proposal.rootNodeId}」必须等于请求指定的「{expected_root}」",
                path="rootNodeId",
                node_ids=[expected_root],
            )
        )

    by_id = {node.id: node for node in base_ir.nodes}
    if expected_root not in by_id:
        issues.append(
            _issue(
                "IR_MISSING_NODE",
                f"基底 IR 中不存在目标节点「{expected_root}」",
                path="rootNodeId",
                node_ids=[expected_root],
            )
        )
        return {"protocol": PROTOCOL, "ok": False, "issues": issues}

    subtree = _collect_subtree(list(base_ir.nodes), expected_root)
    replacement_ids = [node.id for node in proposal.replacementNodes]
    seen_ids: set[str] = set()
    duplicated: list[str] = []
    for nid in replacement_ids:
        if nid in seen_ids and nid not in duplicated:
            duplicated.append(nid)
        seen_ids.add(nid)
    if duplicated:
        issues.append(
            _issue(
                "IR_DUPLICATE_NODE_ID",
                f"replacementNodes 内存在重复节点 ID：{', '.join(duplicated[:5])}",
                path="replacementNodes",
                node_ids=duplicated[:5],
            )
        )

    outside = set(by_id) - subtree
    conflict = sorted(set(replacement_ids) & outside)
    if conflict:
        issues.append(
            _issue(
                "ASSISTANT_ID_CONFLICT",
                f"新节点 ID 与子树外既有节点冲突：{', '.join(conflict[:5])}，请更换新 ID",
                path="replacementNodes",
                node_ids=conflict[:5],
            )
        )

    kept = [node for node in base_ir.nodes if node.id not in subtree]
    merged_nodes = kept + list(proposal.replacementNodes)
    if len(merged_nodes) > max_nodes:
        issues.append(
            _issue(
                "IR_TOO_MANY_NODES",
                f"替换后节点总数 {len(merged_nodes)} 超过上限 {max_nodes}",
                path="replacementNodes",
            )
        )

    if issues:
        return {"protocol": PROTOCOL, "ok": False, "issues": issues}

    try:
        candidate_ir = DrawingIR.model_validate(
            {
                "schemaVersion": base_ir.schemaVersion,
                "seed": base_ir.seed,
                "canvas": base_ir.canvas.model_dump(),
                "viewport": base_ir.viewport.model_dump(),
                "durationMs": base_ir.durationMs,
                "nodes": [node.model_dump() for node in merged_nodes],
                "animations": [
                    anim.model_dump(by_alias=True)
                    for anim in base_ir.animations
                    if anim.nodeId not in subtree
                ],
                "narrative": [item.model_dump() for item in base_ir.narrative],
            }
        )
    except ValidationError as exc:
        return {"protocol": PROTOCOL, "ok": False, "issues": _pydantic_issues(exc, "ir")}

    report = validate_ir(candidate_ir)
    replacement_id_set = set(replacement_ids)
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for item in report.issues:
        dumped = item.model_dump(mode="json")
        if item.code == "IR_OUT_OF_BOUNDS":
            bad_new = [nid for nid in item.nodeIds if nid in replacement_id_set]
            if bad_new:
                blocking.append(
                    _issue(
                        "IR_OUT_OF_BOUNDS",
                        f"新增/替换节点 {', '.join(bad_new[:5])} 超出 800×600 画布范围，请调整坐标与半径",
                        path="replacementNodes",
                        node_ids=bad_new[:10],
                    )
                )
            else:
                warnings.append(dumped)
        else:
            blocking.append(dumped)

    if blocking:
        return {"protocol": PROTOCOL, "ok": False, "issues": blocking}

    # 额外复核顶点上限（遵循当前策略）
    total_vertices = sum(
        len(n.geometry.points)
        for n in candidate_ir.nodes
        if n.type in ("polygon", "polyline")
    )
    if total_vertices > max_vertices:
        return {
            "protocol": PROTOCOL,
            "ok": False,
            "issues": [
                _issue(
                    "IR_TOO_MANY_VERTICES",
                    f"替换后顶点总数 {total_vertices} 超过上限 {max_vertices}",
                    path="replacementNodes",
                )
            ],
        }

    # 隔离环境真采样校验：确保可被多后端采样器稳定执行
    try:
        sample_scene(candidate_ir, 0)
    except Exception as exc:  # noqa: BLE001
        return {
            "protocol": PROTOCOL,
            "ok": False,
            "issues": [
                _issue(
                    "ASSISTANT_SAMPLE_FAILED",
                    f"候选 IR 采样校验失败（{type(exc).__name__}）",
                    path="replacementNodes",
                )
            ],
        }

    return {
        "protocol": PROTOCOL,
        "ok": True,
        "normalizedProposal": proposal.model_dump(mode="json"),
        "candidateIr": candidate_ir.model_dump(mode="json"),
        "issues": warnings,
        "isolatedValidationPassed": True,
    }


def _validate_parameter_schema(payload: dict[str, Any]) -> dict[str, Any]:
    from app.engine.instantiate import (
        TemplateDefinitionError,
        check_template_integrity,
        instantiate,
    )
    from app.engine.sample import sample_scene
    from app.engine.validate import MAX_TOTAL_VERTICES, validate_ir
    from app.schemas.assistant import ParameterSchemaProposal
    from app.schemas.generation import IntegerParameter, NumberParameter
    from app.schemas.ir import DrawingIR

    template_ctx = payload.get("templateContext") or {}
    template_ir = DrawingIR.model_validate(template_ctx["templateIr"])
    policy = payload.get("policy") or {}
    max_params = int(policy.get("maxParameters", 16))
    max_nodes = int(policy.get("maxIRNodes", 200))
    max_vertices = int(policy.get("maxVertices", MAX_TOTAL_VERTICES))

    try:
        proposal = ParameterSchemaProposal.model_validate(payload["proposal"])
    except ValidationError as exc:
        return {"protocol": PROTOCOL, "ok": False, "issues": _pydantic_issues(exc)}

    issues: list[dict[str, Any]] = []
    if len(proposal.parameterSchema) > max_params:
        issues.append(
            _issue(
                "ASSISTANT_TOO_MANY_PARAMS",
                f"参数数量 {len(proposal.parameterSchema)} 超过上限 {max_params}",
                path="parameterSchema",
            )
        )

    names = [p.name for p in proposal.parameterSchema]
    if len(set(names)) != len(names):
        issues.append(
            _issue(
                "ASSISTANT_DUPLICATE_PARAM",
                "parameterSchema 中存在重复参数名",
                path="parameterSchema",
            )
        )

    known_names = set(names)
    unknown_defaults = [k for k in proposal.defaults if k not in known_names]
    if unknown_defaults:
        issues.append(
            _issue(
                "ASSISTANT_UNKNOWN_DEFAULT",
                f"defaults 包含未在 parameterSchema 定义的参数：{', '.join(unknown_defaults[:5])}",
                path="defaults",
            )
        )

    if issues:
        return {"protocol": PROTOCOL, "ok": False, "issues": issues}

    # 补齐 defaults 以便前端与实例化保持完整一致
    normalized_defaults = {p.name: p.default for p in proposal.parameterSchema}
    normalized_defaults.update(proposal.defaults)

    try:
        check_template_integrity(
            template_ir,
            proposal.parameterSchema,
            proposal.bindings,
            [],
        )
        outcome = instantiate(
            template_ir,
            proposal.parameterSchema,
            proposal.bindings,
            [],
            normalized_defaults,
        )
    except TemplateDefinitionError as exc:
        return {
            "protocol": PROTOCOL,
            "ok": False,
            "issues": [_issue("ASSISTANT_BINDING_INVALID", str(exc), path="bindings")],
        }

    if not outcome.ok:
        return {
            "protocol": PROTOCOL,
            "ok": False,
            "issues": [
                _issue("ASSISTANT_PARAM_INVALID", item.message, path=item.path)
                for item in outcome.issues
            ],
        }

    # 验证数字参数的 min/max 边界写入 IR 也不会违反 DrawingIR 字段约束（如 opacity<=1, strokeWidth<=20）
    min_params = dict(outcome.params)
    max_params_vals = dict(outcome.params)
    for param in proposal.parameterSchema:
        if isinstance(param, (IntegerParameter, NumberParameter)):
            min_params[param.name] = param.min
            max_params_vals[param.name] = param.max
    for label, bound_vals in (("min", min_params), ("max", max_params_vals)):
        try:
            bound_outcome = instantiate(
                template_ir,
                proposal.parameterSchema,
                proposal.bindings,
                [],
                bound_vals,
            )
            if not bound_outcome.ok:
                return {
                    "protocol": PROTOCOL,
                    "ok": False,
                    "issues": [
                        _issue(
                            "ASSISTANT_PARAM_RANGE_INVALID",
                            f"参数 {label} 边界值实例化失败：{bound_outcome.issues[0].message}",
                            path=bound_outcome.issues[0].path,
                        )
                    ],
                }
        except TemplateDefinitionError as exc:
            return {
                "protocol": PROTOCOL,
                "ok": False,
                "issues": [
                    _issue(
                        "ASSISTANT_PARAM_RANGE_INVALID",
                        f"参数 {label} 边界值超出目标 IR 属性允许范围：{exc}",
                        path="parameterSchema",
                    )
                ],
            }

    candidate_ir = outcome.ir
    if len(candidate_ir.nodes) > max_nodes:
        return {
            "protocol": PROTOCOL,
            "ok": False,
            "issues": [
                _issue(
                    "IR_TOO_MANY_NODES",
                    f"实例化后节点数 {len(candidate_ir.nodes)} 超过上限 {max_nodes}",
                    path="parameterSchema",
                )
            ],
        }

    report = validate_ir(candidate_ir)
    bound_node_ids = {b.nodeId for b in proposal.bindings}
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for item in report.issues:
        dumped = item.model_dump(mode="json")
        if item.code == "IR_OUT_OF_BOUNDS":
            bad_bound = [nid for nid in item.nodeIds if nid in bound_node_ids]
            if bad_bound:
                blocking.append(
                    _issue(
                        "IR_OUT_OF_BOUNDS",
                        f"参数默认值导致绑定节点 {', '.join(bad_bound[:5])} 超出 800×600 画布范围",
                        path="defaults",
                        node_ids=bad_bound[:10],
                    )
                )
            else:
                warnings.append(dumped)
        else:
            blocking.append(dumped)

    if blocking:
        return {"protocol": PROTOCOL, "ok": False, "issues": blocking}

    total_vertices = sum(
        len(n.geometry.points)
        for n in candidate_ir.nodes
        if n.type in ("polygon", "polyline")
    )
    if total_vertices > max_vertices:
        return {
            "protocol": PROTOCOL,
            "ok": False,
            "issues": [
                _issue(
                    "IR_TOO_MANY_VERTICES",
                    f"实例化后顶点总数 {total_vertices} 超过上限 {max_vertices}",
                    path="parameterSchema",
                )
            ],
        }

    try:
        sample_scene(candidate_ir, 0)
    except Exception as exc:  # noqa: BLE001
        return {
            "protocol": PROTOCOL,
            "ok": False,
            "issues": [
                _issue(
                    "ASSISTANT_SAMPLE_FAILED",
                    f"实例化 IR 采样校验失败（{type(exc).__name__}）",
                    path="defaults",
                )
            ],
        }

    normalized_proposal = {
        "parameterSchema": [p.model_dump(mode="json") for p in proposal.parameterSchema],
        "bindings": [b.model_dump(mode="json") for b in proposal.bindings],
        "defaults": dict(outcome.params),
    }
    return {
        "protocol": PROTOCOL,
        "ok": True,
        "normalizedProposal": normalized_proposal,
        "candidateIr": candidate_ir.model_dump(mode="json"),
        "resolvedParams": dict(outcome.params),
        "issues": warnings,
        "isolatedValidationPassed": True,
    }


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("输入超过 1MiB 上限")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
            raise ValueError("协议版本不匹配")
    except Exception as exc:  # noqa: BLE001
        print(f"assistant_worker: 输入协议错误：{exc}", file=sys.stderr)
        return EXIT_PROTOCOL

    policy = payload.get("policy") or {}
    watchdog = _Watchdog(
        cpu_seconds=float(policy.get("cpuMs", 2000)) / 1000.0,
        memory_mib=float(policy.get("memoryMiB", 256)),
        wall_seconds=float(policy.get("wallMs", 4000)) / 1000.0,
    )
    watchdog.start()

    try:
        target = payload.get("target")
        if target == "ir_fragment":
            result = _validate_ir_fragment(payload)
        elif target == "parameter_schema":
            result = _validate_parameter_schema(payload)
        else:
            result = {
                "protocol": PROTOCOL,
                "ok": False,
                "issues": [_issue("ASSISTANT_INVALID_TARGET", f"未知目标类型：{target}")],
            }
    except Exception as exc:  # noqa: BLE001
        watchdog.stop()
        _emit(
            {
                "protocol": PROTOCOL,
                "ok": False,
                "issues": [
                    _issue(
                        "ASSISTANT_ISOLATED_ERROR",
                        f"隔离校验进程内部异常（{type(exc).__name__}）",
                    )
                ],
            }
        )
        return EXIT_OK

    watchdog.stop()
    _emit(result)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

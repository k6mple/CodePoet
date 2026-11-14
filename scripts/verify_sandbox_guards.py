"""反向验证：把每条防护退化成「看起来也能跑」的写法，测试必须精确失败。

方法论：一个测试如果无论实现对不对都通过，它就没有测任何东西。
所以这里逐条把防护摘掉，跑对应的那一条，必须 FAIL；恢复后必须 PASS。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
SANDBOX = BACKEND / "app" / "services" / "sandbox.py"
POLICY = BACKEND / "app" / "services" / "policy.py"
WORKER = BACKEND / "app" / "engine" / "dsl_worker.py"
DSL = BACKEND / "app" / "engine" / "dsl.py"

CASES = [
    (
        "AST 白名单按策略再过一遍（worker）",
        WORKER,
        '    allowed = set(enabled)',
        '    return  # 退化：不过策略\n    allowed = set(enabled)',
        "tests.test_sandbox.ExecuteTests.test_disabled_function_is_rejected",
    ),
    (
        "base 必须是本人拥有（不是「可读」）",
        SANDBOX,
        "    if base is None or base.owner_id != user.id:",
        "    if base is None:",  # 退化：只判存在
        "tests.test_sandbox.OwnershipTests.test_base_must_be_owned_not_merely_readable",
    ),
    (
        "策略硬上限",
        POLICY,
        "    over: list[str] = []\n    hard = HARD_LIMITS.model_dump(mode=\"json\")",
        "    return []  # 退化：不判硬限\n    hard = HARD_LIMITS.model_dump(mode=\"json\")",
        "tests.test_policy.WriteTests.test_over_hard_limit_is_rejected",
    ),
    (
        "只读字段拒绝",
        POLICY,
        "    read_only_touched = sorted(changed & READ_ONLY_FIELDS)",
        "    read_only_touched: list[str] = []  # 退化：不拦只读",
        "tests.test_policy.WriteTests.test_read_only_field_is_rejected",
    ),
    (
        "并发配额（独占写事务）",
        SANDBOX,
        "    locking.begin_immediate(db)\n    try:\n        active = _active_count(db, user.id)",
        "    try:  # 退化：不开独占写事务\n        active = _active_count(db, user.id)",
        "tests.test_sandbox.ConcurrencyTests.test_create_run_opens_exclusive_transaction",
    ),
    (
        "策略乐观锁（条件进 WHERE）",
        POLICY,
        "        .where(\n            PolicyModel.id == 1,\n            PolicyModel.revision == payload.expectedRevision,\n        )",
        "        .where(PolicyModel.id == 1)  # 退化：只按主键更新",
        "tests.test_sandbox.ConcurrencyTests.test_policy_patch_rejects_stale_identity_map",
    ),
    (
        "run 绑定提交时快照",
        SANDBOX,
        '        "limits": {\n            "maxDrawCalls": snapshot["dslMaxDrawCalls"],',
        '        "limits": {\n            "maxDrawCalls": policy_service.snapshot(db)[1].dslMaxDrawCalls,',
        "tests.test_sandbox.SnapshotBindingTests."
        "test_run_keeps_policy_snapshot_taken_at_submission",
    ),
    (
        "源码不出现在 provenance",
        SANDBOX,
        "        fragmentSource=None,",
        "        fragmentSource=row.source_json,  # 退化：把源码写进 provenance",
        "tests.test_sandbox.OwnershipTests."
        "test_source_never_leaks_through_generation_provenance",
    ),
    (
        "内存退出码归入内存超限",
        SANDBOX,
        "def is_memory_exit(returncode: int) -> bool:\n"
        "    return returncode == 71 or (returncode & 0xFFFFFFFF) == _NT_STATUS_NO_MEMORY",
        "def is_memory_exit(returncode: int) -> bool:\n"
        "    return False  # 退化：不认任何内存退出码",
        "tests.test_sandbox.BudgetTests.test_memory_watchdog_kills_worker",
    ),
    (
        "parameter issue 不再读 .code（P1）",
        DSL,
        "                    {\"path\": issue.path, \"message\": issue.message}",
        "                    {\"code\": issue.code, \"message\": issue.message, \"path\": issue.path}",
        "tests.test_dsl.SetParamTests.test_unknown_param_is_invalid_base",
    ),
    (
        "finding 用规则 ID 而不是错误码",
        WORKER,
        '                    "ruleId": (rule or code)[:80],',
        '                    "ruleId": code[:80],  # 退化：拿错误码当规则 ID',
        "tests.test_sandbox.ExecuteTests.test_static_rejection_still_leaves_a_run",
    ),
]



def run(test_id: str) -> bool:
    result = subprocess.run(
        [str(PY), "-m", "unittest", test_id],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=300,
    )
    return result.returncode == 0


def main() -> int:
    failures: list[str] = []
    for label, path, original, broken, test_id in CASES:
        text = path.read_text(encoding="utf-8")
        if original not in text:
            print(f"[SKIP] {label}：找不到锚点，说明代码已经变了，需人工复核")
            failures.append(label)
            continue
        path.write_text(text.replace(original, broken, 1), encoding="utf-8", newline="\n")
        try:
            degraded_pass = run(test_id)
        finally:
            path.write_text(text, encoding="utf-8", newline="\n")
        restored_pass = run(test_id)

        verdict = "OK" if (not degraded_pass and restored_pass) else "PROBLEM"
        if verdict != "OK":
            failures.append(label)
        print(
            f"[{verdict}] {label}: 退化后{'通过(说明测试无效)' if degraded_pass else '失败(符合预期)'}，"
            f"恢复后{'通过' if restored_pass else '仍失败(需排查)'}"
        )

    print()
    if failures:
        print("有问题的项：" + "、".join(failures))
        return 1
    print(f"全部 {len(CASES)} 条反向验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

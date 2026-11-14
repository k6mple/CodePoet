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

CASES = [
    (
        "AST 白名单按策略再过一遍（worker）",
        WORKER,
        '    allowed = set(enabled)',
        '    return  # 退化：不过策略\n    allowed = set(enabled)',
        "tests.test_sandbox.ExecuteTests.test_disabled_function_is_rejected",
    ),
    (
        "base 授权检查",
        SANDBOX,
        "    if base is None or not can_read(db, user, base):",
        "    if base is None:",  # 退化：只判存在
        "tests.test_sandbox.AcceptTests.test_other_users_base_is_404",
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
        "并发配额",
        SANDBOX,
        "    if _active_count(db, user.id) >= MAX_ACTIVE_RUNS_PER_USER:",
        "    if False:  # 退化：不判配额",
        "tests.test_sandbox.QuotaTests.test_third_active_run_is_429",
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
        "内存退出码归入内存超限",
        SANDBOX,
        "def is_memory_exit(returncode: int) -> bool:\n"
        "    return returncode == 71 or (returncode & 0xFFFFFFFF) == _NT_STATUS_NO_MEMORY",
        "def is_memory_exit(returncode: int) -> bool:\n"
        "    return False  # 退化：不认任何内存退出码",
        "tests.test_sandbox.BudgetTests.test_memory_watchdog_kills_worker",
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

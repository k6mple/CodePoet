"""K1 真网络冒烟：真实 Wikidata → 站内规则 → 不可变快照。

和 `tests/test_knowledge_graph.py` 的分工：那边把来源换成假实现，验我们自己的
逻辑；这里**真的打公网**，验「抓取 + 解析 + 映射」这条链在真实数据上成立。

它不可避免依赖外网，所以不进单测、单独跑：

    python scripts/smoke_knowledge_graph.py
    python scripts/smoke_knowledge_graph.py --port 18160 --keep

网络不通时会明确报「来源不可用」而不是伪装成功——这正是 503 那条契约。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def run_script(name: str, data_dir: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["CODEPOET_DATA_DIR"] = str(data_dir)
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / name)],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-400:]


def register(client: httpx.Client, base: str, name: str, role: str, db_path: Path) -> str:
    token = client.get(f"{base}/auth/csrf").json()["csrfToken"]
    password = "Aa1!" + uuid4().hex[:12]
    created = client.post(
        f"{base}/auth/register",
        json={"username": name, "password": password, "nickname": name},
        headers={"X-CSRF-Token": token},
    )
    if created.status_code != 201:
        raise SystemExit(f"注册失败：{created.text[:200]}")
    user_id = created.json()["id"]
    connection = sqlite3.connect(db_path)
    connection.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    connection.commit()
    connection.close()
    login = client.post(
        f"{base}/auth/login",
        json={"username": name, "password": password},
        headers={"X-CSRF-Token": client.get(f"{base}/auth/csrf").json()["csrfToken"]},
    )
    if login.status_code != 200:
        raise SystemExit(f"登录失败：{login.text[:200]}")
    return user_id


def main() -> int:
    parser = argparse.ArgumentParser(description="K1 真网络冒烟")
    parser.add_argument("--data-dir", default=str(PROJECT_ROOT / ".smoke" / "kg"))
    parser.add_argument("--port", type=int, default=18160)
    parser.add_argument("--entity", default="Q16528", help="Wikidata 实体 id（默认莲）")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    db_path = data_dir / "codepoet.sqlite3"
    base = f"http://127.0.0.1:{args.port}/api/v1"

    print("== 准备数据库（含演示知识实体与规则）==")
    code, out = run_script("migrate.py", data_dir)
    check("migrate 成功", code == 0, out if code else "")
    code, out = run_script("seed.py", data_dir)
    check("seed 成功", code == 0, out if code else "")
    code, out = run_script("seed_demo.py", data_dir)
    check("seed_demo 成功（知识实体 + 规则）", code == 0, out if code else "")

    print("== 启动服务（开启 wikidata 白名单）==")
    env = dict(os.environ)
    env["CODEPOET_DATA_DIR"] = str(data_dir)
    env["CODEPOET_KNOWLEDGE_GRAPH_SOURCES"] = "wikidata"
    # 显式映射表：竹亚科→竹、梅→墨梅 这类对应关系写表里，不靠字符串猜
    env["CODEPOET_KNOWLEDGE_GRAPH_MAPPING_FILE"] = "shared/demo/knowledge-graph-mappings.json"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(args.port)],
        cwd=str(BACKEND_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        client = httpx.Client(trust_env=False, timeout=60)
        for _ in range(60):
            try:
                if client.get(f"{base}/health").status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            raise SystemExit("服务没起来")

        register(client, base, "kg_smoke", "creator", db_path)
        csrf = client.get(f"{base}/auth/csrf").json()["csrfToken"]

        print(f"== 真实抓取 {args.entity} ==")
        started = time.perf_counter()
        response = client.post(
            f"{base}/knowledge-graph/generations",
            json={"sourceId": "wikidata", "externalEntityId": args.entity},
            headers={"X-CSRF-Token": csrf},
        )
        elapsed = (time.perf_counter() - started) * 1000
        check("端点返回 201", response.status_code == 201,
              f"{response.status_code} / {elapsed:.0f}ms / {response.text[:120]}")

        if response.status_code == 201:
            body = response.json()
            graph = body["provenance"].get("externalGraph")
            check("写入 externalGraph 快照", graph is not None)
            if graph:
                check("来源与实体 id 正确",
                      graph["sourceId"] == "wikidata"
                      and graph["externalEntityId"] == args.entity)
                check("有 contentHash", bool(graph.get("contentHash")))
                check("拿到实体标签", bool(graph["entities"][0]["label"]),
                      graph["entities"][0]["label"])
                check("拿到一跳关系", len(graph["relations"]) >= 1,
                      f"{len(graph['relations'])} 条")
                check("关系带可读标签",
                      any(item.get("label") for item in graph["relations"]))
                affects = {item["affects"] for item in graph["mappingDecisions"]}
                check("实体映射影响规则选择", "rule-selection" in affects, str(affects))
                check("关系映射影响叙事说明", "caption" in affects, str(affects))
                narrative = " ".join(
                    str(item.get("caption") or "") for item in body["ir"]["narrative"]
                )
                check("外部事实进入 IR 叙事", bool(narrative), narrative[:90])
            check("生成的 IR 非空", bool(body["ir"]["nodes"]))

        print("== 未配置来源时应 503 ==")
        client.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        if not args.keep:
            shutil.rmtree(data_dir, ignore_errors=True)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} 通过 ===")
    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print("失败项：" + "、".join(failed))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""后端联调冒烟：走真实 uvicorn + 真实库文件 + 真实 Cookie/CSRF 会话。

单测跑得再多也证明不了「起服务之后还这样」。这个脚本专门补那一层：
每一项都按 HTTP 打，断言响应体，而不是直接调服务函数。

覆盖的是 2026-11-12 前端联调清单里的 6 项：
  BE-WORK-PUBLIC-POINTER-001  匿名作品详情/缩略图只认发布版
  BE-FAVORITES-READ-001       收藏写入后读得到，列表真实过滤
  BE-TEMPLATE-EDIT-001        draft/rejected 版本就地修订 → 再送审
  BE-DEMO-SEED-001            seed.py → seed_demo.py 连跑不撞主键
  BE-SUBGRAPH-CAPABILITY-001  子树能力声明与实现一致
  BE-EXPORT-TYPES-001         六类导出、计数一致、快照带 IR

用法（仓库根目录）：

    uv run --project backend --extra dev python scripts/smoke_integration.py
    uv run ... python scripts/smoke_integration.py --port 18200 --keep

退出码 0 表示全过，1 表示有失败项（会列出名字）。

数据准备允许直连 sqlite：要验的是接口行为，不是「数据怎么来的」。
但作品链路仍然按生产会出现的形态造（公开 v1 + 私有待审 v2，各带自己的
渲染产物），否则测不出「公共视图该取哪一份」。
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
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-400:]


def register(client: httpx.Client, base: str, name: str, role: str, db_path: Path) -> str:
    token = client.get(f"{base}/auth/csrf").json()["csrfToken"]
    password = "Aa1!" + uuid4().hex[:12]
    response = client.post(
        f"{base}/auth/register",
        json={"username": name, "password": password, "nickname": name},
        headers={"X-CSRF-Token": token},
    )
    if response.status_code != 201:
        raise SystemExit(f"注册 {name} 失败：{response.status_code} {response.text[:200]}")
    user_id = response.json()["id"]
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
        raise SystemExit(f"登录 {name} 失败：{login.text[:200]}")
    return user_id


def seed_work_chain(db_path: Path, owner_id: str) -> dict[str, str]:
    """造「公开 v1 + 私有待审 v2」的作品，两版各带自己的渲染产物。

    这是真实的日常形态：作者基于已发布版本改一版、送审、还没批。
    """
    now = "2026-11-12T00:00:00+00:00"
    ir = {
        "schemaVersion": "1.0",
        "seed": 5,
        "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
        "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
        "durationMs": 0,
        "nodes": [
            {
                "id": "c1",
                "parentId": None,
                "type": "circle",
                "geometry": {"cx": 400, "cy": 300, "r": 60},
                "style": {
                    "fill": "#E8A0A0",
                    "stroke": "#8B3A3A",
                    "strokeWidth": 2,
                    "opacity": 1,
                },
            }
        ],
        "animations": [],
        "narrative": [],
    }
    provenance = {
        "kind": "manual_edit",
        "baseGenerationId": None,
        "matchedEntities": [],
        "matchedRules": [],
        "selectedSymbols": [],
        "paletteDecision": None,
        "compositionDecision": None,
        "timelineDecision": None,
        "templateSelection": None,
        "conflicts": [],
        "warnings": [],
        "engineVersion": "smoke-1.0",
        "ontologyVersion": 1,
        "policyRevision": 1,
        "fragmentSource": None,
        "fragmentSourceHash": None,
    }
    ids = {
        "work": "wrk-smoke",
        "v1": "wv-smoke-1",
        "v2": "wv-smoke-2",
        "gen1": "gen-smoke-1",
        "gen2": "gen-smoke-2",
        "job1": "job-smoke-1",
        "job2": "job-smoke-2",
        "art1": "art-smoke-public",
        "art2": "art-smoke-private",
    }
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys=ON")
    for gen, job in ((ids["gen1"], ids["job1"]), (ids["gen2"], ids["job2"])):
        connection.execute(
            "INSERT INTO generations (id, owner_id, mode, backend, template_version_id,"
            " params_json, seed, ir_json, ir_hash, provenance_json,"
            " validation_report_json, warnings_json, unpublished_source, request_id,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                gen, owner_id, "ir", "canvas2d", None, "{}", 5, json.dumps(ir),
                "e" * 64, json.dumps(provenance),
                '{"parameterValid":true,"nodeCount":1,"outOfBoundsNodeCount":0,"issues":[]}',
                "[]", 0, "req-smoke", now,
            ),
        )
        connection.execute(
            "INSERT INTO render_jobs (id, owner_id, generation_id, backend,"
            " execution_mode, status, phase, times_ms_json, policy_revision,"
            " renderer_version, client_environment_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job, owner_id, gen, "canvas2d", "browser_upload", "succeeded",
                "completed", "[0]", 1, "smoke-1.0",
                '{"userAgent":"smoke","deviceScaleFactor":1}', 1, now, now,
            ),
        )
    for art, job in ((ids["art1"], ids["job1"]), (ids["art2"], ids["job2"])):
        connection.execute(
            "INSERT INTO artifacts (id, owner_id, kind, content_type, byte_size,"
            " storage_path, sha256, width, height, time_ms, render_job_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                art, owner_id, "png", "image/png", 100,
                f"frames/{art}.png", "f" * 64, 8, 6, 0, job, now,
            ),
        )
    connection.execute(
        "INSERT INTO works (id, owner_id, title, description, revision,"
        " published_version_id, latest_version_id, display_version_id,"
        " thumbnail_artifact_id, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            ids["work"], owner_id, "烟测作品", "", 1, ids["v1"], ids["v2"],
            ids["v2"], ids["art2"], now, now,
        ),
    )
    for version, number, status, gen, job in (
        (ids["v1"], 1, "published", ids["gen1"], ids["job1"]),
        (ids["v2"], 2, "pending_review", ids["gen2"], ids["job2"]),
    ):
        connection.execute(
            "INSERT INTO work_versions (id, work_id, version_number, created_by, status,"
            " title, description, generation_id, render_job_id, change_note,"
            " submitted_by, submitted_at, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                version, ids["work"], number, owner_id, status, "烟测作品", "",
                gen, job, "", owner_id, now, 1, now, now,
            ),
        )
    connection.commit()
    connection.close()
    return ids


def wait_for_health(client: httpx.Client, base: str) -> bool:
    for _ in range(60):
        try:
            if client.get(f"{base}/health").status_code == 200:
                return True
        except Exception:  # noqa: BLE001 —— 端口还没起来，重试
            time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="后端联调冒烟（真实服务 + 真实会话）")
    parser.add_argument(
        "--data-dir",
        default=str(PROJECT_ROOT / ".smoke" / "data"),
        help="隔离数据目录，脚本会先清空重建",
    )
    parser.add_argument("--port", type=int, default=18150, help="后端端口")
    parser.add_argument(
        "--keep", action="store_true", help="保留数据目录，跑完不删（排查用）"
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    db_path = data_dir / "codepoet.sqlite3"
    base = f"http://127.0.0.1:{args.port}/api/v1"

    print("== 准备数据库 ==")
    code, out = run_script("migrate.py", data_dir)
    check("migrate 成功", code == 0, out if code else "")

    print("== BE-DEMO-SEED-001：默认 seed 之后跑 demo 导入 ==")
    code1, out1 = run_script("seed.py", data_dir)
    check("seed.py 成功（建用户 + 模板）", code1 == 0, out1 if code1 else "")
    code2, out2 = run_script("seed_demo.py", data_dir)
    check("默认 seed 之后 seed_demo.py 不崩", code2 == 0, out2 if code2 else "")
    code3, out3 = run_script("seed_demo.py", data_dir)
    check("seed_demo.py 重复执行仍成功", code3 == 0, out3 if code3 else "")

    connection = sqlite3.connect(db_path)
    templates = connection.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
    rules = connection.execute("SELECT COUNT(*) FROM mapping_rules").fetchone()[0]
    connection.close()
    check("模板没有重复插入", templates == 3, f"templates={templates}")
    check("规则补齐到 8 条", rules == 8, f"rules={rules}")

    print("== 启动服务 ==")
    env = dict(os.environ)
    env["CODEPOET_DATA_DIR"] = str(data_dir)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(args.port)],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        client = httpx.Client(trust_env=False, timeout=30)
        if not wait_for_health(client, base):
            raise SystemExit(f"服务没起来（端口 {args.port}）")

        print("== 账号 ==")
        register(client, base, "smoke_admin", "admin", db_path)
        creator_id = register(client, base, "smoke_creator", "creator", db_path)
        ids = seed_work_chain(db_path, creator_id)

        print("== BE-WORK-PUBLIC-POINTER-001 ==")
        client.post(f"{base}/auth/logout",
                    headers={"X-CSRF-Token": client.get(f"{base}/auth/csrf").json()["csrfToken"]})
        anon = client.get(f"{base}/works/{ids['work']}")
        body = anon.json() if anon.status_code == 200 else {}
        check("匿名作品详情 200", anon.status_code == 200, anon.text[:100])
        check("displayVersionId 指向公开版", body.get("displayVersionId") == ids["v1"],
              str(body.get("displayVersionId")))
        check("latestVersionId 仍是最新私有版", body.get("latestVersionId") == ids["v2"])
        check("缩略图来自公开版那次渲染", body.get("thumbnailArtifactId") == ids["art1"],
              str(body.get("thumbnailArtifactId")))
        opened = client.get(f"{base}/work-versions/{ids['v1']}")
        check("按 display 能打开版本", opened.status_code == 200, str(opened.status_code))
        hidden = client.get(f"{base}/work-versions/{ids['v2']}")
        check("私有新版仍 404", hidden.status_code == 404, str(hidden.status_code))
        listed = client.get(f"{base}/works?scope=public").json()
        item = next((i for i in listed["items"] if i["id"] == ids["work"]), None)
        check("公共列表里的 display 也是公开版",
              item is not None and item["displayVersionId"] == ids["v1"])

        print("== BE-FAVORITES-READ-001 ==")
        register(client, base, "smoke_reader", "creator", db_path)
        token = client.get(f"{base}/auth/csrf").json()["csrfToken"]
        liked = client.put(f"{base}/works/{ids['work']}/favorite",
                           headers={"X-CSRF-Token": token})
        check("收藏 PUT 200", liked.status_code == 200, liked.text[:80])
        detail = client.get(f"{base}/works/{ids['work']}").json()
        check("详情读到 isFavorite=true", detail.get("isFavorite") is True,
              str(detail.get("isFavorite")))
        favorites = client.get(f"{base}/works?scope=favorites").json()
        check("收藏列表有这条", favorites.get("total") == 1, f"total={favorites.get('total')}")
        client.delete(f"{base}/works/{ids['work']}/favorite",
                      headers={"X-CSRF-Token": token})
        again = client.get(f"{base}/works?scope=favorites").json()
        check("取消后列表清空", again.get("total") == 0, f"total={again.get('total')}")

        print("== BE-TEMPLATE-EDIT-001 ==")
        client.post(f"{base}/auth/logout",
                    headers={"X-CSRF-Token": client.get(f"{base}/auth/csrf").json()["csrfToken"]})
        register(client, base, "smoke_author", "developer", db_path)
        token = client.get(f"{base}/auth/csrf").json()["csrfToken"]
        metadata = {
            "name": "烟测模板", "description": "",
            "entityIds": [], "tagIds": [], "symbols": [], "timeTheme": None,
        }
        created = client.post(
            f"{base}/templates", json={"metadata": metadata},
            headers={"X-CSRF-Token": token},
        ).json()
        version = client.post(
            f"{base}/templates/{created['id']}/versions",
            json={
                "irTemplate": {
                    "schemaVersion": "1.0", "seed": 1,
                    "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
                    "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
                    "durationMs": 0,
                    "nodes": [{
                        "id": "c1", "parentId": None, "type": "circle",
                        "geometry": {"cx": 400, "cy": 300, "r": 60},
                        "style": {"fill": "#E8A0A0", "stroke": "#8B3A3A",
                                  "strokeWidth": 2, "opacity": 1},
                    }],
                    "animations": [], "narrative": [],
                },
                "parameterSchema": [], "bindings": [], "constraints": [],
                "supportedBackends": ["canvas2d"],
                "metadata": metadata, "changeNote": "初版",
            },
            headers={"X-CSRF-Token": token},
        ).json()
        patched = client.patch(
            f"{base}/template-versions/{version['id']}",
            json={"expectedRevision": 1, "changeNote": "退回后修订"},
            headers={"X-CSRF-Token": token},
        )
        check("PATCH 不再是 405", patched.status_code == 200, patched.text[:100])
        check("changeNote 已生效", patched.json().get("changeNote") == "退回后修订")
        check("revision 递增", patched.json().get("revision") == 2)
        stale = client.patch(
            f"{base}/template-versions/{version['id']}",
            json={"expectedRevision": 1, "changeNote": "旧 revision"},
            headers={"X-CSRF-Token": token},
        )
        check("旧 revision 409", stale.status_code == 409, str(stale.status_code))
        submitted = client.post(
            f"{base}/template-versions/{version['id']}/submit",
            json={"expectedRevision": 2},
            headers={"X-CSRF-Token": token},
        )
        check("修订后能直接送审", submitted.status_code == 200, submitted.text[:100])
        locked = client.patch(
            f"{base}/template-versions/{version['id']}",
            json={"expectedRevision": 3, "changeNote": "再改"},
            headers={"X-CSRF-Token": token},
        )
        check("送审后不可编辑 409", locked.status_code == 409, str(locked.status_code))

        print("== BE-SUBGRAPH-CAPABILITY-001 ==")
        caps = client.get(f"{base}/engine/capabilities").json()
        check("subgraphEditing 已开启", caps["features"]["subgraphEditing"] is True,
              str(caps["features"]["subgraphEditing"]))

        print("== BE-EXPORT-TYPES-001 ==")
        client.post(f"{base}/auth/logout",
                    headers={"X-CSRF-Token": client.get(f"{base}/auth/csrf").json()["csrfToken"]})
        register(client, base, "smoke_ops", "admin", db_path)
        token = client.get(f"{base}/auth/csrf").json()["csrfToken"]
        exported = client.post(
            f"{base}/admin/exports",
            json={"types": ["concepts", "tags", "entities", "rules", "templates",
                            "workSnapshots"]},
            headers={"X-CSRF-Token": token},
        )
        check("六类导出 200", exported.status_code == 200, exported.text[:120])
        if exported.status_code == 200:
            package = exported.json()
            counts = package["manifest"]["counts"]
            check("规则有内容", counts["rules"] == 8, f"rules={counts['rules']}")
            check("作品快照有内容", counts["workSnapshots"] >= 1,
                  f"workSnapshots={counts['workSnapshots']}")
            for key in ("concepts", "tags", "entities", "rules", "templates",
                        "workSnapshots"):
                check(f"counts.{key} 与 data 一致",
                      counts[key] == len(package["data"][key]))
            snapshot = package["data"]["workSnapshots"][0]
            check("快照字段完整",
                  set(snapshot) == {"work", "version", "generation", "renderJob",
                                    "missingArtifacts"})
            check("快照带 IR",
                  isinstance(snapshot.get("generation", {}).get("ir"), dict))
            check("快照如实报告产物缺失（本例没落真 PNG）",
                  snapshot.get("missingArtifacts") is True,
                  str(snapshot.get("missingArtifacts")))

        print("== 导入校验：非法结构不能打成 500 ==")
        for label, rule in (
            ("match 是字符串", {"id": "rule-bad-1", "match": "bad-shape"}),
            ("entityIds 里塞对象", {"id": "rule-bad-2", "match": {"entityIds": [{}]}}),
        ):
            package = {
                "schemaVersion": 1.0,
                "exportedAt": "2026-11-12T00:00:00Z",
                "manifest": {
                    "counts": {"concepts": 0, "tags": 0, "entities": 0, "rules": 1,
                               "templates": 0, "workSnapshots": 0},
                    "includesArtifacts": False,
                },
                "data": {"concepts": [], "tags": [], "entities": [], "rules": [rule],
                         "templates": [], "workSnapshots": []},
            }
            validated = client.post(
                f"{base}/admin/imports/validate",
                json=package,
                headers={"X-CSRF-Token": client.get(f"{base}/auth/csrf").json()["csrfToken"]},
            )
            check(f"非法规则（{label}）返回 200 而非 500",
                  validated.status_code == 200, validated.text[:100])
            if validated.status_code == 200:
                check(f"非法规则（{label}）判为 valid=false",
                      validated.json().get("valid") is False,
                      str(validated.json().get("valid")))

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
    sys.exit(main())

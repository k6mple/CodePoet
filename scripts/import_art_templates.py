"""通过官方 REST API 幂等导入并审核发布三套高精度国画艺术模板。

用法（仓库根目录，需后端已启动）：
    uv run --project backend python scripts/import_art_templates.py
    uv run --project backend python scripts/import_art_templates.py --base-url http://127.0.0.1:8000
    uv run --project backend python scripts/import_art_templates.py --force-new-version

工作流（严格遵守四角色权限与后端校验）：
  1. 以 developer_demo 登录创建模板容器与版本草稿，并提交审核（POST /templates、/versions、/submit）；
  2. 以 expert_demo 登录审核批准（POST /reviews），使模板版本正式发布并对全部用户可见；
  3. 默认幂等：若公开列表中已存在同名已发布模板则跳过（传 --force-new-version 则为其追加新版本并发布）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ART_DIR = PROJECT_ROOT / "shared" / "demo" / "templates" / "art"
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from seed import DEMO_PASSWORD  # noqa: E402


def login_client(base_url: str, username: str) -> tuple[httpx.Client, str]:
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=20.0, trust_env=False)
    csrf_resp = client.get("/api/v1/auth/csrf")
    if csrf_resp.status_code != 200:
        client.close()
        raise SystemExit(f"获取 CSRF 失败（HTTP {csrf_resp.status_code}）：{csrf_resp.text}")
    csrf = csrf_resp.json().get("csrfToken") or client.cookies.get("codepoet_csrf")
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": DEMO_PASSWORD},
        headers={"X-CSRF-Token": str(csrf)},
    )
    if resp.status_code != 200:
        client.close()
        raise SystemExit(f"登录 {username} 失败（HTTP {resp.status_code}）：{resp.text}")
    csrf = resp.json().get("csrfToken") or client.cookies.get("codepoet_csrf") or csrf
    if not csrf:
        client.close()
        raise SystemExit(f"登录 {username} 后未获取到 csrfToken")
    return client, str(csrf)


def api_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    csrf: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"X-CSRF-Token": csrf} if csrf else {}
    resp = client.request(method, path, json=payload, headers=headers)
    if resp.status_code not in (200, 201, 202):
        raise SystemExit(f"{method} {path} 失败（HTTP {resp.status_code}）：{resp.text}")
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="通过官方 API 导入并发布三套高精度国画艺术模板")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="后端服务地址")
    parser.add_argument(
        "--force-new-version",
        action="store_true",
        help="若同名模板已存在，为其新建并发布一个新版本",
    )
    parser.add_argument(
        "--include-upgrades",
        action="store_true",
        help="同时发布 upgrade-*.json，为原有模板容器追加美化新版本",
    )
    args = parser.parse_args()

    files = sorted(ART_DIR.glob("art-*.json"))
    if args.include_upgrades:
        files.extend(sorted(ART_DIR.glob("upgrade-*.json")))
    if not files:
        raise SystemExit(f"未在 {ART_DIR} 找到模板 JSON，请先运行 scripts/build_art_templates.py")

    # 升级原有模板容器（如 admin_demo 名下的 tpl-lotus/tpl-bamboo/tpl-narrative）需要管理员权限创建新版本，
    # 再由 expert_demo 审核批准，严格遵守四角色权限分离
    dev_client, dev_csrf = login_client(args.base_url, "developer_demo")
    admin_client, admin_csrf = login_client(args.base_url, "admin_demo")
    exp_client, exp_csrf = login_client(args.base_url, "expert_demo")
    try:
        all_page = api_json(admin_client, "GET", "/api/v1/templates?scope=all&pageSize=100")
        by_name: dict[str, dict[str, Any]] = {
            item["metadata"]["name"]: item for item in all_page.get("items", [])
        }
        by_id: dict[str, dict[str, Any]] = {
            item["id"]: item for item in all_page.get("items", [])
        }

        created_count = 0
        skipped_count = 0
        for path in files:
            spec = json.loads(path.read_text(encoding="utf-8"))
            name = spec["metadata"]["name"]
            target_id = spec.get("targetTemplateId")
            existing = by_id.get(target_id) if target_id else by_name.get(name)

            # 对升级模板：默认始终检查目标容器；若最新版本 changeNote 与本次一致且未 --force-new-version 则跳过
            if existing is not None and existing.get("publishedVersionId") and not args.force_new_version:
                if target_id:
                    pub_ver = api_json(admin_client, "GET", f"/api/v1/template-versions/{existing['publishedVersionId']}")
                    if pub_ver.get("changeNote") == spec.get("changeNote"):
                        print(f"[SKIP] {name} ({target_id}) 已是最新美化版 ({existing['publishedVersionId']})")
                        skipped_count += 1
                        continue
                else:
                    print(
                        f"[SKIP] {name} 已发布（templateId={existing['id']}, "
                        f"versionId={existing['publishedVersionId']}）"
                    )
                    skipped_count += 1
                    continue

            # 若升级已有容器（可能归属 admin_demo 或 developer_demo），使用 admin_client 建版与送审，
            # 新建艺术模板仍使用 developer_demo；审核统一由 expert_demo 批准
            author_client, author_csrf = (admin_client, admin_csrf) if target_id else (dev_client, dev_csrf)

            if existing is None:
                tpl = api_json(
                    author_client,
                    "POST",
                    "/api/v1/templates",
                    csrf=author_csrf,
                    payload={"metadata": spec["metadata"]},
                )
                template_id = tpl["id"]
            else:
                template_id = existing["id"]

            version = api_json(
                author_client,
                "POST",
                f"/api/v1/templates/{template_id}/versions",
                csrf=author_csrf,
                payload={
                    "metadata": spec["metadata"],
                    "irTemplate": spec["irTemplate"],
                    "parameterSchema": spec["parameterSchema"],
                    "bindings": spec["bindings"],
                    "constraints": spec["constraints"],
                    "supportedBackends": spec["supportedBackends"],
                    "changeNote": spec.get("changeNote") or "高精度国画艺术模板",
                },
            )
            submitted = api_json(
                author_client,
                "POST",
                f"/api/v1/template-versions/{version['id']}/submit",
                csrf=author_csrf,
                payload={"expectedRevision": version["revision"]},
            )
            api_json(
                exp_client,
                "POST",
                "/api/v1/reviews",
                csrf=exp_csrf,
                payload={
                    "targetType": "template_version",
                    "targetId": submitted["id"],
                    "expectedRevision": submitted["revision"],
                    "decision": "approve",
                    "reason": "高精度新中式国画模板验收通过，批准发布",
                },
            )
            print(
                f"[OK] 已发布 {name}：templateId={template_id}, "
                f"versionId={submitted['id']}, backends={spec['supportedBackends']}"
            )
            created_count += 1

        print(f"\n完成：新发布 {created_count} 套，跳过 {skipped_count} 套。")
        return 0
    finally:
        dev_client.close()
        admin_client.close()
        exp_client.close()


if __name__ == "__main__":
    raise SystemExit(main())

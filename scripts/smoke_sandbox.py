"""S1-A2 启动冒烟：真起 uvicorn、真登录、真跑一次 DSL。

和单元测试的区别：这里走的是真实 HTTP、真实进程、真实 sqlite 文件，
用来证明「服务起来之后这条链路是通的」，而不是「TestClient 里是通的」。

用法（在仓库根目录）：

    python scripts/smoke_sandbox.py

自己起自己关，不依赖外部已经跑着服务。
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)

BASE_IR = {
    "schemaVersion": "1.0",
    "seed": 42,
    "canvas": {"width": 800, "height": 600, "background": "#FFFFFF"},
    "viewport": {"x": 0, "y": 0, "width": 800, "height": 600},
    "durationMs": 0,
    "nodes": [
        {
            "id": "c1",
            "parentId": None,
            "type": "circle",
            "geometry": {"cx": 400, "cy": 300, "r": 100},
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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Client:
    """极简客户端：保留 Cookie，自动带 X-CSRF-Token。"""

    def __init__(self, base: str) -> None:
        self.base = base
        self.cookies: dict[str, str] = {}
        self.csrf = ""

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        return headers

    def request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data, headers=self._headers(), method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                for header, value in response.getheaders():
                    if header.lower() == "set-cookie":
                        key, _, rest = value.partition("=")
                        self.cookies[key.strip()] = rest.split(";")[0]
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if raw else None

    def prime_csrf(self) -> str:
        _, body = self.request("GET", "/api/v1/auth/csrf")
        self.csrf = body["csrfToken"]
        return self.csrf


def wait_healthy(base: str, timeout: float = 40.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/v1/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001 - 起服务阶段什么都可能失败
            pass
        time.sleep(0.3)
    raise SystemExit("服务在 40s 内没有就绪")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="codepoet-smoke-")
    data_dir = Path(tmp) / "runtime"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["CODEPOET_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(BACKEND)

    print(f"1) 迁移到临时库：{data_dir}")
    subprocess.run(
        [str(PY), str(ROOT / "scripts" / "migrate.py")],
        cwd=str(ROOT),
        env=env,
        check=True,
        capture_output=True,
    )

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    print(f"2) 启动 uvicorn：{base}")
    server = subprocess.Popen(
        [
            str(PY), "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_healthy(base)
        client = Client(base)

        print("3) 能力声明")
        status, capabilities = client.request("GET", "/api/v1/engine/capabilities")
        assert status == 200, capabilities
        features = capabilities["features"]
        print(
            f"   dslSandbox={features['dslSandbox']} "
            f"advancedPolicies={features['advancedPolicies']}"
        )
        assert features["dslSandbox"], "本机拉不起隔离 worker，能力声明应为 false"

        print("4) 未登录访问新端点")
        for method, path in (
            ("POST", "/api/v1/sandbox-runs"),
            ("GET", "/api/v1/sandbox-runs/run-x"),
            ("GET", "/api/v1/admin/policies"),
        ):
            status, body = client.request(method, path, {} if method == "POST" else None)
            print(f"   {method} {path} -> {status} {body['error']['code']}")
            assert status == 401, body

        print("5) 注册 + 登录")
        csrf = client.prime_csrf()
        client.request(
            "POST",
            "/api/v1/auth/register",
            {"username": "smoke1", "password": "Aa1!" + "x" * 12, "nickname": "smoke1"},
        )
        status, body = client.request(
            "POST",
            "/api/v1/auth/login",
            {"username": "smoke1", "password": "Aa1!" + "x" * 12},
        )
        assert status == 200, body
        client.csrf = body["csrfToken"]
        owner_id = body["user"]["id"]
        print(f"   owner={owner_id}")

        print("6) 直接写一条 base generation（绕开模板种子）")
        generation_id = "gen-smoke-base"
        connection = sqlite3.connect(data_dir / "codepoet.sqlite3")
        connection.execute(
            "INSERT INTO generations (id, owner_id, mode, backend, template_version_id,"
            " params_json, seed, ir_json, ir_hash, provenance_json,"
            " validation_report_json, warnings_json, unpublished_source,"
            " request_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                generation_id, owner_id, "ir", "canvas2d", None, "{}", 42,
                json.dumps(BASE_IR), "a" * 64,
                json.dumps({
                    "kind": "manual_edit", "baseGenerationId": None,
                    "matchedEntities": [], "matchedRules": [], "selectedSymbols": [],
                    "paletteDecision": None, "compositionDecision": None,
                    "timelineDecision": None, "templateSelection": None,
                    "conflicts": [], "warnings": [], "engineVersion": "smoke",
                    "ontologyVersion": 1, "policyRevision": 1,
                    "fragmentSource": None, "fragmentSourceHash": None,
                }),
                '{"parameterValid":true,"nodeCount":1,"outOfBoundsNodeCount":0,"issues":[]}',
                "[]", 0, "req-smoke", "2026-11-14T00:00:00+00:00",
            ),
        )
        connection.commit()
        connection.close()

        print("7) 提交 DSL 执行")
        source = json.dumps(
            {
                "version": 1,
                "ops": [
                    {"op": "circle", "cx": 120, "cy": 140, "r": 30, "fill": "#3366CC"},
                    {
                        "op": "polygon",
                        "points": [[500, 200], [560, 200], [530, 260]],
                        "fill": "#33AA66",
                        "opacity": 0.8,
                    },
                ],
            }
        )
        status, run = client.request(
            "POST",
            "/api/v1/sandbox-runs",
            {"baseGenerationId": generation_id, "source": source},
        )
        print(f"   -> {status} status={run.get('status')} id={run.get('id')}")
        assert status == 202, run
        assert run["status"] == "queued" and run["generationId"] is None

        print("8) 轮询到终态")
        deadline = time.monotonic() + 30
        final = run
        while time.monotonic() < deadline:
            _, final = client.request("GET", f"/api/v1/sandbox-runs/{run['id']}")
            if final["status"] in {"succeeded", "failed", "timed_out", "cancelled"}:
                break
            time.sleep(0.2)
        print(
            f"   status={final['status']} generationId={final['generationId']} "
            f"metrics={final.get('metrics')}"
        )
        assert final["status"] == "succeeded", final.get("error")

        print("9) 读产物 generation")
        _, generation = client.request(
            "GET", f"/api/v1/generations/{final['generationId']}"
        )
        nodes = [node["id"] for node in generation["ir"]["nodes"]]
        print(
            f"   mode={generation['mode']} kind={generation['provenance']['kind']} "
            f"nodes={len(nodes)}"
        )
        assert generation["mode"] == "fragment"
        assert generation["provenance"]["kind"] == "fragment"
        assert generation["provenance"]["baseGenerationId"] == generation_id
        # 源码不能经 provenance 流出：作品公开后 provenance 是匿名可读的
        assert generation["provenance"]["fragmentSource"] is None, "provenance 不该带源码"
        assert (
            generation["provenance"]["fragmentSourceHash"] == final["sourceHash"]
        ), "hash 必须留得住，否则没法溯源到那条 run"

        print("10) AST 违规必须留痕但不产出 generation")
        status, rejected = client.request(
            "POST",
            "/api/v1/sandbox-runs",
            {
                "baseGenerationId": generation_id,
                "source": json.dumps({"version": 1, "ops": [{"op": "exec", "code": "rm -rf /"}]}),
            },
        )
        assert status == 202, rejected
        deadline = time.monotonic() + 30
        final = rejected
        while time.monotonic() < deadline:
            _, final = client.request("GET", f"/api/v1/sandbox-runs/{rejected['id']}")
            if final["status"] in {"succeeded", "failed", "timed_out", "cancelled"}:
                break
            time.sleep(0.2)
        findings = final.get("auditFindings") or []
        print(
            f"   status={final['status']} code={(final.get('error') or {}).get('code')} "
            f"rule={(findings[0]['ruleId'] if findings else None)} "
            f"generationId={final['generationId']}"
        )
        assert final["status"] == "failed"
        assert final["error"]["code"] == "DSL_AUDIT_REJECTED"
        assert final["generationId"] is None
        # finding 的 ruleId 必须是审计规则 ID，不是错误码
        assert findings, "静态拒绝必须给出一条审计发现"
        assert findings[0]["ruleId"] == "DSL_OP_WHITELIST", findings
        assert findings[0]["line"] >= 0

        print("\n冒烟通过：受理 → 隔离执行 → 落库 → 违规留痕，全部符合契约")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        output = server.stdout.read().decode("utf-8", errors="replace") if server.stdout else ""
        if "Traceback" in output:
            print("\n服务端输出里有 Traceback：\n" + output[-3000:])


if __name__ == "__main__":
    sys.exit(main())

"""知识图谱接入（K1-A）：白名单、映射、快照、派生继承。

测试不连外网：把 `_SOURCES["wikidata"]` 换成假实现，验证的是**我们自己的
逻辑**——真来源的连通性由 `scripts/probe_kg.py` 单独负责（那是环境结论，
不该让 CI 依赖公网）。

覆盖契约里的四条错误分支（503/504/422/422），以及三条容易被忽略的语义：

* `mappingDecisions` 必须真的记下「哪条外部事实影响了什么」；
* 外部事实要进 caption（IR 的叙事节点），不能只躺在 provenance 里；
* 派生之后 `externalGraph` 仍保留——否则溯源一改就断。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import errors
from app.main import create_app
from app.models.users import User as UserModel
from app.schemas.knowledge_graph import (
    ExternalEntity,
    ExternalGraph,
    ExternalRelation,
    MappingDecision,
)
from app.services import import_export
from app.services import knowledge_graph
from app.services import works as works_service
from app.settings import Settings

API = "/api/v1"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def apply_migrations(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        connection.executescript(path.read_text(encoding="utf-8"))
    connection.commit()
    connection.close()


def fake_graph(label: str = "莲", *, with_relation: bool = True) -> ExternalGraph:
    """一份形状真实的外部分布快照（莲 Q16528 的裁剪版）。"""
    relations = []
    attributes = {"学名(P225)": "Nelumbo nucifera"}
    decisions: list[MappingDecision] = []
    if with_relation:
        relations.append(
            ExternalRelation(
                id="Q16528-P105-Q7432",
                type="分类单元",
                fromId="Q16528",
                toId="Q7432",
                label="植物",
            )
        )
        attributes["分类单元(P105)"] = "植物"
    return ExternalGraph(
        sourceId="wikidata",
        externalEntityId="Q16528",
        fetchedAt=datetime.now(timezone.utc),
        dataVersion=None,
        contentHash="a" * 32,
        entities=[
            ExternalEntity(
                id="Q16528",
                label=label,
                description="睡莲科莲属多年生挺水植物",
                attributes=attributes,
            )
        ],
        relations=relations,
        mappingDecisions=decisions,
    )


class _Base(unittest.TestCase):
    sources = "wikidata"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(
            _env_file=None,
            data_dir=Path(self.tmp.name) / "runtime",
            knowledge_graph_sources=self.sources,
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        apply_migrations(self.settings.database_path)
        self.csrf, self.user_id = self._register("kg_user")
        self._seed_knowledge_and_rule()

    def _register(self, name: str) -> tuple[str, str]:
        token = self.client.get(f"{API}/auth/csrf").json()["csrfToken"]
        password = "Aa1!" + "x" * 12
        created = self.client.post(
            f"{API}/auth/register",
            json={"username": name, "password": password, "nickname": name},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(created.status_code, 201, created.text)
        user_id = created.json()["id"]
        login = self.client.post(
            f"{API}/auth/login",
            json={"username": name, "password": password},
            headers={"X-CSRF-Token": self.client.get(f"{API}/auth/csrf").json()["csrfToken"]},
        )
        self.assertEqual(login.status_code, 200, login.text)
        return login.json()["csrfToken"], user_id

    def _seed_knowledge_and_rule(self) -> None:
        """站内「莲」+ 一条命中它的规则，作为映射落点。"""
        db = self.app.state.sessions()
        try:
            actor = db.get(UserModel, self.user_id)
            import_export._insert_entities(  # noqa: SLF001
                db,
                [{
                    "id": "knowledge-lotus",
                    "name": "莲",
                    "summary": "出淤泥而不染",
                    "tagIds": [],
                    "conceptIds": [],
                }],
                actor,
            )
            import_export._insert_rules(  # noqa: SLF001
                db,
                [{
                    "id": "rule-lotus",
                    "name": "莲·清正",
                    "enabled": True,
                    "priority": 80,
                    "match": {"entityIds": ["knowledge-lotus"], "operator": "any"},
                    "effects": {
                        "symbolIds": ["lotus"],
                        "palette": ["#D4537E", "#EF9F27"],
                        "composition": "radial",
                        "motion": "sway",
                        "caption": "莲：出淤泥而不染。",
                    },
                    "explanation": "",
                    "revision": 1,
                    "version": 1,
                }],
                actor,
            )
            db.commit()
        finally:
            db.close()

    def _post(self, body: dict):
        return self.client.post(
            f"{API}/knowledge-graph/generations",
            json=body,
            headers={"X-CSRF-Token": self.csrf},
        )


class WhitelistTests(_Base):
    """来源白名单：只认配置里的键。"""

    sources = ""

    def test_unconfigured_source_returns_503(self) -> None:
        response = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"})
        self.assertEqual(response.status_code, 503, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "KNOWLEDGE_GRAPH_UNAVAILABLE")
        self.assertEqual(error["details"]["available"], [])

    def test_unknown_source_returns_503(self) -> None:
        self.app.state.settings.knowledge_graph_sources = "wikidata"
        response = self._post({"sourceId": "cypher", "externalEntityId": "Q16528"})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error"]["code"], "KNOWLEDGE_GRAPH_UNAVAILABLE")


class FetchFailureTests(_Base):
    """抓取失败按契约翻译成错误码，不退化成 500。"""

    def _with_source(self, behaviour):
        def fake(entity_id: str, settings):  # noqa: ANN001
            raise behaviour

        patcher = patch.dict(knowledge_graph._SOURCES, {"wikidata": fake})  # noqa: SLF001
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_entity_not_found_returns_422(self) -> None:
        self._with_source(errors.knowledge_graph_entity_not_found("没有这个实体"))
        response = self._post({"sourceId": "wikidata", "externalEntityId": "Q0"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["error"]["code"], "KNOWLEDGE_GRAPH_ENTITY_NOT_FOUND"
        )

    def test_timeout_returns_504(self) -> None:
        self._with_source(errors.knowledge_graph_timeout("超时"))
        response = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"})
        self.assertEqual(response.status_code, 504, response.text)
        self.assertEqual(response.json()["error"]["code"], "KNOWLEDGE_GRAPH_TIMEOUT")


class GenerationTests(_Base):
    """主路径：抓取 → 映射 → 生成 → 快照。"""

    def _patch_source(self, graph: ExternalGraph):
        patcher = patch.dict(
            knowledge_graph._SOURCES,  # noqa: SLF001
            {"wikidata": lambda entity_id, settings: graph},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_requires_login(self) -> None:
        self.client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": self.csrf})
        response = self.client.post(
            f"{API}/knowledge-graph/generations",
            json={"sourceId": "wikidata", "externalEntityId": "Q16528"},
        )
        self.assertIn(response.status_code, (401, 403), response.text)

    def test_success_writes_external_graph_snapshot(self) -> None:
        self._patch_source(fake_graph())
        response = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()

        self.assertEqual(body["backend"], "canvas2d")
        provenance = body["provenance"]
        graph = provenance["externalGraph"]
        self.assertIsNotNone(graph, "外部快照必须写进 provenance")
        self.assertEqual(graph["sourceId"], "wikidata")
        self.assertEqual(graph["externalEntityId"], "Q16528")
        self.assertIsNone(graph["dataVersion"], "来源不给版本号就该是 null")
        self.assertTrue(graph["contentHash"])
        self.assertEqual(len(graph["entities"]), 1)
        self.assertEqual(len(graph["relations"]), 1)

        kinds = {(item["kind"], item["affects"]) for item in graph["mappingDecisions"]}
        self.assertIn(("entity", "rule-selection"), kinds, "实体映射要记进决策")
        self.assertIn(("relation", "caption"), kinds, "关系映射要记进决策")

    def test_relation_changes_caption_in_ir(self) -> None:
        """外部事实必须落到 IR 的叙事节点，而不只是待在快照里。"""
        self._patch_source(fake_graph())
        body = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"}).json()
        narrative = body["ir"]["narrative"]
        self.assertTrue(narrative, "IR 必须带叙事节点")
        captions = " ".join(str(item.get("caption") or "") for item in narrative)
        self.assertIn("Nelumbo nucifera", captions, "学名应进入叙事说明")
        self.assertIn("出淤泥而不染", captions, "站内规则的说明不能被外部事实顶掉")

    def test_unmappable_entity_returns_422(self) -> None:
        self._patch_source(fake_graph(label="恐龙"))
        response = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "NO_SEMANTIC_MATCH")

    def test_derived_generation_keeps_external_graph(self) -> None:
        """细则 K1：externalGraph 经过 ir 派生后保留。"""
        self._patch_source(fake_graph())
        base = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"}).json()
        derived = self.client.post(
            f"{API}/generations",
            json={
                "mode": "ir",
                "baseGenerationId": base["id"],
                "ir": base["ir"],
                "backend": "canvas2d",
            },
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertIn(derived.status_code, (200, 201), derived.text)
        graph = derived.json()["provenance"]["externalGraph"]
        self.assertIsNotNone(graph, "派生后不能丢掉外部来源")
        self.assertEqual(graph["externalEntityId"], "Q16528")


    def test_derived_subgraph_keeps_external_graph(self) -> None:
        """subgraph 派生也要保留 externalGraph（评审指出只测了 ir）。

        细则 K1 要求 externalGraph 经过 ir / subgraph 后都保留。这一层没有新增
        外部事实，所以原样继承：contentHash 与基底一致，mappingDecisions 不动。
        """
        self._patch_source(fake_graph())
        base = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"}).json()
        root = base["ir"]["nodes"][0]["id"]
        derived = self.client.post(
            f"{API}/generations/{base['id']}/subgraph",
            json={"rootNodeId": root, "replacementNodes": []},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(derived.status_code, 201, derived.text)
        graph = derived.json()["generation"]["provenance"]["externalGraph"]
        self.assertIsNotNone(graph, "subgraph 派生后不能丢掉外部来源")
        self.assertEqual(graph["externalEntityId"], "Q16528")
        self.assertEqual(
            graph["contentHash"],
            base["provenance"]["externalGraph"]["contentHash"],
            "这一层没有新增外部事实，快照应原样继承",
        )


class OfflineReplayTests(unittest.TestCase):
    """旧作品复现只读快照，不联网。"""

    def test_graph_snapshot_is_self_contained(self) -> None:
        """快照里必须自带足够信息，复现时不需要再查来源。"""
        graph = fake_graph()
        payload = graph.model_dump(mode="json")
        for key in ("sourceId", "externalEntityId", "fetchedAt", "contentHash",
                    "entities", "relations", "mappingDecisions"):
            self.assertIn(key, payload)
        # 实体自带 label/attributes，关系自带 type/label，不依赖外部再查
        self.assertTrue(payload["entities"][0]["label"])
        self.assertTrue(payload["entities"][0]["attributes"])
        self.assertEqual(payload["relations"][0]["label"], "植物")
        # JSON 可序列化且稳定（contentHash 依赖它）
        again = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        self.assertEqual(payload["contentHash"], again["contentHash"])

class ExplicitMappingTests(_Base):
    """显式映射表与「专指规则优先」。

    这两条都是实测踩出来的：
    * 精确同名只能覆盖「莲」，竹（Wikidata 叫竹亚科）和梅（站内叫墨梅）都映射不上；
    * 光能映射还不够，priority 90 的多实体叙事规则会把画面主体抢走。
    """

    def _patch(self, graph: ExternalGraph):
        patcher = patch.dict(
            knowledge_graph._SOURCES,  # noqa: SLF001
            {"wikidata": lambda entity_id, settings: graph},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_mapping(self, entries: dict) -> None:
        path = Path(self.tmp.name) / "mapping.json"
        path.write_text(
            json.dumps({"version": 1, "sources": {"wikidata": entries}}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.app.state.settings.knowledge_graph_mapping_file = str(path)

    def test_explicit_mapping_beats_label(self) -> None:
        """外部标签是「竹亚科」、站内条目叫「竹」时，靠表映射而不是靠相似度。"""
        self._write_mapping({
            "Q670887": {"knowledgeId": "knowledge-lotus", "reason": "测试用：指向莲"}
        })
        graph = fake_graph(label="竹亚科")
        graph = graph.model_copy(update={"externalEntityId": "Q670887"})
        self._patch(graph)
        body = self._post({"sourceId": "wikidata", "externalEntityId": "Q670887"}).json()
        reasons = [d["reason"] for d in body["provenance"]["externalGraph"]["mappingDecisions"]]
        self.assertTrue(any("显式映射" in text for text in reasons), reasons)

    def test_prefer_entity_rule_beats_generic_narrative(self) -> None:
        """泛化的多实体叙事规则不能抢走单实体的主体。

        造一条 priority 更高、entityIds 含 {lotus, bamboo} 的规则：请求竹时，
        它不该被选中——画面主体应该是竹，不是莲的叙事。
        """
        db = self.app.state.sessions()
        try:
            actor = db.get(UserModel, self.user_id)
            import_export._insert_rules(  # noqa: SLF001
                db,
                [{
                    "id": "rule-narrative",
                    "name": "四秒·清莲叙事",
                    "enabled": True,
                    "priority": 90,
                    "match": {
                        "entityIds": ["knowledge-lotus", "knowledge-bamboo"],
                        "operator": "any",
                    },
                    "effects": {
                        "symbolIds": ["narrative", "lotus", "bamboo"],
                        "palette": ["#F7F6F0", "#D4537E"],
                        "composition": "radial",
                        "motion": "reveal",
                        "caption": "四秒清莲叙事：由水到莲，再到守正。",
                    },
                    "explanation": "",
                    "revision": 1,
                    "version": 1,
                }],
                actor,
            )
            db.commit()
        finally:
            db.close()

        self._patch(fake_graph())
        body = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"}).json()
        captions = " ".join(
            str(item.get("caption") or "") for item in body["ir"]["narrative"]
        )
        self.assertIn("出淤泥而不染", captions, "应选中专指「莲」的规则")
        self.assertNotIn("四秒清莲叙事", captions, "泛化叙事规则不该抢走主体")


class CaptionBudgetTests(_Base):
    """决策记录必须与真实采纳一致（评审 P2）。

    旧实现里 `_fact_sentence` 只取前三个属性拼后缀，`_relation_decisions` 却把
    **所有**有 label 的关系都记成「写入叙事说明」——记录和事实两回事。
    而且 200 字符截断完全没考虑，长事实会被静默截掉却仍记为已采纳。
    """

    def _patch(self, graph: ExternalGraph):
        patcher = patch.dict(
            knowledge_graph._SOURCES,  # noqa: SLF001
            {"wikidata": lambda entity_id, settings: graph},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fat_graph(self, count: int = 6, length: int = 40) -> ExternalGraph:
        """造一堆长度可观的关系，保证总长超过 caption 预算。"""
        base = fake_graph()
        relations = [
            ExternalRelation(
                id=f"Q16528-P{i}-Q{i}",
                type="相关",
                fromId="Q16528",
                toId=f"Q{i}",
                label="关" * length,
            )
            for i in range(count)
        ]
        return base.model_copy(update={"relations": relations})

    def test_caption_budget_never_exceeds_limit(self) -> None:
        """纯函数：拼出来的后缀 + 基线 caption 不超过 200。"""
        base_caption = "莲：出淤泥而不染，濯清涟而不妖。"  # 18 字符
        fragments = [
            knowledge_graph.FactFragment(
                text="x" * 60, kind="relation", externalId=f"r{i}", label=f"L{i}"
            )
            for i in range(5)
        ]
        suffix, adopted, skipped = knowledge_graph._apply_caption_budget(  # noqa: SLF001
            base_caption, fragments
        )
        self.assertTrue(adopted, "至少该装进一条")
        self.assertTrue(skipped, "超长片段必须被记为未采纳")
        total = len(base_caption) + 1 + len(suffix)
        self.assertLessEqual(total, 200, f"实际 {total} 字符，超出预算")
        self.assertEqual(len(adopted) + len(skipped), len(fragments))

    def test_only_adopted_facts_are_recorded(self) -> None:
        """记成「写入叙事说明」的，必须真的出现在 caption 里。"""
        self._patch(self._fat_graph(count=6, length=40))
        body = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"}).json()

        decisions = body["provenance"]["externalGraph"]["mappingDecisions"]
        relations = body["provenance"]["externalGraph"]["relations"]
        relation_decisions = [d for d in decisions if d["kind"] == "relation"]
        captions = " ".join(str(node.get("caption") or "") for node in body["ir"]["narrative"])

        self.assertLess(
            len(relation_decisions),
            len(relations),
            "6 条 40 字的关系必然装不下，必须有未采纳的——否则说明仍是无条件全记",
        )
        for decision in relation_decisions:
            recorded = decision["reason"].split("「", 1)[1].rstrip("」")
            self.assertIn(
                recorded, captions, f"记成已写入但 caption 里没有：{recorded[:24]}"
            )

    def test_recorded_never_exceeds_fetched(self) -> None:
        """记录数永远不超过抓取数。"""
        self._patch(self._fat_graph(count=6, length=40))
        body = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"}).json()
        graph = body["provenance"]["externalGraph"]
        caption_decisions = [
            d for d in graph["mappingDecisions"]
            if d["affects"] == "caption" and d["kind"] in {"relation", "attribute"}
        ]
        fetched = len(graph["relations"]) + len(graph["entities"][0]["attributes"])
        self.assertLessEqual(len(caption_decisions), fetched)

    def test_relation_can_reach_caption(self) -> None:
        """关系必须真的能进 caption，不是只有属性。

        旧实现只取前三个属性，关系一条都进不去——那「关系参与视觉决策」
        就只是空话。
        """
        self._patch(fake_graph())
        body = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"}).json()
        captions = " ".join(str(node.get("caption") or "") for node in body["ir"]["narrative"])
        self.assertIn("分类单元：植物", captions, f"关系没进 caption：{captions}")


class DependencyHashTests(unittest.TestCase):
    """依赖快照哈希必须反映外部事实（评审 P2）。

    旧实现写死 `sha256(b"codepoet-p0-04")`：换实体、换规则、换渲染器都不变。
    那这个字段等于没写——复现时无法判断两份作品是不是同一套依赖。
    """

    def _hash(
        self,
        external: dict | None,
        *,
        rule_version: int = 1,
        ir_hash: str = "a" * 64,
        renderer_version: str | None = "codepoet-canvas-1.0.0",
    ) -> str:
        from types import SimpleNamespace

        provenance = {
            "engineVersion": "p0-03.1",
            "ontologyVersion": 1,
            "policyRevision": 1,
            "matchedRules": [{"ruleId": "rule-1", "version": rule_version}],
            "matchedEntities": [{"entityId": "knowledge-lotus", "revision": 1}],
            "externalGraph": external,
        }
        generation = SimpleNamespace(
            provenance_json=json.dumps(provenance),
            ir_hash=ir_hash,
            backend="canvas2d",
        )
        return works_service._dependency_snapshot_hash(  # noqa: SLF001
            generation, renderer_version
        )

    def _external(self, content_hash: str = "b" * 32) -> dict:
        return {
            "sourceId": "wikidata",
            "externalEntityId": "Q16528",
            "contentHash": content_hash,
        }

    def test_same_dependencies_same_hash(self) -> None:
        self.assertEqual(self._hash(self._external()), self._hash(self._external()))

    def test_external_content_change_changes_hash(self) -> None:
        """同一实体、内容变了 → 依赖必须变。这是本条评审的核心。"""
        self.assertNotEqual(
            self._hash(self._external("b" * 32)),
            self._hash(self._external("c" * 32)),
            "外部内容哈希变了，依赖快照哈希却没变",
        )

    def test_external_entity_change_changes_hash(self) -> None:
        other = {**self._external(), "externalEntityId": "Q157763"}
        self.assertNotEqual(self._hash(self._external()), self._hash(other))

    def test_rule_version_change_changes_hash(self) -> None:
        self.assertNotEqual(self._hash(None), self._hash(None, rule_version=2))

    def test_ir_change_changes_hash(self) -> None:
        self.assertNotEqual(self._hash(None), self._hash(None, ir_hash="e" * 64))

    def test_graph_generation_differs_from_plain(self) -> None:
        """带外部图谱与不带，依赖必然不同——否则「依赖同一」是假话。"""
        self.assertNotEqual(self._hash(None), self._hash(self._external()))

    def test_actual_renderer_version_changes_hash(self) -> None:
        """同一个 generation 被两个不同渲染器跑过 → 依赖哈希必须不同（评审 P2）。

        旧实现读的是**当前注册表**的版本，所以这两个会算出同一个哈希——
        等于说「两次渲染依赖相同」，而实际用的渲染器根本不同。
        """
        external = self._external()
        self.assertNotEqual(
            self._hash(external, renderer_version="codepoet-canvas-1.0.0"),
            self._hash(external, renderer_version="codepoet-canvas-0.9.0"),
        )

    def test_task_version_not_current_registry_version(self) -> None:
        """升级期间完成旧任务：manifest 记的是旧版本，哈希也必须按旧版本算。

        这条用「同一个 generation + 两个不同任务版本」表达：
        只要哈希随传入的任务版本变化，就不会出现「manifest 说旧版本、
        哈希按新版本」的自相矛盾。
        """
        external = self._external()
        old_task = self._hash(external, renderer_version="codepoet-canvas-0.9.0")
        new_task = self._hash(external, renderer_version="codepoet-canvas-1.0.0")
        # 除了渲染器版本，其余依赖完全相同
        self.assertNotEqual(old_task, new_task)
        # 同一版本重复计算必须稳定
        self.assertEqual(old_task, self._hash(external, renderer_version="codepoet-canvas-0.9.0"))

    def test_missing_renderer_version_uses_placeholder(self) -> None:
        """任务没记版本时落到 "unknown"，与 manifest 里 `or "unknown"` 一致。"""
        external = self._external()
        self.assertEqual(
            self._hash(external, renderer_version=None),
            self._hash(external, renderer_version=""),
        )

    def test_hash_is_stable_across_process_order(self) -> None:
        """字典顺序不影响结果（sort_keys + 固定分隔符）。"""
        first = self._hash(self._external())
        # 同样的内容，键顺序换一下重建一次
        reordered = {
            "contentHash": "b" * 32,
            "externalEntityId": "Q16528",
            "sourceId": "wikidata",
        }
        self.assertEqual(first, self._hash(reordered))


class RestartReplayTests(_Base):
    """保存 → 重开 → 断网 → 复现（评审 P2）。

    原来叫「离线复现」的用例只检查快照 JSON 的形状，那不是复现——真正的复现
    要经过「作品版本已经落库、服务重启、来源完全不可达」这三步。

    这里三个要素都真实构造：

    1. 作品与版本**真的落库**（含一帧真实 PNG 产物）；
    2. **新建一个 app 实例**指向同一个数据库，模拟进程重启；
    3. 在重开后的上下文里把来源清空、并让 ``urlopen`` 直接抛错。

    复现接口必须照样返回 202，且读回来的快照仍带完整 ``externalGraph``。
    """

    NOW = "2026-11-13T00:00:00+00:00"

    def _patch_source(self, graph: ExternalGraph):
        patcher = patch.dict(
            knowledge_graph._SOURCES,  # noqa: SLF001
            {"wikidata": lambda entity_id, settings: graph},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _png(self) -> bytes:
        import struct
        import zlib

        width, height = 8, 6
        raw = b"".join(b"\x00" + bytes((10, 120, 200)) * width for _ in range(height))

        def chunk(tag: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + tag
                + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b"")
        )

    def _save_work_with_version(self, gen_id: str) -> str:
        """把生成结果存成作品与已发布版本，附一帧真实产物。"""
        from app.models.users import User as _User  # noqa: F401  （保持导入顺序稳定）
        from app.services import uploads

        stored = uploads.store_frame(self.settings, self.user_id, self._png(), 0)
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO render_jobs (id, owner_id, generation_id, backend, execution_mode,"
            " status, phase, times_ms_json, policy_revision, renderer_version,"
            " client_environment_json, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("job-kg", self.user_id, gen_id, "canvas2d", "browser_upload", "succeeded",
             "completed", "[0]", 1, "codepoet-canvas-1.0.0",
             '{"userAgent":"test/1.0","deviceScaleFactor":1}', 1, self.NOW, self.NOW),
        )
        connection.execute(
            "INSERT INTO artifacts (id, owner_id, kind, content_type, byte_size,"
            " storage_path, sha256, width, height, time_ms, render_job_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (stored.artifact_id, self.user_id, stored.kind, stored.content_type,
             stored.byte_size, stored.storage_path, stored.sha256,
             stored.width, stored.height, stored.time_ms, "job-kg", self.NOW),
        )
        connection.execute(
            "INSERT INTO works (id, owner_id, title, description, published_version_id,"
            " revision, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("wrk-kg", self.user_id, "图谱作品", "", "wv-kg", 1, self.NOW, self.NOW),
        )
        connection.execute(
            "INSERT INTO work_versions (id, work_id, version_number, created_by, status,"
            " title, description, generation_id, render_job_id, change_note,"
            " submitted_by, submitted_at, revision, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("wv-kg", "wrk-kg", 1, self.user_id, "published", "图谱作品", "",
             gen_id, "job-kg", "", None, None, 1, self.NOW, self.NOW),
        )
        connection.commit()
        connection.close()
        return "wv-kg"

    def _reopen(self):
        """新建 app 实例 + 新 client，模拟进程重启后重新进入。"""
        app = create_app(self.settings)
        client = TestClient(app)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        token = client.get(f"{API}/auth/csrf").json()["csrfToken"]
        login = client.post(
            f"{API}/auth/login",
            json={"username": "kg_user", "password": "Aa1!" + "x" * 12},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(login.status_code, 200, login.text)
        return client, login.json()["csrfToken"]

    def test_replay_after_restart_with_source_down(self) -> None:
        self._patch_source(fake_graph())
        base = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"}).json()
        version_id = self._save_work_with_version(base["id"])

        # ---- 重开 + 断网 ----
        client2, csrf2 = self._reopen()
        with (
            patch.dict(knowledge_graph._SOURCES, {}, clear=True),  # noqa: SLF001
            patch(
                "app.services.knowledge_graph.urllib.request.urlopen",
                side_effect=OSError("network unreachable"),
            ),
        ):
            response = client2.post(
                f"{API}/work-versions/{version_id}/reproduce",
                json={
                    "rendererVersion": "codepoet-canvas-1.0.0",
                    "clientEnvironment": {"userAgent": "test/1.0", "deviceScaleFactor": 1},
                },
                headers={"X-CSRF-Token": csrf2},
            )
            self.assertEqual(response.status_code, 202, response.text)

            # 快照在重启后仍自包含
            read = client2.get(f"{API}/generations/{base['id']}")
            self.assertEqual(read.status_code, 200, read.text)
            graph = read.json()["provenance"]["externalGraph"]
            self.assertIsNotNone(graph, "重启后读不到外部快照")
            self.assertEqual(graph["externalEntityId"], "Q16528")
            self.assertTrue(graph["contentHash"])
            self.assertTrue(graph["relations"], "关系快照必须随作品保存")

    def test_replay_does_not_need_source_configured(self) -> None:
        """来源从实例配置里移除后，旧作品仍然可以复现。

        K1 的验收要的是这一条：**旧作品复现不联网**。如果复现路径悄悄依赖
        「当前还配置着 wikidata」，那换台机器就复现不了。
        """
        self._patch_source(fake_graph())
        base = self._post({"sourceId": "wikidata", "externalEntityId": "Q16528"}).json()
        version_id = self._save_work_with_version(base["id"])

        self.app.state.settings.knowledge_graph_sources = ""
        client2, csrf2 = self._reopen()
        response = client2.post(
            f"{API}/work-versions/{version_id}/reproduce",
            json={
                "rendererVersion": "codepoet-canvas-1.0.0",
                "clientEnvironment": {"userAgent": "test/1.0", "deviceScaleFactor": 1},
            },
            headers={"X-CSRF-Token": csrf2},
        )
        self.assertEqual(response.status_code, 202, response.text)

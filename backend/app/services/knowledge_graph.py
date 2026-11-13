"""知识图谱接入（K1-A）：外部实体 → 站内知识 → IR。

链路只有一条，但每一步都有明确边界：

    白名单 sourceId → 抓实体（有界 + 超时）→ 解析属性/一跳关系
    → 映射站内知识实体 → 命中映射规则 → 生成 IR + 外部快照

三条硬约束，都是踩过的坑换来的：

1. **不接受用户传 URL**。`sourceId` 只认部署配置里的键，否则服务端就成了
   任意地址的代理（SSRF、内网探测、凭据外泄全跟着来）。
2. **不依赖 SPARQL**。Wikidata 的查询服务在故障期会限流到 1 req/min（实测
   429），而实体快照接口稳定。关系从 `claims` 里解析即可，不需要查询服务。
3. **抓不到就明确失败**，绝不返回「伪本地生成」——那会让来源快照记下一次
   其实没发生过的抓取。

关系怎么「参与决策」：外部属性/关系拼成一句可读事实追加进 `caption`，而
caption 是 IR 的叙事节点内容；同时每条影响都写进 `mappingDecisions`。
不加未经站内规则背书的视觉决策（比如自己发明调色规则）。
"""

from __future__ import annotations

import hashlib
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from dataclasses import replace
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.engine import capabilities
from app.engine import semantic as semantic_engine
from app.models.knowledge import KnowledgeEntity as KnowledgeModel
from app.models.users import User
from app.schemas.generation import SemanticGeneration, SemanticInput
from app.schemas.knowledge_graph import (
    MAX_ATTRIBUTES,
    MAX_ENTITIES,
    MAX_RELATIONS,
    ExternalEntity,
    ExternalGraph,
    ExternalRelation,
    GraphGenerationRequest,
    MappingDecision,
)
from app.services import generations as generations_service
from app.services import knowledge as knowledge_service
from app.services import rules as rules_service
from app.settings import PROJECT_ROOT, Settings

__all__ = [
    "available_sources",
    "fetch_graph",
    "generate_from_graph",
]

#: 单次响应读取上限。实体快照正常 100~300KB，2MB 已经是很宽松的兜底。
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

USER_AGENT = "CodePoet-K1/0.1 (+https://github.com/k6mple/CodePoet)"

#: Wikidata 属性白名单。只取和「能不能画、画什么」有关的事实；
#: 一个实体常有 100+ 个 claim，全抓既没必要也过不了有界读取这一关。
WIKIDATA_ATTRIBUTES: dict[str, str] = {
    "P225": "学名",
    "P105": "分类单元",
    "P171": "亲本",
    "P31": "实例于",
    "P279": "子类",
    "P1034": "模式种",
    "P610": "最高分类单元",
    "P366": "用途",
    "P1343": "记载于",
}

#: 拼进 caption 的属性顺序：先给「是什么」，再给「归哪类」。
CAPTION_ATTRIBUTE_ORDER = ("学名", "分类单元", "亲本", "用途")

#: 关系目标查标签的上限。一次批量请求，不做 N 次。
MAX_LABEL_LOOKUPS = 10


class _FetchFailure(Exception):
    """内部信号：抓取失败，由调用方翻译成契约错误码。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind  # "timeout" | "unavailable" | "not_found"
        self.message = message


def available_sources(settings: Settings) -> list[str]:
    """当前实例允许调用的来源。空列表 = 未配置，端点应 503。"""
    raw = getattr(settings, "knowledge_graph_sources", "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _get_json(url: str, settings: Settings) -> dict:
    """有界 + 带超时的 GET。"""
    timeout = float(getattr(settings, "knowledge_graph_timeout_seconds", 10.0))
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _FetchFailure("not_found", f"来源返回 404：{url}") from exc
        raise _FetchFailure("unavailable", f"来源返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise _FetchFailure("timeout", f"来源在 {timeout:g}s 内没有响应") from exc
        raise _FetchFailure("unavailable", f"来源不可达：{reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise _FetchFailure("timeout", f"来源在 {timeout:g}s 内没有响应") from exc

    if len(raw) > MAX_RESPONSE_BYTES:
        raise _FetchFailure("unavailable", "来源响应超过读取上限")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _FetchFailure("unavailable", "来源返回的不是合法 JSON") from exc


# ---------------------------------------------------------------------------
# Wikidata 适配器
# ---------------------------------------------------------------------------


def _wikidata_labels(qids: list[str], settings: Settings) -> dict[str, str]:
    """批量查标签。失败时返回空 dict——标签只是可读性增强，不该让整次抓取失败。"""
    if not qids:
        return {}
    ids = "|".join(qids[:MAX_LABEL_LOOKUPS])
    query = urllib.parse.urlencode({
        "action": "wbgetentities",
        "ids": ids,
        "props": "labels",
        "languages": "zh|zh-cn|en",
        "format": "json",
    })
    try:
        data = _get_json(f"https://www.wikidata.org/w/api.php?{query}", settings)
    except _FetchFailure:
        return {}
    labels: dict[str, str] = {}
    for qid, payload in (data.get("entities") or {}).items():
        for lang in ("zh", "zh-cn", "en"):
            label = (payload.get("labels") or {}).get(lang, {}).get("value")
            if label:
                labels[qid] = label
                break
    return labels


def _fetch_wikidata(entity_id: str, settings: Settings) -> ExternalGraph:
    data = _get_json(
        f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json", settings
    )
    entity = (data.get("entities") or {}).get(entity_id)
    if entity is None:
        raise errors.knowledge_graph_entity_not_found(
            f"来源里没有实体 {entity_id}", details={"sourceId": "wikidata"}
        )

    labels = entity.get("labels") or {}
    label = None
    for lang in ("zh", "zh-cn", "en"):
        label = (labels.get(lang) or {}).get("value")
        if label:
            break
    if not label:
        raise errors.knowledge_graph_entity_not_found(
            f"实体 {entity_id} 没有可用标签", details={"sourceId": "wikidata"}
        )
    description = None
    for lang in ("zh", "zh-cn", "en"):
        description = (entity.get("descriptions") or {}).get(lang, {}).get("value")
        if description:
            break

    claims = entity.get("claims") or {}
    attributes: dict[str, str] = {}
    pending_relations: list[tuple[str, str, str]] = []  # (属性名, 属性 id, 目标 Q)

    for pid, values in claims.items():
        name = WIKIDATA_ATTRIBUTES.get(pid)
        if name is None:
            continue
        for value in values:
            snak = value.get("mainsnak") or {}
            if snak.get("snaktype") != "value":
                continue
            datavalue = snak.get("datavalue") or {}
            if datavalue.get("type") == "string":
                if len(attributes) < MAX_ATTRIBUTES:
                    attributes[f"{name}({pid})"] = str(datavalue["value"])[:120]
            elif datavalue.get("type") == "wikibase-entityid":
                target = datavalue.get("value", {}).get("id")
                if target:
                    pending_relations.append((name, pid, target))

    target_ids = list(dict.fromkeys(target for _, _, target in pending_relations))
    target_labels = _wikidata_labels(target_ids, settings)

    relations: list[ExternalRelation] = []
    for name, pid, target in pending_relations[:MAX_RELATIONS]:
        relations.append(
            ExternalRelation(
                id=f"{entity_id}-{pid}-{target}",
                type=name,
                fromId=entity_id,
                toId=target,
                label=target_labels.get(target),
            )
        )
        # 关系的可读标签也记一条属性，方便 caption 直接引用
        if len(attributes) < MAX_ATTRIBUTES and target_labels.get(target):
            attributes[f"{name}({pid})"] = target_labels[target][:120]

    entities = [
        ExternalEntity(
            id=entity_id,
            label=label,
            description=(description or None),
            attributes=dict(attributes),
        )
    ]

    payload_for_hash = {
        "entities": [item.model_dump(mode="json") for item in entities],
        "relations": [item.model_dump(mode="json") for item in relations],
    }
    content_hash = hashlib.sha256(
        json.dumps(payload_for_hash, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return ExternalGraph(
        sourceId="wikidata",
        externalEntityId=entity_id,
        fetchedAt=datetime.now(timezone.utc),
        # Wikidata 不提供数据版本号，这里如实为 null，不编一个
        dataVersion=None,
        contentHash=content_hash,
        entities=entities[:MAX_ENTITIES],
        relations=relations,
        mappingDecisions=[],
    )


_SOURCES = {"wikidata": _fetch_wikidata}


def fetch_graph(settings: Settings, source_id: str, entity_id: str) -> ExternalGraph:
    """按白名单抓取并解析。失败一律翻译成契约错误码。"""
    allowed = available_sources(settings)
    if source_id not in allowed or source_id not in _SOURCES:
        raise errors.knowledge_graph_unavailable(
            f"来源 {source_id} 未配置或不受支持",
            details={"sourceId": source_id, "available": allowed},
        )
    try:
        return _SOURCES[source_id](entity_id, settings)
    except _FetchFailure as failure:
        if failure.kind == "timeout":
            raise errors.knowledge_graph_timeout(failure.message) from failure
        if failure.kind == "not_found":
            raise errors.knowledge_graph_entity_not_found(
                failure.message, details={"sourceId": source_id, "externalEntityId": entity_id}
            ) from failure
        raise errors.knowledge_graph_unavailable(failure.message) from failure


# ---------------------------------------------------------------------------
# 映射：外部 → 站内
# ---------------------------------------------------------------------------


def _load_explicit_mapping(settings: Settings) -> dict[str, dict[str, Any]]:
    """读显式映射表。读不到就当没有——但不会因此静默降级成「猜」。"""
    raw_path = getattr(settings, "knowledge_graph_mapping_file", "") or ""
    if not raw_path.strip():
        return {}
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sources = data.get("sources") or {}
    return {source: entries for source, entries in sources.items() if isinstance(entries, dict)}


def _map_entities(
    db: Session, graph: ExternalGraph, settings: Settings | None = None
) -> tuple[list[str], list[MappingDecision]]:
    """把外部实体对到站内知识条目。

    优先级，从确定到宽松：

    1. **显式映射表**（``knowledge_graph_mapping_file``）——人工确认过的对应关系；
    2. **精确同名**；
    3. **站内别名精确等于外部标签**。

    刻意**不做**字符串包含或相似度匹配：「竹亚科」与「竹」、「梅」与「墨梅」
    看起来像，但靠相似度命中会让来源和知识对不上——这种对应关系该写进映射表，
    让人确认一次，而不是每次请求都猜一遍。
    """
    names = {entity.label for entity in graph.entities}
    explicit = _load_explicit_mapping(settings or Settings(_env_file=None)).get(
        graph.sourceId, {}
    )

    decisions: list[MappingDecision] = []
    matched_ids: list[str] = []

    # 1) 显式映射
    entry = explicit.get(graph.externalEntityId)
    if isinstance(entry, dict) and entry.get("knowledgeId"):
        row = db.get(KnowledgeModel, entry["knowledgeId"])
        if row is not None:
            matched_ids.append(row.id)
            reason = str(entry.get("reason") or "")
            decisions.append(
                MappingDecision(
                    kind="entity",
                    externalId=graph.externalEntityId,
                    target="knowledge-entity",
                    targetId=row.id,
                    affects="rule-selection",
                    reason=f"显式映射到「{row.name}」{('：' + reason) if reason else ''}"[:200],
                )
            )
            return matched_ids, decisions
        # 表里指到了一个不存在的条目：继续走兜底，但留下警告性的决策记录
        decisions.append(
            MappingDecision(
                kind="entity",
                externalId=graph.externalEntityId,
                target="knowledge-entity",
                targetId=None,
                affects="rule-selection",
                reason=f"显式映射指向的条目 {entry['knowledgeId']} 不存在，改用标签匹配",
            )
        )

    # 2) 精确同名 / 3) 别名精确
    for row in db.scalars(select(KnowledgeModel)).all():
        aliases: list[str] = []
        try:
            raw_aliases = json.loads(row.alias_json or "[]")
            if isinstance(raw_aliases, list):
                aliases = [str(item) for item in raw_aliases]
        except ValueError:
            aliases = []

        if row.name in names:
            how, label = "同名", row.name
        else:
            hit = next((alias for alias in aliases if alias in names), None)
            if hit is None:
                continue
            how, label = "别名", hit

        matched_ids.append(row.id)
        decisions.append(
            MappingDecision(
                kind="entity",
                externalId=graph.externalEntityId,
                target="knowledge-entity",
                targetId=row.id,
                affects="rule-selection",
                reason=f"外部标签「{label}」与站内条目「{row.name}」{how}，作为规则匹配依据",
            )
        )
    return matched_ids, decisions


@dataclass(frozen=True)
class FactFragment:
    """一条候选事实片段，带着它的来源，便于回溯到具体属性/关系。"""

    text: str
    kind: str  # "attribute" | "relation"
    externalId: str
    label: str


def _fact_fragments(graph: ExternalGraph) -> list[FactFragment]:
    """候选事实片段，按「越能说明这是什么」排序。

    属性在前（学名最能定位实物，其次分类、亲本、用途），关系在后。
    **关系也进候选**——否则「关系参与视觉决策」就只是抓来摆着看的空话。
    """
    fragments: list[FactFragment] = []
    entity = graph.entities[0] if graph.entities else None
    if entity is not None:
        for key in CAPTION_ATTRIBUTE_ORDER:
            for attr_key, value in entity.attributes.items():
                if attr_key.split("(")[0] != key:
                    continue
                if value:
                    fragments.append(
                        FactFragment(
                            text=value,
                            kind="attribute",
                            externalId=graph.externalEntityId,
                            label=key,
                        )
                    )
                break

    for relation in graph.relations:
        if not relation.label:
            continue
        fragments.append(
            FactFragment(
                text=f"{relation.type}：{relation.label}",
                kind="relation",
                externalId=relation.id,
                label=relation.label,
            )
        )
    return fragments


def _apply_caption_budget(
    base_caption: str,
    fragments: list[FactFragment],
    limit: int = generations_service.CAPTION_LIMIT,
) -> tuple[str, list[FactFragment], list[FactFragment]]:
    """按真实 caption 预算贪心装入片段，返回 (拼好的后缀, 已采纳, 未采纳)。

    预算必须扣掉站内规则自己的 caption 和分隔符，否则会出现「记成已采纳、
    实际被 200 字符截掉」。这正是评审指出的问题：旧实现只取前三个属性拼后缀，
    却把所有有 label 的关系都记成「写入叙事说明」。

    短片段即使排在长片段之后也会被试着装入（不 break），所以一条长事实不会
    把后面所有短事实一起挡掉。
    """
    separator = "｜"
    budget = limit - len(base_caption) - len(separator) if base_caption else limit

    adopted: list[FactFragment] = []
    skipped: list[FactFragment] = []
    used = 0
    for fragment in fragments:
        cost = len(fragment.text) + (len(separator) if adopted else 0)
        if used + cost > budget:
            skipped.append(fragment)
            continue
        adopted.append(fragment)
        used += cost

    return separator.join(item.text for item in adopted), adopted, skipped


def _adopted_decisions(adopted: list[FactFragment]) -> list[MappingDecision]:
    """只为**真正进了 caption** 的片段留决策记录。

    未采纳的片段不写进 mappingDecisions：这个列表的语义是「外部事实影响了
    什么」，没影响就不该有记录。抓回来的全集在 ``externalGraph.relations``
    里，仍然可查——所以「记了几条」永远小于等于「抓了几条」。
    """
    decisions: list[MappingDecision] = []
    for fragment in adopted:
        decisions.append(
            MappingDecision(
                kind="relation" if fragment.kind == "relation" else "attribute",
                externalId=fragment.externalId,
                target="caption",
                targetId=None,
                affects="caption",
                reason=f"{fragment.label}：写入叙事说明「{fragment.text}」"[:200],
            )
        )
    return decisions


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def generate_from_graph(
    db: Session,
    user: User,
    payload: GraphGenerationRequest,
    request_id: str,
    settings: Settings,
    *,
    enabled_backends: frozenset[str] | None = None,
) -> Any:
    """抓取 → 映射 → 生成不可变快照。

    复用 ``generations.create_generation``：校验、落库、规则/实体快照全都一样，
    只是多带一份 ``external_graph`` 进 provenance。
    """
    enabled = (
        enabled_backends
        if enabled_backends is not None
        else capabilities.enabled_backends(settings)
    )
    # 能力闸门放在最前面：请求里已经声明了后端，没必要先花 6 秒抓网络
    capabilities.require_enabled(payload.backend, enabled)

    graph = fetch_graph(settings, payload.sourceId, payload.externalEntityId)

    matched_ids, decisions = _map_entities(db, graph, settings)
    if not matched_ids:
        raise errors.no_semantic_match(
            "外部实体没有映射到任何站内知识条目",
            details={
                "externalEntityId": graph.externalEntityId,
                "labels": [entity.label for entity in graph.entities],
            },
        )

    fragments = _fact_fragments(graph)

    # 走语义生成：文本用外部实体的标签与事实，实体用映射到的站内条目
    text_parts = [entity.label for entity in graph.entities]
    if fragments:
        text_parts.append("｜".join(item.text for item in fragments))
    semantic_payload = SemanticGeneration(
        mode="semantic",
        input=SemanticInput(text=" ".join(text_parts)[:2000], entityIds=matched_ids),
        params={},
        backend=payload.backend,
        seed=payload.seed,
    )

    # **先按真实 caption 算出预算**，再决定哪些事实真的进得去。
    # 必须用与生成完全相同的匹配/窄化/synthesize，所以共用 resolve_semantic；
    # 自己另写一遍就会漂移，而漂移的后果正是「记成已采纳、实际被截断」。
    _, synth = generations_service.resolve_semantic(
        db, semantic_payload.input.text, matched_ids, matched_ids
    )
    suffix, adopted, skipped = _apply_caption_budget(synth.caption, fragments)
    decisions.extend(_adopted_decisions(adopted))

    return generations_service.create_generation(
        db,
        user,
        semantic_payload,
        request_id,
        enabled_backends=enabled,
        external_graph=graph.model_copy(
            update={"mappingDecisions": decisions}
        ).model_dump(mode="json"),
        # 外部事实追加进叙事说明。这是**叙事联动**：caption 是 IR 叙事节点的
        # 文本，当前 Canvas 渲染器不把它绘进 PNG，页面题记会展示它。
        # 不要据此宣称「外部关系改变了画面像素」。
        caption_suffix=suffix or None,
        # 只让「针对这个实体」的规则参与：否则 priority 更高的多实体叙事规则
        # 会把画面主体抢走（竹生成出来是莲的叙事）
        prefer_entity_ids=matched_ids,
    )

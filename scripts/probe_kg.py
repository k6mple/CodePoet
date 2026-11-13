"""K1 探针：Wikidata 能不能提供「可映射到站内知识」的实体事实。

G-RESOURCE 门槛要的不是「域名能通」，而是这条链能成立：

    中文意象（莲/竹/梅） → Wikidata 实体 → 属性 + 一跳关系 → 映射到站内知识/规则

所以探针分三步，每步都记录真实响应：

1. `wbsearchentities` 按中文关键词找实体，看排名第一的是不是我们要的那个；
2. `Special:EntityData/{Q}.json` 拿标签、描述、claims；
3. `query.wikidata.org` SPARQL 拿一跳关系，确认关系不是空壳。

只读公开接口，不带凭据，不写任何数据。
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / ".smoke" / "probe-kg"  # 项目内隔离目录
OUT.mkdir(parents=True, exist_ok=True)

UA = "CodePoet-K1-Probe/0.1 (contact: local dev)"
TERMS = ["莲", "竹", "梅"]

report: list[str] = []


def note(line: str = "") -> None:
    print(line)
    report.append(line)


def fetch_json(url: str, timeout: int = 25) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def search(term: str) -> list[dict]:
    query = urllib.parse.urlencode({
        "action": "wbsearchentities",
        "search": term,
        "language": "zh",
        "uselang": "zh",
        "format": "json",
        "limit": 3,
    })
    data = fetch_json(f"https://www.wikidata.org/w/api.php?{query}")
    return data.get("search", [])


def entity(qid: str) -> dict:
    data = fetch_json(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    return data["entities"][qid]


def relations(qid: str, limit: int = 25) -> list[dict]:
    """一跳关系：谁指向我 / 我指向谁，只要实体类目标。"""
    sparql = f"""
SELECT ?prop ?propLabel ?target ?targetLabel WHERE {{
  {{ wd:{qid} ?prop ?target . }}
  UNION
  {{ ?target ?prop wd:{qid} . }}
  FILTER(STRSTARTS(STR(?prop), "http://www.wikidata.org/prop/direct/"))
  FILTER(STRSTARTS(STR(?target), "http://www.wikidata.org/entity/Q"))
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh,en". }}
}}
LIMIT {limit}
"""
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode(
        {"query": sparql, "format": "json"}
    )
    data = fetch_json(url)
    return data["results"]["bindings"]


def main() -> int:
    note("# K1 探针：Wikidata 实体事实")
    note(f"环境：{OUT}")
    note()

    summary = []
    for term in TERMS:
        note(f"## 关键词「{term}」")
        started = time.perf_counter()
        try:
            hits = search(term)
        except Exception as exc:  # noqa: BLE001
            note(f"  FAIL 搜索失败：{type(exc).__name__}: {exc}")
            summary.append((term, 0, 0, 0, "search-failed"))
            continue
        elapsed = (time.perf_counter() - started) * 1000
        note(f"  搜索返回 {len(hits)} 条 / {elapsed:.0f}ms")
        if not hits:
            summary.append((term, 0, 0, 0, "no-hit"))
            continue

        top = hits[0]
        qid = top["id"]
        note(f"  首选：{qid}  {top.get('label', '')} —— {top.get('description', '')[:60]}")
        if len(hits) > 1:
            note(f"  次选：{hits[1]['id']}  {hits[1].get('label', '')}")

        try:
            data = entity(qid)
        except Exception as exc:  # noqa: BLE001
            note(f"  FAIL 取实体失败：{type(exc).__name__}: {exc}")
            summary.append((term, 0, 0, 0, "entity-failed"))
            continue

        labels = data.get("labels", {})
        zh_label = (labels.get("zh") or labels.get("zh-cn") or {}).get("value")
        en_label = (labels.get("en") or {}).get("value")
        desc = (data.get("descriptions", {}).get("zh") or {}).get("value")
        claims = data.get("claims", {})
        note(f"  标签：zh={zh_label!r} en={en_label!r}")
        note(f"  描述：{(desc or '')[:80]}")
        note(f"  claims 属性数：{len(claims)}")

        # 挑几个对我们有意义的属性：分类学、象征、用途
        interesting = []
        for pid, values in claims.items():
            for value in values:
                mainsnak = value.get("mainsnak", {})
                if mainsnak.get("snaktype") != "value":
                    continue
                datavalue = mainsnak.get("datavalue", {})
                if datavalue.get("type") == "wikibase-entityid":
                    interesting.append((pid, datavalue["value"]["id"]))
                elif datavalue.get("type") == "string":
                    interesting.append((pid, str(datavalue["value"])[:40]))
        note(f"  有值 claims：{len(interesting)} 条，前 8：{interesting[:8]}")

        try:
            rels = relations(qid)
        except Exception as exc:  # noqa: BLE001
            note(f"  SPARQL 关系失败：{type(exc).__name__}: {exc}")
            rels = []
        note(f"  一跳关系：{len(rels)} 条")
        for rel in rels[:5]:
            note(
                f"    {rel.get('propLabel', {}).get('value', rel['prop']['value'].rsplit('/', 1)[-1])}"
                f" → {rel.get('targetLabel', {}).get('value', rel['target']['value'].rsplit('/', 1)[-1])}"
            )

        summary.append((term, len(claims), len(interesting), len(rels), "ok"))
        note()

    note("## 汇总")
    note("| 关键词 | claims | 有值 | 一跳关系 | 结论 |")
    note("| --- | --- | --- | --- | --- |")
    for term, claims_count, values_count, rel_count, status in summary:
        note(f"| {term} | {claims_count} | {values_count} | {rel_count} | {status} |")

    ok = [row for row in summary if row[4] == "ok" and row[2] >= 5 and row[3] >= 5]
    note()
    if len(ok) >= 3:
        note("## 判定：来源可用（3 个实体都有 ≥5 条有值 claim 与 ≥5 条一跳关系）")
        note("门槛通过条件：3 实体 + 2 关系端到端；这里已有 3 实体 × 多关系。")
        verdict = 0
    else:
        note(f"## 判定：来源不足（只有 {len(ok)}/3 个实体达标）")
        verdict = 2

    OUT.joinpath("report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"\n报告：{OUT / 'report.md'}")
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())

"""有限 JSON 包预校验、事务导入与备份导出。

core-demo 的取舍（契约明写）：

* 按类型**整库**导入导出，不支持按 ids 挑、不支持智能合并
* **重复 ID 整批拒绝**——不做「存在就跳过」，因为静默跳过会让用户以为
  导入了 100 条、实际只有 20 条
* **模板一律落 draft**：导入的模板没经过审核，不能直接进公开库
* 包体与结果都不得超过 5MiB

三个函数对应三个接口，流程是「校验拿凭证 → 用凭证执行」：

    validate_import  → ImportValidation（10 分钟、绑定用户、单次消费）
    apply_import     → 真正写库（事务，失败则一行不写）

凭证里存整份包体：执行时不再信任客户端重发的版本。否则中间被换掉，
hash 就对不上——校验过的和实际导入的会是两份东西。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.models.creations import Generation as GenerationModel
from app.models.knowledge import Concept as ConceptModel
from app.models.knowledge import KnowledgeEntity as KnowledgeModel
from app.models.knowledge import Tag as TagModel
from app.models.operations import DataMeta
from app.models.operations import ImportValidation as ValidationModel
from app.models.rules import MappingRule as RuleModel
from app.models.templates import Template as TemplateModel
from app.models.templates import TemplateVersion as VersionModel
from app.models.users import User
from app.models.works import Artifact as ArtifactModel
from app.models.works import RenderJob as RenderJobModel
from app.models.works import Work as WorkModel
from app.models.works import WorkVersion as WorkVersionModel
from app.schemas.operations import (
    BackupPackage,
    CoreImportPackage,
    ImportApplied,
    ImportApply,
    ImportManifest,
    ImportValidation,
    ManifestCounts,
    MAX_PACKAGE_BYTES,
)
from app.engine.validate import validate_ir
from app.schemas.knowledge import KnowledgeEntity as KnowledgeEntitySchema
from app.schemas.rules import MappingRule as MappingRuleSchema
from app.schemas.taxonomy import Concept as ConceptSchema
from app.schemas.taxonomy import Tag as TagSchema
from app.schemas.templates import Template as TemplateSchema
from app.schemas.templates import TemplateVersion as TemplateVersionSchema
from app.schemas.validation import Issue
from app.settings import Settings
from app.services import audit
from app.services import knowledge as knowledge_service
from app.services import permissions
from app.services import rules as rules_service
from app.services import taxonomy
from app.services import templates as templates_service
from app.services import uploads
from app.services import works as works_service
from app.services.generations import build_generation_schema

__all__ = ["apply_import", "export_backup", "validate_import"]

# 契约：validationId 10 分钟有效
VALIDATION_TTL_SECONDS = 600

_IMPORTABLE = ("concepts", "tags", "entities", "rules", "templates")
# 导出覆盖契约里的全部六类。导入只支持能完整还原的四类：
# 契约写明 core-demo 不支持 workSnapshots 导入，作品快照是留档备份，
# 恢复图片与公开状态不在范围内。
_EXPORTABLE = ("concepts", "tags", "entities", "rules", "templates", "workSnapshots")
# 契约 data.workSnapshots 的 maxItems；超了要明确报错，不能截断成假备份
_MAX_SNAPSHOTS = 1000


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _issue(code: str, message: str, path: str | None = None) -> Issue:
    return Issue(code=code, message=message, path=path, nodeIds=[])


def _require_permission(actor: User, permission: str) -> None:
    if not permissions.has_permission(actor, permission):
        raise errors.forbidden("需要管理员权限")


def _data_revision(db: Session) -> int:
    row = db.get(DataMeta, 1)
    return row.data_revision if row else 0


def _bump_data_revision(db: Session) -> int:
    row = db.get(DataMeta, 1)
    if row is None:
        row = DataMeta(id=1, data_revision=1)
        db.add(row)
    else:
        row.data_revision += 1
    db.flush()
    return row.data_revision


def _canonical_hash(payload: dict) -> str:
    """对规范化 JSON 求 sha256。

    ``sort_keys`` 是关键：同一份内容换个键顺序不该产生不同 hash，
    否则客户端重传一次就被判成「内容变了」。
    """
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def _artifact_missing(db: Session, settings: Settings | None, job_id: str) -> bool:
    """这一版那次渲染的产物还在不在。

    不能只看 ``artifacts`` 里有没有行：行还在、文件被清掉的情况在生产里
    很常见（换机器、清理磁盘）。备份包既然不含 PNG，就必须如实告诉使用者
    「这一条将来能不能直接复用」——用「有没有记录」代理「文件在不在」，
    等于给一个会骗人的备份。
    """
    storage_path = db.scalar(
        select(ArtifactModel.storage_path)
        .where(ArtifactModel.render_job_id == job_id)
        .order_by(ArtifactModel.time_ms)
        .limit(1)
    )
    if not storage_path:
        return True
    if settings is None:
        # 拿不到数据目录时只能按记录判断，明确退化为「记录在即认为在」
        return False
    try:
        return not uploads.artifact_path(settings, storage_path).exists()
    except Exception:  # noqa: BLE001 —— 库路径被手工改坏时按缺失处理
        return True


def _export_work_snapshots(
    db: Session, actor: User, settings: Settings | None
) -> list[dict]:
    """每个作品版本一条：作品 + 版本 + 生成快照 + 渲染任务。

    ``missingArtifacts`` 如实说明这一版那次渲染的产物还在不在：备份包本身
    不含 PNG（``includesArtifacts`` 恒 false），产物被清过的条目将来只能重渲染。
    把这件事写在数据里，比在文档里写一句「不支持」更不容易被忽略。
    """
    versions = db.scalars(
        select(WorkVersionModel)
        .join(WorkModel, WorkModel.id == WorkVersionModel.work_id)
        .order_by(WorkModel.created_at, WorkVersionModel.version_number)
    ).all()
    if len(versions) > _MAX_SNAPSHOTS:
        raise errors.ApiError(
            413,
            "PACKAGE_TOO_LARGE",
            f"作品版本 {len(versions)} 条，超过 {_MAX_SNAPSHOTS} 条上限；"
            "请改导其他类型后分批留档",
        )

    items: list[dict] = []
    for version in versions:
        work = db.get(WorkModel, version.work_id)
        generation = db.get(GenerationModel, version.generation_id)
        job = (
            db.get(RenderJobModel, version.render_job_id)
            if version.render_job_id
            else None
        )
        if work is None or generation is None or job is None:
            # 少一条等于备份在骗人：宁可明确失败，也不要交出一个有洞的包
            raise errors.ApiError(
                500,
                "BACKUP_DATA_CORRUPT",
                f"作品版本 {version.id} 的关联数据不完整，无法导出备份",
            )
        missing = _artifact_missing(db, settings, job.id)
        items.append(
            {
                "work": works_service.build_work(db, work, actor).model_dump(mode="json"),
                "version": works_service.build_version(version).model_dump(mode="json"),
                "generation": build_generation_schema(generation).model_dump(mode="json"),
                "renderJob": works_service.build_render_job(job).model_dump(mode="json"),
                "missingArtifacts": missing,
            }
        )
    return items


def export_backup(
    db: Session, actor: User, types: list[str], settings: Settings | None = None
) -> BackupPackage:
    """按类型导出整库。

    ``types`` 覆盖契约里的全部六类。导出的东西不一定都导得回去：
    契约明说 core-demo 不支持 workSnapshots 导入——作品快照是留档备份，
    恢复图片与公开状态不在范围内。所以包里带 ``missingArtifacts``，
    让使用者知道「备份里没有图」这件事，而不是以为能一键还原。
    """
    _require_permission(actor, permissions.EXPORTS_CREATE)

    data: dict[str, list[dict]] = {key: [] for key in _EXPORTABLE}

    if "concepts" in types:
        rows = db.scalars(
            select(ConceptModel).order_by(ConceptModel.created_at, ConceptModel.id)
        ).all()
        # 复用分类服务的序列化，避免两套字段定义各自漂移
        data["concepts"] = [
            taxonomy._build_concept(row).model_dump(mode="json")  # noqa: SLF001
            for row in rows
        ]

    if "tags" in types:
        rows = db.scalars(
            select(TagModel).order_by(TagModel.created_at, TagModel.id)
        ).all()
        parents = taxonomy.parent_map(db, TagModel)
        conflicts = taxonomy.conflict_map(db)
        data["tags"] = [
            taxonomy._build_tag(row, parents, conflicts).model_dump(mode="json")  # noqa: SLF001
            for row in rows
        ]

    if "entities" in types:
        rows = db.scalars(
            select(KnowledgeModel).order_by(KnowledgeModel.created_at, KnowledgeModel.id)
        ).all()
        tag_parents = knowledge_service._tag_tree(db)  # noqa: SLF001
        data["entities"] = [
            knowledge_service._build_entity(row, tag_parents).model_dump(mode="json")  # noqa: SLF001
            for row in rows
        ]

    if "templates" in types:
        rows = db.scalars(
            select(TemplateModel).order_by(TemplateModel.created_at, TemplateModel.id)
        ).all()
        items: list[dict] = []
        for row in rows:
            published = (
                db.get(VersionModel, row.published_version_id)
                if row.published_version_id
                else None
            )
            versions = db.scalars(
                select(VersionModel)
                .where(VersionModel.template_id == row.id)
                .order_by(VersionModel.version_number)
            ).all()
            items.append(
                {
                    "template": templates_service.build_template(
                        row, published, actor
                    ).model_dump(mode="json"),
                    "versions": [
                        templates_service.build_version(version).model_dump(mode="json")
                        for version in versions
                    ],
                }
            )
        data["templates"] = items

    if "rules" in types:
        # 按优先级导：映射规则是「先命中先算」的，顺序变了语义就变了
        rows = db.scalars(
            select(RuleModel).order_by(
                RuleModel.priority, RuleModel.created_at, RuleModel.id
            )
        ).all()
        data["rules"] = [
            rules_service._build(row).model_dump(mode="json")  # noqa: SLF001
            for row in rows
        ]

    if "workSnapshots" in types:
        data["workSnapshots"] = _export_work_snapshots(db, actor, settings)

    counts = ManifestCounts(
        concepts=len(data["concepts"]),
        tags=len(data["tags"]),
        entities=len(data["entities"]),
        rules=len(data["rules"]),
        templates=len(data["templates"]),
        workSnapshots=len(data["workSnapshots"]),
    )

    package = {
        "schemaVersion": 1.0,
        "exportedAt": _now_iso(),
        "manifest": {"counts": counts.model_dump(), "includesArtifacts": False},
        "data": data,
    }

    size = len(json.dumps(package, ensure_ascii=False).encode("utf-8"))
    if size > MAX_PACKAGE_BYTES:
        raise errors.ApiError(
            413,
            "PACKAGE_TOO_LARGE",
            f"导出结果 {size} 字节，超过 {MAX_PACKAGE_BYTES} 字节上限；请分类型导出",
        )

    return BackupPackage.model_validate(package)


# ---------------------------------------------------------------------------
# 预校验
# ---------------------------------------------------------------------------


def _ids_exist_in_db(db: Session, model, ids: set[str]) -> set[str]:
    if not ids:
        return set()
    rows = db.scalars(select(model.id).where(model.id.in_(list(ids)))).all()
    return set(rows)


def _all_ids(db: Session, model) -> set[str]:
    """库里某张表的全部 ID。

    引用校验要判断「这个 parentId 能不能解析到」，包内和库里都算数。
    只查包内会让「引用已有标签」被误报成悬空引用。
    """
    return set(db.scalars(select(model.id)).all())


def _check_duplicates(
    db: Session, model, items: list[dict], label: str, errors: list[Issue]
) -> set[str]:
    """返回包内 ID 集合，同时把「包内重复」和「与库冲突」都记进 errors。"""
    seen: set[str] = set()
    duplicated: set[str] = set()
    for index, item in enumerate(items):
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            errors.append(_issue("INVALID_ID", f"{label}[{index}] 缺少有效的 id", f"data.{label}[{index}].id"))
            continue
        if item_id in seen:
            duplicated.add(item_id)
        seen.add(item_id)
    for item_id in sorted(duplicated):
        errors.append(
            _issue("DUPLICATE_ID", f"{label} 中 id「{item_id}」重复；重复 ID 整批拒绝", f"data.{label}")
        )

    existing = _ids_exist_in_db(db, model, seen)
    for item_id in sorted(existing):
        errors.append(
            _issue(
                "ID_CONFLICT",
                f"{label} 中 id「{item_id}」已存在于数据库；core-demo 不支持合并导入",
                f"data.{label}",
            )
        )
    return seen


def _validate_shape(items: list[dict], label: str, model, errors: list[Issue]) -> None:
    """逐条按契约 schema 校验字段。

    只查 id 和 parentId 是不够的：一个 tag 少了 name、或者 status 写了
    「deleted」，等到 insert 时才炸，那时事务已经开了，报错也看不出是
    第几条。这里提前定位到下标。
    """
    for index, item in enumerate(items):
        try:
            model.model_validate(item)
        except Exception as exc:  # noqa: BLE001 - Pydantic 的错误类型随版本变
            first = str(exc).splitlines()[0][:200]
            errors.append(
                _issue(
                    "INVALID_SHAPE",
                    f"{label}[{index}] 结构不合法：{first}",
                    f"data.{label}[{index}]",
                )
            )


def _validate_template_structure(items: list[dict], errors: list[Issue]) -> None:
    """按契约 schema 逐条校验模板容器与它的每个版本，含 IR 跨字段校验。

    之前这里只查了 version 的 id 存在且不重复。一个只有 id 的版本能通过
    校验、apply 落库，之后 GET /template-versions/{id} 因为缺 metadata /
    irTemplate 直接 500——坏包没有被拒绝，而是污染了库。这个接口的承诺是
    「不合格的包一行都不写」，那就必须把结构验到底。
    """
    for index, item in enumerate(items):
        template = item.get("template") or {}
        try:
            TemplateSchema.model_validate(template)
        except Exception as exc:  # noqa: BLE001 - Pydantic 的错误类型随版本变
            errors.append(
                _issue(
                    "INVALID_TEMPLATE",
                    f"templates[{index}].template 结构不合法："
                    f"{str(exc).splitlines()[0][:200]}",
                    f"data.templates[{index}].template",
                )
            )

        for vidx, version in enumerate(item.get("versions") or []):
            path = f"data.templates[{index}].versions[{vidx}]"
            try:
                parsed = TemplateVersionSchema.model_validate(version)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    _issue(
                        "INVALID_TEMPLATE_VERSION",
                        f"templates[{index}].versions[{vidx}] 结构不合法："
                        f"{str(exc).splitlines()[0][:200]}",
                        path,
                    )
                )
                continue

            # 版本必须挂在它声明的模板下，否则导入后是个孤儿版本：
            # 模板页永远看不到它，而它自己又占着 ID
            template_id = template.get("id")
            if template_id and parsed.templateId != template_id:
                errors.append(
                    _issue(
                        "INVALID_TEMPLATE_VERSION",
                        f"版本「{parsed.id}」的 templateId 是 {parsed.templateId}，"
                        f"与所在模板「{template_id}」不一致",
                        f"{path}.templateId",
                    )
                )

            # IR 再过一遍跨字段校验：DrawingIR 只能保证字段形状，
            # 节点 id 重复、父引用悬空这类问题要到 validate_ir 才看得出来
            report = validate_ir(parsed.irTemplate)
            for issue in report.issues:
                if issue.code == "IR_OUT_OF_BOUNDS":
                    continue  # 越界不阻断，和作品保存的判定保持一致
                errors.append(
                    _issue(
                        issue.code,
                        f"版本「{parsed.id}」的 IR 不合法：{issue.message}",
                        f"{path}.irTemplate.{issue.path}"
                        if issue.path
                        else f"{path}.irTemplate",
                    )
                )


def _check_parent_refs(
    items: list[dict], label: str, known: set[str], errors: list[Issue]
) -> None:
    """父 ID 必须能在包内或库里找到，否则建出来的树是断的。"""
    for index, item in enumerate(items):
        parent = item.get("parentId")
        if parent is None:
            continue
        if parent not in known:
            errors.append(
                _issue(
                    "MISSING_REFERENCE",
                    f"{label}[{index}] 的 parentId「{parent}」在包内和库里都不存在",
                    f"data.{label}[{index}].parentId",
                )
            )


def _check_list_refs(
    items: list[dict], label: str, field: str, known: set[str], errors: list[Issue]
) -> None:
    for index, item in enumerate(items):
        values = item.get(field) or []
        if not isinstance(values, list):
            continue
        for value in values:
            if value not in known:
                errors.append(
                    _issue(
                        "MISSING_REFERENCE",
                        f"{label}[{index}] 的 {field} 引用了不存在的「{value}」",
                        f"data.{label}[{index}].{field}",
                    )
                )


def validate_import(
    db: Session, actor: User, package: CoreImportPackage
) -> ImportValidation:
    """结构与引用全过一遍，通过才允许 apply。"""
    _require_permission(actor, permissions.IMPORTS_APPLY)
    errors_list: list[Issue] = []

    payload = package.model_dump(mode="json")
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > MAX_PACKAGE_BYTES:
        raise errors.ApiError(
            413,
            "PACKAGE_TOO_LARGE",
            f"包体 {size} 字节，超过 {MAX_PACKAGE_BYTES} 字节上限",
        )

    if package.data.workSnapshots:
        errors_list.append(
            _issue(
                "UNSUPPORTED_TYPE",
                "core-demo 不支持导入 workSnapshots；作品快照是留档备份，"
                "恢复图片与公开状态不在范围内",
                "data.workSnapshots",
            )
        )

    # manifest 声明与实际条数必须一致：对不上说明包被手工改过，
    # 而导出方和导入方对「应该有多少条」的理解已经分裂了
    for key in _IMPORTABLE:
        declared = getattr(package.manifest.counts, key)
        actual = len(getattr(package.data, key))
        if declared != actual:
            errors_list.append(
                _issue(
                    "COUNT_MISMATCH",
                    f"manifest 声明 {key} 有 {declared} 条，实际 {actual} 条",
                    f"manifest.counts.{key}",
                )
            )

    concept_ids = _check_duplicates(db, ConceptModel, package.data.concepts, "concepts", errors_list)
    tag_ids = _check_duplicates(db, TagModel, package.data.tags, "tags", errors_list)
    entity_ids = _check_duplicates(db, KnowledgeModel, package.data.entities, "entities", errors_list)

    # 模板在包里是 {template, versions}，单独处理 ID
    template_ids: set[str] = set()
    for index, item in enumerate(package.data.templates):
        template = item.get("template") or {}
        template_id = template.get("id")
        if not isinstance(template_id, str) or not template_id:
            errors_list.append(
                _issue("INVALID_ID", f"templates[{index}] 缺少 template.id", f"data.templates[{index}].template.id")
            )
            continue
        if template_id in template_ids:
            errors_list.append(
                _issue("DUPLICATE_ID", f"templates 中 id「{template_id}」重复", "data.templates")
            )
        template_ids.add(template_id)
        metadata = template.get("metadata") or {}
        if not str(metadata.get("name") or "").strip():
            errors_list.append(
                _issue(
                    "INVALID_TEMPLATE",
                    f"templates[{index}]「{template_id}」的 metadata.name 为空",
                    f"data.templates[{index}].template.metadata.name",
                )
            )
        versions = item.get("versions") or []
        if not versions:
            errors_list.append(
                _issue(
                    "INVALID_TEMPLATE",
                    f"templates[{index}]「{template_id}」没有任何版本",
                    f"data.templates[{index}].versions",
                )
            )
        seen_versions: set[str] = set()
        for vidx, version in enumerate(versions):
            version_id = version.get("id")
            if not version_id:
                errors_list.append(
                    _issue(
                        "INVALID_ID",
                        f"templates[{index}].versions[{vidx}] 缺少 id",
                        f"data.templates[{index}].versions[{vidx}].id",
                    )
                )
            elif version_id in seen_versions:
                errors_list.append(
                    _issue(
                        "DUPLICATE_ID",
                        f"templates[{index}] 内版本 id「{version_id}」重复",
                        f"data.templates[{index}].versions",
                    )
                )
            else:
                seen_versions.add(version_id)

    conflicts = _ids_exist_in_db(db, TemplateModel, template_ids)
    for template_id in sorted(conflicts):
        errors_list.append(
            _issue(
                "ID_CONFLICT",
                f"templates 中 id「{template_id}」已存在于数据库；不支持合并导入",
                "data.templates",
            )
        )

    # 结构校验先于引用校验：字段都不全的条目，讨论引用没有意义
    _validate_shape(package.data.tags, "tags", TagSchema, errors_list)
    _validate_shape(package.data.concepts, "concepts", ConceptSchema, errors_list)
    _validate_shape(package.data.entities, "entities", KnowledgeEntitySchema, errors_list)
    _validate_template_structure(package.data.templates, errors_list)

    # 引用检查：包内的 + 库内已有的，都算可解析
    all_tags = tag_ids | _all_ids(db, TagModel)
    all_concepts = concept_ids | _all_ids(db, ConceptModel)
    _check_parent_refs(package.data.tags, "tags", all_tags, errors_list)
    _check_parent_refs(package.data.concepts, "concepts", all_concepts, errors_list)

    # 知识条目引用的标签/概念，要能在包内或库里解析到
    _check_list_refs(package.data.entities, "entities", "tagIds", all_tags, errors_list)
    _check_list_refs(package.data.entities, "entities", "conceptIds", all_concepts, errors_list)

    # 映射规则：结构 + 主键 + 命中条件里的引用。规则被引用错了不会报错，
    # 只会「看起来配了规则但一条都命中不了」，比直接失败更难查。
    _check_duplicates(db, RuleModel, package.data.rules, "rules", errors_list)
    _validate_shape(package.data.rules, "rules", MappingRuleSchema, errors_list)
    all_entities = entity_ids | _all_ids(db, KnowledgeModel)
    for index, item in enumerate(package.data.rules):
        match = item.get("match") or {}
        for field, known in (
            ("entityIds", all_entities),
            ("conceptIds", all_concepts),
            ("tagIds", all_tags),
        ):
            for ref in match.get(field) or []:
                if ref in known:
                    continue
                errors_list.append(
                    _issue(
                        "UNRESOLVED_REFERENCE",
                        f"rules[{index}]「{item.get('name')}」的 {field} "
                        f"引用了不存在的「{ref}」",
                        f"data.rules[{index}].match.{field}",
                    )
                )

    valid = not errors_list
    validation_id = _new_id("impv")
    expires_at = _now() + timedelta(seconds=VALIDATION_TTL_SECONDS)
    counts = ManifestCounts(
        concepts=len(package.data.concepts),
        tags=len(package.data.tags),
        entities=len(package.data.entities),
        rules=len(package.data.rules),
        templates=len(package.data.templates),
        workSnapshots=len(package.data.workSnapshots),
    )

    db.add(
        ValidationModel(
            id=validation_id,
            actor_id=actor.id,
            content_hash=_canonical_hash(payload),
            expected_data_revision=_data_revision(db),
            valid=1 if valid else 0,
            # 存整份包体：apply 时不再信任客户端重发的版本
            package_json=json.dumps(payload, ensure_ascii=False),
            counts_json=json.dumps(counts.model_dump(), ensure_ascii=False),
            expires_at=expires_at.isoformat(timespec="seconds"),
            consumed_at=None,
            created_at=_now_iso(),
        )
    )
    audit.record(
        db,
        actor=actor,
        action="import.validate",
        target_type="import",
        target_id=validation_id,
        input_summary={"valid": valid, "errorCount": len(errors_list)},
        result="success" if valid else "failure",
        error_code=None if valid else "IMPORT_INVALID",
    )
    db.commit()

    return ImportValidation(
        validationId=validation_id,
        valid=valid,
        contentHash=_canonical_hash(payload),
        expectedDataRevision=_data_revision(db),
        expiresAt=expires_at,
        counts=counts,
        errors=errors_list[:100],
    )


# ---------------------------------------------------------------------------
# 执行导入
# ---------------------------------------------------------------------------


def _insert_tags(db: Session, items: list[dict], actor: User) -> None:
    """按父子依赖顺序插入。

    包里的数组顺序不保证父在前，所以多轮扫描：每轮插入「父已就位」的那些，
    一轮下来一条都插不进去就说明有环，直接报错——不进入死循环。
    """
    pending = {item["id"]: item for item in items}
    now = _now_iso()
    progress = True
    while pending and progress:
        progress = False
        for tag_id in list(pending):
            item = pending[tag_id]
            parent = item.get("parentId")
            if parent is not None and parent in pending:
                continue
            db.add(
                TagModel(
                    id=tag_id,
                    name=str(item.get("name") or "")[:80],
                    description=str(item.get("description") or ""),
                    parent_id=parent,
                    status=str(item.get("status") or "active"),
                    conflicts_with_json=json.dumps(item.get("conflictsWith") or [], ensure_ascii=False),
                    owner_id=actor.id,
                    revision=int(item.get("revision") or 1),
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            del pending[tag_id]
            progress = True
    if pending:
        raise errors.validation_error(
            f"标签父子关系存在环，无法建立层级：{sorted(pending)[:5]}"
        )


def _insert_concepts(db: Session, items: list[dict], actor: User) -> None:
    pending = {item["id"]: item for item in items}
    now = _now_iso()
    progress = True
    while pending and progress:
        progress = False
        for concept_id in list(pending):
            item = pending[concept_id]
            parent = item.get("parentId")
            if parent is not None and parent in pending:
                continue
            db.add(
                ConceptModel(
                    id=concept_id,
                    name=str(item.get("name") or "")[:80],
                    description=str(item.get("description") or ""),
                    parent_id=parent,
                    status=str(item.get("status") or "active"),
                    owner_id=actor.id,
                    revision=int(item.get("revision") or 1),
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            del pending[concept_id]
            progress = True
    if pending:
        raise errors.validation_error(
            f"本体父子关系存在环，无法建立层级：{sorted(pending)[:5]}"
        )


def _insert_entities(db: Session, items: list[dict], actor: User) -> None:
    now = _now_iso()
    for item in items:
        db.add(
            KnowledgeModel(
                id=item["id"],
                type=str(item.get("type") or "concept"),
                name=str(item.get("name") or "")[:80],
                alias_json=json.dumps(item.get("aliases") or [], ensure_ascii=False),
                summary=str(item.get("summary") or ""),
                content=str(item.get("content") or ""),
                source_references_json=json.dumps(
                    item.get("sourceReferences") or [], ensure_ascii=False
                ),
                concept_ids_json=json.dumps(item.get("conceptIds") or [], ensure_ascii=False),
                tag_ids_json=json.dumps(item.get("tagIds") or [], ensure_ascii=False),
                relations_json=json.dumps(item.get("relations") or [], ensure_ascii=False),
                time_theme=item.get("timeTheme"),
                status=str(item.get("status") or "active"),
                owner_id=actor.id,
                revision=int(item.get("revision") or 1),
                created_at=now,
                updated_at=now,
            )
        )
    db.flush()


def _insert_rules(db: Session, items: list[dict], actor: User) -> None:
    """还原映射规则，保留包内主键。

    不用 ``rules_service.create_rule``：那个函数会生成新 id 并逐条 commit。
    备份还原要求 id 稳定（规则可能被别处引用），且要和其余类型同处一个事务。
    """
    now = _now_iso()
    for item in items:
        db.add(
            RuleModel(
                id=item["id"],
                name=str(item.get("name") or "")[:80],
                enabled=1 if item.get("enabled", True) else 0,
                priority=int(item.get("priority") or 0),
                match_json=json.dumps(item.get("match") or {}, ensure_ascii=False),
                effects_json=json.dumps(item.get("effects") or {}, ensure_ascii=False),
                explanation=str(item.get("explanation") or "")[:500],
                version=int(item.get("version") or 1),
                revision=int(item.get("revision") or 1),
                owner_id=actor.id,
                created_at=now,
                updated_at=now,
            )
        )
    db.flush()


def _insert_templates(db: Session, items: list[dict], actor: User) -> None:
    """模板与版本一起插入，**状态一律 draft**。

    契约明确：导入的模板没经过审核，不能直接进公开库。
    导入包里即使写着 published 也不采纳——那是包制造者的历史，
    不是这个环境里的审核结论。
    """
    now = _now_iso()
    for item in items:
        template = item["template"]
        template_id = template["id"]
        # 契约的 Template 没有顶层 name：名称在 metadata 里。
        # 按顶层取会插进空字符串，撞上 templates.name 的 CHECK 约束。
        metadata = template.get("metadata") or {}
        working = template.get("workingMetadata") or metadata
        name = str(metadata.get("name") or working.get("name") or "")[:120]
        db.add(
            TemplateModel(
                id=template_id,
                owner_id=actor.id,
                name=name,
                description=str(metadata.get("description") or ""),
                status="draft",
                category=template.get("category"),
                published_version_id=None,
                latest_version_id=None,
                working_metadata_json=json.dumps(working, ensure_ascii=False),
                supported_backends_json=json.dumps(
                    template.get("supportedBackends") or ["canvas2d"], ensure_ascii=False
                ),
                thumbnail_artifact_id=None,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()

        for version in item.get("versions") or []:
            db.add(
                VersionModel(
                    id=version["id"],
                    template_id=template_id,
                    version_number=int(version.get("versionNumber") or 1),
                    created_by=actor.id,
                    status="draft",
                    metadata_json=json.dumps(version.get("metadata") or {}, ensure_ascii=False),
                    ir_template_json=json.dumps(version.get("irTemplate") or {}, ensure_ascii=False),
                    parameter_schema_json=json.dumps(
                        version.get("parameterSchema") or [], ensure_ascii=False
                    ),
                    bindings_json=json.dumps(version.get("bindings") or [], ensure_ascii=False),
                    constraints_json=json.dumps(
                        version.get("constraints") or [], ensure_ascii=False
                    ),
                    supported_backends_json=json.dumps(
                        version.get("supportedBackends") or ["canvas2d"], ensure_ascii=False
                    ),
                    fragment_source=version.get("fragmentSource"),
                    ontology_version=int(version.get("ontologyVersion") or 1),
                    change_note=str(version.get("changeNote") or ""),
                    submitted_by=None,
                    submitted_at=None,
                    published_at=None,
                    last_review_json=None,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        db.flush()


def apply_import(
    db: Session, actor: User, payload: ImportApply
) -> ImportApplied:
    """用校验凭证执行导入。全程一个事务，失败则一行都不写。"""
    _require_permission(actor, permissions.IMPORTS_APPLY)

    row = db.get(ValidationModel, payload.validationId)
    # 凭证不属于当前用户也按不存在处理，不确认别人有没有做过校验
    if row is None or row.actor_id != actor.id:
        raise errors.not_found("校验凭证不存在")
    if row.consumed_at:
        raise errors.invalid_state("该校验凭证已被使用，请重新校验")
    expires = _parse_dt(row.expires_at)
    if expires is None or expires <= _now():
        raise errors.invalid_state("校验凭证已过期，请重新校验")
    if not row.valid:
        raise errors.invalid_state("该包未通过校验，不能执行导入")

    current = _data_revision(db)
    if current != payload.expectedDataRevision:
        # 数据在验证之后被动过，之前的结构/引用结论已经失效
        raise errors.revision_conflict(payload.expectedDataRevision, current)

    package = CoreImportPackage.model_validate_json(row.package_json)
    counts = ManifestCounts.model_validate_json(row.counts_json)
    import_id = _new_id("imp")

    try:
        _insert_concepts(db, package.data.concepts, actor)
        _insert_tags(db, package.data.tags, actor)
        _insert_entities(db, package.data.entities, actor)
        _insert_rules(db, package.data.rules, actor)
        _insert_templates(db, package.data.templates, actor)

        row.consumed_at = _now_iso()
        new_revision = _bump_data_revision(db)
        audit.record(
            db,
            actor=actor,
            action="import.apply",
            target_type="import",
            target_id=import_id,
            input_summary=counts.model_dump(),
            result="success",
        )
        db.commit()
    except errors.ApiError as exc:
        db.rollback()
        audit.record_failure(
            db,
            actor=actor,
            action="import.apply",
            target_type="import",
            target_id=import_id,
            error_code=exc.code,
            input_summary=counts.model_dump(),
        )
        raise
    except Exception as exc:  # noqa: BLE001
        # 任何意外都整批回滚：导入一半的库比空库更难收拾
        db.rollback()
        audit.record_failure(
            db,
            actor=actor,
            action="import.apply",
            target_type="import",
            target_id=import_id,
            error_code="IMPORT_FAILED",
            input_summary=counts.model_dump(),
        )
        raise errors.validation_error(f"导入失败，已整批回滚：{exc}") from exc

    return ImportApplied(
        importId=import_id,
        dataRevision=new_revision,
        counts=counts,
        warnings=[],
    )

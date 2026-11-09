"""模板检索与读取的业务规则。

控制器只管 HTTP，这里负责三件事：

1. 把库里的 JSON 列还原成契约里的 ``TemplateMetadata``——缺字段补默认、
   脏数据过滤，做到「一条坏数据不会把整个列表接口打成 500」；
2. 判断谁能看到什么：``public`` 只出有公开指针的模板，``mine`` 只出本人，
   ``all`` 只给管理员；无权访问的私有内容一律 404，不泄露存在性；
3. 过滤与分页。

过滤放在 Python 里而不是 SQL：``entityIds`` / ``tagIds`` / ``symbols`` 都在
版本行的 JSON 文本里，用 SQLite 的 JSON1 函数去查会让索引全部失效，而当前
模板量只有个位数。P1 模板过百时再把这几列抽成关联表下推到 SQL。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import TypeAdapter
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app import errors
from app.engine.instantiate import TemplateDefinitionError
from app.models.templates import Template as TemplateModel
from app.models.templates import TemplateVersion as VersionModel
from app.models.users import User
from app.schemas.generation import Binding, Constraint, ParameterDefinition
from app.schemas.ir import DrawingIR
from app.schemas.templates import (
    Backend,
    Scope,
    Symbol,
    Template,
    TemplateMetadata,
    TemplatePage,
    TemplateVersion,
)
from app.services import permissions

# 契约里的四个后端。库里的默认值是历史遗留的 '["canvas"]'，读取时要过滤掉
_BACKENDS = ("turtle", "canvas2d", "webgl", "css")
_SYMBOLS = frozenset({"lotus", "bamboo", "water", "narrative"})
_DESCRIPTION_LIMIT = 1000
_UNNAMED = "未命名模板"

_PARAM_ADAPTER = TypeAdapter(ParameterDefinition)
_CONSTRAINT_ADAPTER = TypeAdapter(Constraint)


# ---- 小工具 --------------------------------------------------------------


def _parse_dt(value: str) -> datetime:
    """库里存的是 ISO-8601 文本；没有时区的老数据按 UTC 解释。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_dt_optional(value: str | None) -> datetime | None:
    return _parse_dt(value) if value else None


def _string_list(value: object, *, limit: int) -> list[str]:
    """契约要求 uniqueItems + 长度上限。脏数据直接丢弃，不让它炸响应。"""
    if not isinstance(value, list):
        return []
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            continue
        text = item[:80]
        if text not in seen:
            seen.append(text)
        if len(seen) >= limit:
            break
    return seen


def parse_metadata(
    raw: str | None,
    *,
    fallback_name: str = "",
    fallback_description: str = "",
) -> TemplateMetadata:
    """解析元数据 JSON，六个字段全部补齐。

    ``metadata_json`` 只有 ``{"name": ...}`` 这种半成品也比比皆是（seed 脚本
    只写名字），所以这里对每个字段单独兜底，不做整体校验失败。
    """
    data: object = {}
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
    if not isinstance(data, dict):
        data = {}

    name = data.get("name")
    name_text = name.strip() if isinstance(name, str) else ""
    name_text = name_text or fallback_name.strip() or _UNNAMED

    description = data.get("description")
    description_text = description if isinstance(description, str) else ""
    if not description_text:
        description_text = fallback_description or ""

    symbols = [item for item in _string_list(data.get("symbols"), limit=4) if item in _SYMBOLS]
    time_theme = data.get("timeTheme")
    if not isinstance(time_theme, str) or not time_theme.strip():
        time_theme = None

    return TemplateMetadata(
        name=name_text[:80],
        description=description_text[:_DESCRIPTION_LIMIT],
        entityIds=_string_list(data.get("entityIds"), limit=100),
        tagIds=_string_list(data.get("tagIds"), limit=100),
        symbols=symbols,
        timeTheme=time_theme[:40] if time_theme else None,
    )


def _supported_backends(raw: str | None) -> list[Backend]:
    """过滤出契约认得的后端；一个都不剩就给 canvas2d，保证列表非空。"""
    try:
        values = json.loads(raw) if raw else []
    except ValueError:
        values = []
    if not isinstance(values, list):
        values = []
    kept = [item for item in _BACKENDS if item in values]
    return kept or ["canvas2d"]  # type: ignore[list-item]


# ---- 模板容器 ------------------------------------------------------------


def _permissions_for_template(*, is_owner: bool, is_admin: bool) -> list[str]:
    """按「是不是作者 / 是不是管理员」推导出对这个模板的权限点。

    值必须取自契约的 Permission 枚举（见 services/permissions.py），
    不能自造 —— 前端是照着这些字符串做按钮开关的。
    """
    values: set[str] = set()
    if is_owner:
        values |= {
            permissions.TEMPLATES_EDIT_OWN,
            permissions.TEMPLATES_SUBMIT_OWN,
            permissions.TEMPLATES_ARCHIVE_OWN,
        }
    if is_admin:
        values.add(permissions.TEMPLATES_EDIT_ANY)
    return sorted(values)


def _load_versions(db: Session, version_ids: list[str]) -> dict[str, VersionModel]:
    """一次查完这一页引用的版本行，避免逐行 db.get 打出 N+1。"""
    ids = [item for item in version_ids if item]
    if not ids:
        return {}
    rows = db.scalars(select(VersionModel).where(VersionModel.id.in_(ids)))
    return {row.id: row for row in rows}


def build_template(
    row: TemplateModel, published: VersionModel | None, viewer: User | None
) -> Template:
    """把容器行还原成契约里的 Template。

    ``metadata`` 优先取发布版快照，这样「作者改了工作元数据但还没发布」时，
    公开列表里看到的仍然是已发布内容，不会提前泄露改动。
    """
    is_admin = viewer is not None and viewer.role == "admin"
    is_owner = viewer is not None and viewer.id == row.owner_id
    can_manage = is_owner or is_admin

    if published is not None:
        metadata = parse_metadata(
            published.metadata_json,
            fallback_name=row.name,
            fallback_description=row.description,
        )
    else:
        metadata = parse_metadata(
            row.working_metadata_json,
            fallback_name=row.name,
            fallback_description=row.description,
        )

    working = None
    if can_manage and row.working_metadata_json:
        working = parse_metadata(row.working_metadata_json, fallback_name=row.name)

    return Template(
        id=row.id,
        revision=row.revision,
        createdAt=_parse_dt(row.created_at),
        updatedAt=_parse_dt(row.updated_at),
        ownerId=row.owner_id,
        metadata=metadata,
        workingMetadata=working,
        publishedVersionId=row.published_version_id,
        latestVersionId=row.latest_version_id,
        supportedBackends=_supported_backends(row.supported_backends_json),
        thumbnailArtifactId=row.thumbnail_artifact_id,
        permissions=_permissions_for_template(is_owner=is_owner, is_admin=is_admin),
    )


def _apply_scope(
    statement: Select, scope: Scope, viewer: User | None
) -> Select:
    if scope == "public":
        # 公开指针非空即「对所有人可见」；归档会清掉指针，自然从列表消失
        return statement.where(TemplateModel.published_version_id.is_not(None))
    if viewer is None:
        raise errors.auth_required()
    if scope == "mine":
        return statement.where(TemplateModel.owner_id == viewer.id)
    if viewer.role != "admin":
        raise errors.forbidden("只有管理员可以查看全部模板")
    return statement


def _matches(
    template: Template,
    *,
    q: str | None,
    entity_ids: list[str] | None,
    tag_ids: list[str] | None,
    symbols: list[Symbol] | None,
    backend: Backend | None,
    time_theme: str | None,
) -> bool:
    """同一维度内 OR、不同维度之间 AND，和契约里「同维度 OR」的说法一致。"""
    metadata = template.metadata

    if q:
        needle = q.strip().lower()
        if needle:
            haystack = f"{metadata.name}\n{metadata.description}".lower()
            if needle not in haystack:
                return False
    if entity_ids and not set(entity_ids) & set(metadata.entityIds):
        return False
    if tag_ids and not set(tag_ids) & set(metadata.tagIds):
        return False
    if symbols and not set(symbols) & set(metadata.symbols):
        return False
    if backend and backend not in template.supportedBackends:
        return False
    if time_theme is not None and metadata.timeTheme != time_theme:
        return False
    return True


def list_templates(
    db: Session,
    viewer: User | None,
    *,
    scope: Scope = "public",
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    entity_ids: list[str] | None = None,
    tag_ids: list[str] | None = None,
    symbols: list[Symbol] | None = None,
    backend: Backend | None = None,
    time_theme: str | None = None,
) -> TemplatePage:
    statement = select(TemplateModel).order_by(
        TemplateModel.updated_at.desc(), TemplateModel.id
    )
    rows = list(db.scalars(_apply_scope(statement, scope, viewer)))

    published = _load_versions(
        db, [row.published_version_id for row in rows]
    )
    views = [
        build_template(
            row,
            published.get(row.published_version_id) if row.published_version_id else None,
            viewer,
        )
        for row in rows
    ]

    filtered = [
        item
        for item in views
        if _matches(
            item,
            q=q,
            entity_ids=entity_ids,
            tag_ids=tag_ids,
            symbols=symbols,
            backend=backend,
            time_theme=time_theme,
        )
    ]

    start = (page - 1) * page_size
    return TemplatePage(
        items=filtered[start : start + page_size],
        total=len(filtered),
        page=page,
        pageSize=page_size,
    )


# ---- 模板版本 ------------------------------------------------------------


def can_read_version(
    viewer: User | None, version: VersionModel, template: TemplateModel | None
) -> bool:
    """G 权限的实际含义：已发布版本所有人可读，未发布版本只有作者/管理员。

    另外放行「待审版本 + 审核人」这一种：审核人看不到内容就没法审。
    但只放行 submitted 这一状态，历史草稿不向审核人开放，避免遍历作者
    的其他版本。
    """
    if version.status == "published":
        return True
    if viewer is None:
        return False
    if viewer.role == "admin":
        return True
    if version.created_by == viewer.id:
        return True
    if template is not None and template.owner_id == viewer.id:
        return True
    return viewer.role == "expert" and version.status == "submitted"


def build_version(row: VersionModel) -> TemplateVersion:
    """整份版本内容。四份 JSON 解析失败属于服务端数据损坏，报 500。"""
    try:
        ir_template = DrawingIR.model_validate_json(row.ir_template_json)
        schema = [
            _PARAM_ADAPTER.validate_python(item)
            for item in json.loads(row.parameter_schema_json or "[]")
        ]
        bindings = [
            Binding.model_validate(item)
            for item in json.loads(row.bindings_json or "[]")
        ]
        constraints = [
            _CONSTRAINT_ADAPTER.validate_python(item)
            for item in json.loads(row.constraints_json or "[]")
        ]
        last_review = (
            json.loads(row.last_review_json) if row.last_review_json else None
        )
    except Exception as exc:  # noqa: BLE001 —— 损坏的形态很多，统一兜住
        raise TemplateDefinitionError(
            f"模板版本 {row.id} 的数据无法解析：{exc}"
        ) from exc

    return TemplateVersion(
        id=row.id,
        revision=row.revision,
        createdAt=_parse_dt(row.created_at),
        updatedAt=_parse_dt(row.updated_at),
        templateId=row.template_id,
        versionNumber=row.version_number,
        createdBy=row.created_by,
        submittedBy=row.submitted_by,
        status=row.status,  # type: ignore[arg-type]
        metadata=parse_metadata(row.metadata_json),
        irTemplate=ir_template,
        parameterSchema=schema,
        bindings=bindings,
        constraints=constraints,
        supportedBackends=["canvas2d"],
        ontologyVersion=row.ontology_version,
        changeNote=row.change_note or "",
        submittedAt=_parse_dt_optional(row.submitted_at),
        publishedAt=_parse_dt_optional(row.published_at),
        lastReview=last_review,
    )


def get_template_version(
    db: Session, viewer: User | None, version_id: str
) -> TemplateVersion:
    """不存在与无权读取返回同一个 404 —— ID 不能被拿来试探草稿是否存在。"""
    row = db.get(VersionModel, version_id)
    if row is None:
        raise errors.not_found("模板版本不存在")
    template = db.get(TemplateModel, row.template_id)
    if not can_read_version(viewer, row, template):
        raise errors.not_found("模板版本不存在")
    return build_version(row)


__all__ = [
    "build_template",
    "build_version",
    "can_read_version",
    "get_template_version",
    "list_templates",
    "parse_metadata",
]

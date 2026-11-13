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
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.engine.instantiate import (
    TemplateDefinitionError,
    check_template_integrity,
    instantiate,
)
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
    TemplateCreate,
    TemplateMetadata,
    TemplatePage,
    TemplateUpdate,
    TemplateVersion,
    TemplateVersionPage,
    VersionCreate,
    VersionUpdate,
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
        supportedBackends=_supported_backends(row.supported_backends_json),
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


# ---- P1A-03：容器与版本的写路径 -------------------------------------------
#
# 读路径在上面（list_templates / get_template_version），这里补写。
# 写操作的三条共同规则：
#   1. 权限按「作者本人 + 管理员」判，无权时给 404 而不是 403 ——
#      403 等于告诉对方「这个模板确实存在」，草稿的存在性也是信息。
#   2. 容器与版本都是乐观锁，expectedRevision 不匹配一律 409。
#   3. 版本内容必须能通过引擎的完整性校验才允许落库，
#      否则库里会出现「存进去了但一生成就 500」的模板。


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _can_manage(actor: User, row: TemplateModel) -> bool:
    """D-own/A：作者本人可管自己的模板，管理员可管全部。"""
    if actor.id == row.owner_id and permissions.has_permission(
        actor, permissions.TEMPLATES_EDIT_OWN
    ):
        return True
    return permissions.has_permission(actor, permissions.TEMPLATES_EDIT_ANY)


def _load_template_or_404(db: Session, template_id: str) -> TemplateModel:
    row = db.get(TemplateModel, template_id)
    if row is None:
        raise errors.not_found("模板不存在")
    return row


def _load_published(db: Session, row: TemplateModel) -> VersionModel | None:
    if not row.published_version_id:
        return None
    return db.get(VersionModel, row.published_version_id)


def create_template(db: Session, actor: User, payload: TemplateCreate) -> Template:
    """新建模板容器。此时还没有任何版本，``latestVersionId`` 为 null。"""
    if not permissions.has_permission(actor, permissions.TEMPLATES_CREATE):
        raise errors.forbidden("没有创建模板的权限")

    metadata = payload.metadata
    now = _now_iso()
    row = TemplateModel(
        id=f"tpl-{uuid4().hex}",
        owner_id=actor.id,
        # 容器上的 name/description 是冗余列，供列表页免解析 JSON；
        # 权威值仍是 working_metadata_json
        name=metadata.name,
        description=metadata.description,
        status="draft",
        published_version_id=None,
        latest_version_id=None,
        working_metadata_json=metadata.model_dump_json(),
        supported_backends_json='["canvas2d"]',
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    return build_template(row, None, actor)


def get_template(db: Session, viewer: User | None, template_id: str) -> Template:
    """模板详情。

    从没有发布过的模板对非作者按不存在处理——草稿的 ID 不该能被试探。
    """
    row = _load_template_or_404(db, template_id)
    is_owner = viewer is not None and viewer.id == row.owner_id
    is_admin = viewer is not None and viewer.role == "admin"
    if row.published_version_id is None and not (is_owner or is_admin):
        raise errors.not_found("模板不存在")
    return build_template(row, _load_published(db, row), viewer)


def update_template(
    db: Session, actor: User, template_id: str, payload: TemplateUpdate
) -> Template:
    """更新容器的工作元数据。**不触碰已发布版本的快照。**

    新版本创建时才把当前工作元数据复制过去，所以这里改了也不会
    影响已经公开的版本——这正是「工作区」和「快照」该有的隔离。
    """
    row = _load_template_or_404(db, template_id)
    if not _can_manage(actor, row):
        raise errors.not_found("模板不存在")
    if row.revision != payload.expectedRevision:
        raise errors.revision_conflict(payload.expectedRevision, row.revision)

    metadata = payload.metadata
    row.name = metadata.name
    row.description = metadata.description
    row.working_metadata_json = metadata.model_dump_json()
    row.revision += 1
    row.updated_at = _now_iso()
    db.commit()
    return build_template(row, _load_published(db, row), actor)


def list_template_versions(
    db: Session,
    viewer: User | None,
    template_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> TemplateVersionPage:
    """版本列表。

    作者/管理员看全部；其他人只看已发布版本——待审和草稿的存在性同样不该
    通过这个接口泄露出去。

    排序用 ``version_number`` 降序而不是契约通用的 ``created_at`` 降序：
    版本号单调递增且唯一，等价于时间序但更稳定（同一秒内建两个版本也不会乱序）。
    """
    row = _load_template_or_404(db, template_id)
    is_owner = viewer is not None and viewer.id == row.owner_id
    is_admin = viewer is not None and viewer.role == "admin"
    can_manage = is_owner or is_admin

    # 和 get_template 同一条可见性规则：从没发布过的模板对外就是不存在。
    # 少了这一句，匿名者虽然只看到已发布版本，但能通过「返回空列表而非 404」
    # 反推出这个 ID 确实是个模板——存在性也是信息。
    if row.published_version_id is None and not can_manage:
        raise errors.not_found("模板不存在")

    statement = select(VersionModel).where(VersionModel.template_id == template_id)
    if not can_manage:
        statement = statement.where(VersionModel.status == "published")

    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(
        db.scalars(
            statement.order_by(
                VersionModel.version_number.desc(), VersionModel.id.desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return TemplateVersionPage(
        items=[build_version(item) for item in rows],
        total=total,
        page=page,
        pageSize=page_size,
    )


def _next_version_number(db: Session, template_id: str) -> int:
    current = db.scalar(
        select(func.max(VersionModel.version_number)).where(
            VersionModel.template_id == template_id
        )
    )
    return (current or 0) + 1


def _dump_params(items: list) -> str:
    """参数定义是判别联合，必须走 TypeAdapter 才能带上 type 字段。"""
    return json.dumps(
        [_PARAM_ADAPTER.dump_python(item, mode="json") for item in items],
        ensure_ascii=False,
    )


def _dump_constraints(items: list) -> str:
    return json.dumps(
        [_CONSTRAINT_ADAPTER.dump_python(item, mode="json") for item in items],
        ensure_ascii=False,
    )


def create_template_version(
    db: Session, actor: User, template_id: str, payload: VersionCreate
) -> TemplateVersion:
    """创建新版本，或从既有版本克隆。

    字段来源的优先级：**请求显式给出 > 克隆来源版本 > 容器工作元数据**。
    这样前端克隆时只传想改的字段即可，不用先把整份内容读回来再原样发一遍。
    """
    row = _load_template_or_404(db, template_id)
    if not _can_manage(actor, row):
        raise errors.not_found("模板不存在")

    if payload.fromVersionId is not None:
        base = db.get(VersionModel, payload.fromVersionId)
        # 跨模板克隆没有语义：版本号、元数据都得挂在自己的容器下
        if base is None or base.template_id != template_id:
            raise errors.not_found("来源版本不存在")
        source = build_version(base)
        ir_template = payload.irTemplate or source.irTemplate
        parameter_schema = (
            payload.parameterSchema
            if payload.parameterSchema is not None
            else source.parameterSchema
        )
        bindings = payload.bindings if payload.bindings is not None else source.bindings
        constraints = (
            payload.constraints
            if payload.constraints is not None
            else source.constraints
        )
        metadata = payload.metadata or source.metadata
        ontology_version = payload.ontologyVersion or source.ontologyVersion
        change_note = (
            payload.changeNote
            if payload.changeNote is not None
            else f"克隆自版本 {source.versionNumber}"
        )
    else:
        ir_template = payload.irTemplate
        parameter_schema = payload.parameterSchema
        bindings = payload.bindings or []
        constraints = payload.constraints or []
        metadata = payload.metadata or parse_metadata(
            row.working_metadata_json,
            fallback_name=row.name,
            fallback_description=row.description,
        )
        ontology_version = payload.ontologyVersion or 1
        change_note = payload.changeNote or ""

    # 这里三个都不该是 None：没有 fromVersionId 时校验器已经要求必填，
    # 有 fromVersionId 时从来源版本补齐了。断言式防御，避免把 None 写进 NOT NULL 列。
    if ir_template is None or parameter_schema is None:
        raise errors.validation_error("版本内容不完整")

    try:
        check_template_integrity(ir_template, parameter_schema, bindings, constraints)
        outcome = instantiate(ir_template, parameter_schema, bindings, constraints, {})
    except TemplateDefinitionError as exc:
        # 引擎把这类问题当作「服务端数据缺陷」，但在这里是客户端刚提交的定义
        # 不自洽，该由它修 → 转成 422，不要让用户看到 500。
        raise errors.validation_error(
            "模板定义不自洽", [{"path": "irTemplate", "message": str(exc)}]
        ) from exc

    if not outcome.ok:
        # 参数默认值本身不合法：建出来的模板一按「生成」就报错，必须当场拦
        raise errors.validation_error(
            "参数默认值不合法",
            [
                {
                    "path": f"parameterSchema.{issue.path}",
                    "message": issue.message,
                }
                for issue in outcome.issues
            ],
        )

    now = _now_iso()
    version = VersionModel(
        id=f"tver-{uuid4().hex}",
        template_id=template_id,
        version_number=_next_version_number(db, template_id),
        created_by=actor.id,
        status="draft",
        metadata_json=metadata.model_dump_json(),
        ir_template_json=ir_template.model_dump_json(),
        parameter_schema_json=_dump_params(list(parameter_schema)),
        bindings_json=json.dumps(
            [item.model_dump(mode="json") for item in bindings], ensure_ascii=False
        ),
        constraints_json=_dump_constraints(list(constraints)),
        # 版本自己声明的支持集合；没声明就沿用 Canvas
        supported_backends_json=json.dumps(
            payload.supportedBackends or ["canvas2d"], ensure_ascii=False
        ),
        fragment_source=None,
        ontology_version=ontology_version,
        change_note=change_note,
        submitted_by=None,
        submitted_at=None,
        published_at=None,
        last_review_json=None,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(version)
    # 容器的最新版本指针跟着走；发布指针留给 P1A-04 的审核流程
    row.latest_version_id = version.id
    if row.working_metadata_json is None:
        row.working_metadata_json = metadata.model_dump_json()
    row.revision += 1
    row.updated_at = now
    try:
        db.commit()
    except IntegrityError:
        # (template_id, version_number) 唯一约束兜住并发创建
        db.rollback()
        raise errors.invalid_state("并发创建版本冲突，请重试")
    return build_version(version)


# 只有这两个状态下内容才算「还没被人看过」：submitted 已进队列、published 是
# 别人引用过的历史，改它们等于在审核人看过的稿子上换字。
_EDITABLE_STATUSES = ("draft", "rejected")


def update_template_version(
    db: Session, actor: User, version_id: str, payload: VersionUpdate
) -> TemplateVersion:
    """D-own/A。就地编辑 draft/rejected 版本。

    「退回→修订→再送审」依赖这条接口。没有它，作者只能新建一个版本冒充
    修订，版本号、变更说明、审核记录全都对不上原稿。
    """
    row = db.get(VersionModel, version_id)
    template = db.get(TemplateModel, row.template_id) if row is not None else None
    if row is None or template is None or not _can_manage(actor, template):
        # 无权与不存在同样 404：403 等于承认这个版本 id 真的存在
        raise errors.not_found("模板版本不存在")
    if row.status not in _EDITABLE_STATUSES:
        raise errors.invalid_state(
            f"{row.status} 状态的版本不能编辑，只有草稿与被退回的版本可以改"
        )
    if payload.expectedRevision != row.revision:
        raise errors.revision_conflict(payload.expectedRevision, row.revision)
    if payload.fragmentSource is not None:
        raise errors.validation_error("当前只支持 IR 模板，片段源码不可写")
    if payload.generatorRef not in (None, "ir"):
        raise errors.validation_error("当前只支持 IR 模板（generatorRef=ir）")

    # 合并规则：请求给了才覆盖，没给就保留。版本是快照，
    # 「这次没传」不等于「把这些字段清空」。
    metadata_json = (
        payload.metadata.model_dump_json()
        if payload.metadata is not None
        else row.metadata_json
    )
    ir_template_json = (
        payload.irTemplate.model_dump_json()
        if payload.irTemplate is not None
        else row.ir_template_json
    )
    parameter_schema_json = (
        _dump_params(list(payload.parameterSchema))
        if payload.parameterSchema is not None
        else row.parameter_schema_json
    )
    bindings_json = (
        json.dumps(
            [item.model_dump(mode="json") for item in payload.bindings],
            ensure_ascii=False,
        )
        if payload.bindings is not None
        else row.bindings_json
    )
    constraints_json = (
        _dump_constraints(list(payload.constraints))
        if payload.constraints is not None
        else row.constraints_json
    )
    supported_backends_json = (
        json.dumps(payload.supportedBackends, ensure_ascii=False)
        if payload.supportedBackends is not None
        else row.supported_backends_json
    )
    ontology_version = (
        payload.ontologyVersion
        if payload.ontologyVersion is not None
        else row.ontology_version
    )
    change_note = (
        payload.changeNote if payload.changeNote is not None else row.change_note
    )

    # 改完的定义必须还是「能生成」的：否则审核人点开才发现存了份跑不起来的
    # 模板，等于把校验成本转给人工。
    try:
        ir_template = DrawingIR.model_validate_json(ir_template_json)
        schema = [
            _PARAM_ADAPTER.validate_python(item)
            for item in json.loads(parameter_schema_json or "[]")
        ]
        bindings = [
            Binding.model_validate(item) for item in json.loads(bindings_json or "[]")
        ]
        constraints = [
            _CONSTRAINT_ADAPTER.validate_python(item)
            for item in json.loads(constraints_json or "[]")
        ]
        check_template_integrity(ir_template, schema, bindings, constraints)
        outcome = instantiate(ir_template, schema, bindings, constraints, {})
    except TemplateDefinitionError as exc:
        raise errors.validation_error(
            "模板定义不自洽", [{"path": "irTemplate", "message": str(exc)}]
        ) from exc
    except ValueError as exc:
        raise errors.validation_error(f"模板定义无法解析：{exc}") from exc
    if not outcome.ok:
        raise errors.validation_error(
            "参数默认值不合法",
            [
                {"path": f"parameterSchema.{issue.path}", "message": issue.message}
                for issue in outcome.issues
            ],
        )

    now = _now_iso()
    # 条件写进 WHERE。只靠上面那次「先比后写」，两个并发 PATCH 都能通过比较，
    # 后一个把前一个的修改盖掉——这正是评审在作品路径上挑出来的同一类问题。
    result = db.execute(
        update(VersionModel)
        .where(
            VersionModel.id == version_id,
            VersionModel.revision == payload.expectedRevision,
            VersionModel.status.in_(_EDITABLE_STATUSES),
        )
        .values(
            metadata_json=metadata_json,
            ir_template_json=ir_template_json,
            parameter_schema_json=parameter_schema_json,
            bindings_json=bindings_json,
            constraints_json=constraints_json,
            supported_backends_json=supported_backends_json,
            ontology_version=ontology_version,
            change_note=change_note,
            revision=VersionModel.revision + 1,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise errors.invalid_state("版本状态已变化，请重新读取后再改")
    db.commit()
    db.refresh(row)
    return build_version(row)


__all__ = [
    "build_template",
    "build_version",
    "can_read_version",
    "create_template",
    "create_template_version",
    "get_template",
    "get_template_version",
    "list_template_versions",
    "list_templates",
    "parse_metadata",
    "update_template",
    "update_template_version",
]

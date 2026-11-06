"""固定角色动作白名单与私有资源所有权校验。

本基础版采用「固定角色 → 固定权限集合」，不做自定义权限设计器
（看板 §2 已明确这一缺口）。四个角色的权限逐层累加：

    creator     普通创作者：建作品、管自己的模板与收藏
    developer   += 知识与本体维护
    expert      += 审核决定、下线作品、导出
    admin       全部权限

权限名严格取自契约 Permission 枚举（24 项），不能自造
——前端和后端都按这些字符串判断。
"""

from __future__ import annotations

from app.models.users import User

# ---- 24 个权限点（与契约 Permission 枚举完全一致）------------------------
PROFILE_EDIT = "profile.edit"
CREATIONS_CREATE = "creations.create"
CREATIONS_EDIT_OWN = "creations.editOwn"
SESSIONS_MANAGE_OWN = "sessions.manageOwn"
WORKS_SUBMIT_OWN = "works.submitOwn"
FAVORITES_MANAGE_OWN = "favorites.manageOwn"
TEMPLATES_CREATE = "templates.create"
TEMPLATES_EDIT_OWN = "templates.editOwn"
TEMPLATES_EDIT_ANY = "templates.editAny"
TEMPLATES_SUBMIT_OWN = "templates.submitOwn"
TEMPLATES_ARCHIVE_OWN = "templates.archiveOwn"
KNOWLEDGE_WRITE = "knowledge.write"
ONTOLOGY_WRITE = "ontology.write"
TAGS_WRITE = "tags.write"
REVIEWS_DECIDE = "reviews.decide"
RULES_READ = "rules.read"
RULES_WRITE = "rules.write"
USERS_APPROVE_ROLE = "users.approveRole"
WORKS_UNPUBLISH_ANY = "works.unpublishAny"
POLICIES_READ = "policies.read"
POLICIES_WRITE = "policies.write"
AUDIT_READ = "audit.read"
IMPORTS_APPLY = "imports.apply"
EXPORTS_CREATE = "exports.create"

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        PROFILE_EDIT,
        CREATIONS_CREATE,
        CREATIONS_EDIT_OWN,
        SESSIONS_MANAGE_OWN,
        WORKS_SUBMIT_OWN,
        FAVORITES_MANAGE_OWN,
        TEMPLATES_CREATE,
        TEMPLATES_EDIT_OWN,
        TEMPLATES_EDIT_ANY,
        TEMPLATES_SUBMIT_OWN,
        TEMPLATES_ARCHIVE_OWN,
        KNOWLEDGE_WRITE,
        ONTOLOGY_WRITE,
        TAGS_WRITE,
        REVIEWS_DECIDE,
        RULES_READ,
        RULES_WRITE,
        USERS_APPROVE_ROLE,
        WORKS_UNPUBLISH_ANY,
        POLICIES_READ,
        POLICIES_WRITE,
        AUDIT_READ,
        IMPORTS_APPLY,
        EXPORTS_CREATE,
    }
)

# 创作者：只能动自己产出的东西，读规则但不改
_CREATOR: frozenset[str] = frozenset(
    {
        PROFILE_EDIT,
        CREATIONS_CREATE,
        CREATIONS_EDIT_OWN,
        SESSIONS_MANAGE_OWN,
        WORKS_SUBMIT_OWN,
        FAVORITES_MANAGE_OWN,
        TEMPLATES_CREATE,
        TEMPLATES_EDIT_OWN,
        TEMPLATES_SUBMIT_OWN,
        TEMPLATES_ARCHIVE_OWN,
        RULES_READ,
    }
)

# 开发者：额外负责知识、本体、标签与规则内容
_DEVELOPER: frozenset[str] = _CREATOR | {
    KNOWLEDGE_WRITE,
    ONTOLOGY_WRITE,
    TAGS_WRITE,
    RULES_WRITE,
}

# 专家：额外承担审核，能下线他人作品、导出数据
_EXPERT: frozenset[str] = _DEVELOPER | {
    REVIEWS_DECIDE,
    WORKS_UNPUBLISH_ANY,
    EXPORTS_CREATE,
    POLICIES_READ,
}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "creator": _CREATOR,
    "developer": _DEVELOPER,
    "expert": _EXPERT,
    "admin": ALL_PERMISSIONS,
}


def permissions_for(role: str) -> list[str]:
    """返回该角色的权限列表，按字母序保证响应稳定（便于前端比对）。"""
    return sorted(ROLE_PERMISSIONS.get(role, frozenset()))


def has_permission(user: User | None, permission: str) -> bool:
    if user is None:
        return False
    return permission in ROLE_PERMISSIONS.get(user.role, frozenset())


def owns(user: User | None, owner_id: str) -> bool:
    """私有资源的所有权判断。

    调用方注意：无所有权时应返回 404 而非 403，
    以免泄露「这个资源确实存在」。
    """
    return user is not None and user.id == owner_id

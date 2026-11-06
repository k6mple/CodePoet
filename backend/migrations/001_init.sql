-- 001_init.sql：P0-01/P0-02 所需的最小业务表
--
-- 设计约定：
--   1. 主键、外键统一用 TEXT，存 UUID 字符串，不使用自增整数
--      （契约里 id 是「不透明标识」，前缀不代表权限）。
--   2. 复杂结构（别名数组、来源引用、IR 模板）以 *_json 列存 JSON 文本。
--      SQLite 3.45+ 有 JSON 函数，但本项目不依赖其做查询条件；
--      需要按条件检索的字段一律单独成列并建索引。
--   3. 时间统一存 ISO-8601 带时区的 TEXT（如 2026-11-06T09:30:00+08:00）。
--   4. 枚举用 CHECK 约束兜底，防止绕过 API 直接写库写出契约外的值。
--   5. revision 用于乐观并发：更新时带旧 revision，不匹配则冲突。

-- 账号：四角色 + 启用状态。password_hash 只存哈希，绝不存明文。
CREATE TABLE users (
    id              TEXT PRIMARY KEY NOT NULL,
    username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash   TEXT NOT NULL,
    nickname        TEXT NOT NULL,
    bio             TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL CHECK (role IN ('creator', 'developer', 'expert', 'admin')),
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    role_applied_at TEXT,
    role_applied_for TEXT CHECK (role_applied_for IS NULL OR role_applied_for IN ('creator', 'developer', 'expert', 'admin')),
    revision        INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- 知识实体：典故、人物、概念、象征。
CREATE TABLE knowledge_entities (
    id                     TEXT PRIMARY KEY NOT NULL,
    type                   TEXT NOT NULL CHECK (type IN ('person', 'story', 'concept', 'symbol')),
    name                   TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    alias_json             TEXT NOT NULL DEFAULT '[]',
    summary                TEXT NOT NULL DEFAULT '',
    content                TEXT NOT NULL DEFAULT '',
    source_references_json TEXT NOT NULL DEFAULT '[]',
    concept_ids_json       TEXT NOT NULL DEFAULT '[]',
    tag_ids_json           TEXT NOT NULL DEFAULT '[]',
    relations_json         TEXT NOT NULL DEFAULT '[]',
    time_theme             TEXT,
    status                 TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    owner_id               TEXT REFERENCES users(id),
    revision               INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

-- 模板主表：只放元数据和指针，真正的 IR 在各版本里。
-- published_version_id 指向「当前公开给别人用的那一版」，
-- latest_version_id 指向「最新的一版（可能是草稿）」。
CREATE TABLE templates (
    id                      TEXT PRIMARY KEY NOT NULL,
    owner_id                TEXT NOT NULL REFERENCES users(id),
    name                    TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    description             TEXT NOT NULL DEFAULT '',
    status                  TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft', 'submitted', 'published', 'rejected', 'archived')),
    category                TEXT,
    published_version_id    TEXT,
    latest_version_id       TEXT,
    working_metadata_json   TEXT,
    supported_backends_json TEXT NOT NULL DEFAULT '["canvas"]',
    thumbnail_artifact_id   TEXT,
    revision                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

-- 模板版本：每次结构改动产生一个新版本，历史只读。
CREATE TABLE template_versions (
    id                              TEXT PRIMARY KEY NOT NULL,
    template_id                     TEXT NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
    version_number                  INTEGER NOT NULL CHECK (version_number >= 1),
    created_by                      TEXT NOT NULL REFERENCES users(id),
    status                          TEXT NOT NULL DEFAULT 'draft'
                                    CHECK (status IN ('draft', 'submitted', 'published', 'rejected', 'archived')),
    metadata_json                   TEXT NOT NULL DEFAULT '{}',
    ir_template_json                TEXT NOT NULL,
    parameter_schema_json           TEXT NOT NULL DEFAULT '[]',
    bindings_json                   TEXT NOT NULL DEFAULT '[]',
    constraints_json                TEXT NOT NULL DEFAULT '[]',
    supported_backends_json         TEXT NOT NULL DEFAULT '["canvas"]',
    fragment_source                 TEXT,
    ontology_version                INTEGER NOT NULL DEFAULT 1,
    change_note                     TEXT NOT NULL DEFAULT '',
    submitted_by                    TEXT REFERENCES users(id),
    submitted_at                    TEXT,
    published_at                    TEXT,
    last_review_json                TEXT,
    revision                        INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at                      TEXT NOT NULL,
    updated_at                      TEXT NOT NULL,
    UNIQUE (template_id, version_number)
);

-- 查询索引：检索、状态筛选和归属查询是最常见的三类。
CREATE INDEX idx_knowledge_type_status ON knowledge_entities (type, status);
CREATE INDEX idx_knowledge_owner ON knowledge_entities (owner_id);
CREATE INDEX idx_templates_owner_status ON templates (owner_id, status);
CREATE INDEX idx_template_versions_status ON template_versions (template_id, status);

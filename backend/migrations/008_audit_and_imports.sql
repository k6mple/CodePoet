-- 008_audit_and_imports.sql
-- P1A-07：审计日志、导入校验记录、数据版本。
--
-- 三张表各解决一件事：
--
-- 1. audit_logs —— 关键写操作的留痕。字段对齐契约 AuditLog（18 个字段），
--    request_id 是追溯的钥匙：一个请求改了三张表，靠它能串起来。
--    记录不可改不可删，否则"审计"两个字没有意义。
-- 2. import_validations —— 预校验与执行之间的凭证。契约要求
--    「validationId 10 分钟有效、绑定用户和内容 hash、单次消费」，
--    所以这里存整份包体：不存的话执行时得让客户端再传一遍，
--    中间可能被换掉，hash 就对不上了。
-- 3. data_meta —— 全局数据版本。导入会整体改动数据集，
--    验证之后数据又变了就必须重新验证，否则 apply 会覆盖别人的改动。

CREATE TABLE audit_logs (
    id                  TEXT PRIMARY KEY NOT NULL,
    created_at          TEXT NOT NULL,
    -- 匿名操作没有 actor，这里允许空
    actor_id            TEXT REFERENCES users(id),
    actor_role          TEXT,
    action              TEXT NOT NULL,
    target_type         TEXT NOT NULL DEFAULT '',
    target_id           TEXT,
    version_id          TEXT,
    request_id          TEXT NOT NULL,
    parent_request_id   TEXT,
    generation_id       TEXT,
    job_id              TEXT,
    work_id             TEXT,
    input_summary_json  TEXT NOT NULL DEFAULT '{}',
    before_json         TEXT,
    after_json          TEXT,
    result              TEXT NOT NULL CHECK (result IN ('success', 'failure')),
    error_code          TEXT
);

CREATE INDEX idx_audit_created ON audit_logs (created_at DESC, id DESC);
CREATE INDEX idx_audit_actor ON audit_logs (actor_id, created_at DESC);
CREATE INDEX idx_audit_request ON audit_logs (request_id);
CREATE INDEX idx_audit_action ON audit_logs (action, created_at DESC);
CREATE INDEX idx_audit_target ON audit_logs (target_type, target_id);

CREATE TRIGGER audit_logs_forbid_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, '审计日志不可修改');
END;

CREATE TRIGGER audit_logs_forbid_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, '审计日志不可删除');
END;

CREATE TABLE import_validations (
    id                     TEXT PRIMARY KEY NOT NULL,
    actor_id               TEXT NOT NULL REFERENCES users(id),
    content_hash           TEXT NOT NULL,
    expected_data_revision INTEGER NOT NULL CHECK (expected_data_revision >= 0),
    -- 校验是否通过。存下来是为了让 apply 能拒绝「结构本身就不合法」的包，
    -- 而不是指望前端自己先看一眼 valid 字段再决定要不要点执行
    valid                  INTEGER NOT NULL DEFAULT 0 CHECK (valid IN (0, 1)),
    -- 整份包体存下来：执行时不再信任客户端重发的版本
    package_json           TEXT NOT NULL,
    counts_json            TEXT NOT NULL DEFAULT '{}',
    expires_at             TEXT NOT NULL,
    -- 单次消费：执行成功即写入，二次执行会被拒
    consumed_at            TEXT,
    created_at             TEXT NOT NULL
);

CREATE INDEX idx_import_validations_actor
    ON import_validations (actor_id, created_at DESC);

CREATE TABLE data_meta (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    data_revision INTEGER NOT NULL DEFAULT 0 CHECK (data_revision >= 0)
);

INSERT INTO data_meta (id, data_revision) VALUES (1, 0);

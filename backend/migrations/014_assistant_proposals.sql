-- X1：LLM 智能辅助提案持久化。
--
-- 仅新增 assistant_proposals 表，不修改历史表结构，确保旧库（含 F1 演示备份库）
-- 平滑升级且历史作品零破坏。

CREATE TABLE assistant_proposals (
    id                    TEXT    PRIMARY KEY,
    owner_id              TEXT    NOT NULL REFERENCES users(id),
    base_generation_id    TEXT    NOT NULL REFERENCES generations(id),

    -- ir_fragment / parameter_schema
    target                TEXT    NOT NULL CHECK (target IN ('ir_fragment', 'parameter_schema')),
    root_node_id          TEXT,

    prompt                TEXT    NOT NULL,
    prompt_hash           TEXT    NOT NULL CHECK (length(prompt_hash) = 64),

    -- queued / running / ready / failed
    status                TEXT    NOT NULL CHECK (status IN ('queued', 'running', 'ready', 'failed')),
    model                 TEXT    NOT NULL,
    attempts              INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0 AND attempts <= 2),
    policy_revision       INTEGER NOT NULL DEFAULT 1 CHECK (policy_revision >= 1),

    -- proposal 在 ready 前必须为 NULL；ready 后保存规范化 JSON 与 SHA-256 哈希
    proposal_json         TEXT,
    proposal_hash         TEXT    CHECK (proposal_hash IS NULL OR length(proposal_hash) = 64),
    validation_json       TEXT,
    error_json            TEXT,

    -- 显式 apply 后记录关联的新 generation，事务幂等保护避免重复派生
    applied_generation_id TEXT    REFERENCES generations(id),

    request_id            TEXT    NOT NULL,
    created_at            TEXT    NOT NULL,
    finished_at           TEXT,

    CHECK (
        (target = 'ir_fragment' AND root_node_id IS NOT NULL) OR
        (target = 'parameter_schema' AND root_node_id IS NULL)
    ),
    CHECK (
        (status = 'ready' AND proposal_json IS NOT NULL AND proposal_hash IS NOT NULL AND attempts >= 1) OR
        (status <> 'ready' AND proposal_json IS NULL)
    ),
    CHECK (
        (status IN ('queued', 'running') AND finished_at IS NULL) OR
        (status IN ('ready', 'failed') AND finished_at IS NOT NULL)
    ),
    CHECK (applied_generation_id IS NULL OR status = 'ready')
);

CREATE INDEX idx_assistant_proposals_owner_created
    ON assistant_proposals (owner_id, created_at DESC);

CREATE INDEX idx_assistant_proposals_owner_status
    ON assistant_proposals (owner_id, status);

CREATE INDEX idx_assistant_proposals_base
    ON assistant_proposals (base_generation_id);

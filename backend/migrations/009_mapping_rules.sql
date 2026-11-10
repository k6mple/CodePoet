-- 009_mapping_rules.sql
-- P1B-01：语义映射规则。
--
-- 规则是「输入文字/实体 → 图形决策」的可维护配置。两个字段值得解释：
--
-- * priority 决定冲突时谁说了算。0~1000，越大越优先。同优先级按
--   created_at 早的优先，保证结果是确定的（同样的输入永远出同样的图）。
-- * version 与 revision 分开：revision 是乐观锁计数器（每次 PATCH +1），
--   version 是「规则内容版本号」（也被 PATCH 推进，但语义不同——前者给
--   并发控制用，后者给推理快照记录「当时用的是第几版规则」用）。
--   合成一列会让「同一次修改既影响并发判断又影响推理来源」纠缠不清。

CREATE TABLE mapping_rules (
    id             TEXT PRIMARY KEY NOT NULL,
    name           TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    enabled        INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    priority       INTEGER NOT NULL DEFAULT 0 CHECK (priority BETWEEN 0 AND 1000),
    match_json     TEXT NOT NULL,
    effects_json   TEXT NOT NULL,
    explanation    TEXT NOT NULL DEFAULT '' CHECK (length(explanation) <= 500),
    version        INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    revision       INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    owner_id       TEXT REFERENCES users(id),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX idx_rules_enabled_priority
    ON mapping_rules (enabled, priority DESC, created_at);

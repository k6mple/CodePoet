-- 007_sessions_favorites.sql
-- P1A-05：编辑会话、不可变快照与作品收藏。
--
-- 设计要点：
--
-- 1. 会话（creation_sessions）是「当前编辑现场」，快照（session_snapshots）
--    是历史。两者分开的原因：现场只保留一份最新状态，历史必须一条条攒下来
--    才能回退。混成一张表就得在每次保存时把旧状态迁移走，容易丢。
-- 2. 快照不可修改，只用触发器拦 UPDATE。DELETE 不禁——会话被删时靠
--    ON DELETE CASCADE 连带清理，禁掉会让删除整个失败。
-- 3. latest_snapshot_id 不设外键：它指向快照表，而快照表又外键回会话，
--    双向外键在 SQLite 的插入顺序上会互相卡住。用服务层保证一致性，
--    代价是历史上可能出现悬空 ID，读取时按「取不到就当没有」处理。
-- 4. 收藏用 (owner_id, work_id) 复合主键，幂等天然成立：
--    重复收藏是 INSERT OR IGNORE，取消是 DELETE，都不需要先查后写。

CREATE TABLE creation_sessions (
    id                 TEXT PRIMARY KEY NOT NULL,
    owner_id           TEXT NOT NULL REFERENCES users(id),
    name               TEXT NOT NULL DEFAULT '' CHECK (length(name) <= 80),
    -- EditorState 整份存 JSON。字段会随 P1-B 演进，拆列得不偿失
    state_json         TEXT NOT NULL,
    latest_snapshot_id TEXT,
    revision           INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX idx_sessions_owner ON creation_sessions (owner_id, updated_at DESC);

CREATE TABLE session_snapshots (
    id                        TEXT PRIMARY KEY NOT NULL,
    session_id                TEXT NOT NULL
                                REFERENCES creation_sessions(id) ON DELETE CASCADE,
    -- 写入时该会话的 revision，用于把快照与某次保存对上号
    session_revision          INTEGER NOT NULL CHECK (session_revision >= 1),
    state_json                TEXT NOT NULL,
    name                      TEXT NOT NULL DEFAULT '' CHECK (length(name) <= 80),
    note                      TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 200),
    -- 由恢复产生的快照会记下来源，方便界面上标「从这一版恢复」
    restored_from_snapshot_id TEXT,
    created_at                TEXT NOT NULL
);

CREATE INDEX idx_snapshots_session
    ON session_snapshots (session_id, created_at DESC, id DESC);

CREATE TRIGGER snapshots_forbid_update
BEFORE UPDATE ON session_snapshots
BEGIN
    SELECT RAISE(ABORT, '快照不可修改：它是某一时刻的既成事实，要改就新建快照');
END;

CREATE TABLE work_favorites (
    owner_id   TEXT NOT NULL REFERENCES users(id),
    work_id    TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, work_id)
);

CREATE INDEX idx_favorites_work ON work_favorites (work_id);

-- P1A-01 / P1A-02：角色申请审批状态、标签树、本体树
--
-- 设计要点：
-- 1. 标签与本体都是「单父树」，父子关系用 parent_id 自引用，不建闭包表。
--    两棵树体量都在百级，递归查询用应用层一次取全表算，比维护闭包便宜。
-- 2. 停用一律软停用（status='inactive'），不物理删除。
--    知识条目与作品快照里存着 tagId/conceptId，删了历史就断链。
-- 3. 「同名」只在 active 范围内禁止，用部分唯一索引表达。
--    停用后名字可以重新占用，避免运营改错一次名字就永久占坑。
-- 4. 防环做两层：直接自引用用触发器拦（零成本），
--    间接环（A→B→A）必须查全表，放服务层。
-- 5. users 的角色申请状态不新建表：
--    契约里 User.roleApplication 只有单条，没有历史列表的要求，
--    而 001 已有 role_applied_at / role_applied_for 两个字段，
--    补四个列比另起一张表少一次 join，也不会出现两份真相。

-- ---- 1. users：角色申请审批状态 -------------------------------------------

ALTER TABLE users ADD COLUMN role_application_status TEXT
    CHECK (role_application_status IS NULL
           OR role_application_status IN ('pending', 'approved', 'rejected'));
ALTER TABLE users ADD COLUMN role_decision_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN role_decided_at TEXT;
-- 谁批的，用于审计；本人审批由服务层拦，这里只记录事实
ALTER TABLE users ADD COLUMN role_decided_by TEXT REFERENCES users(id);

-- 存量回填：001 里已申请但尚无状态的历史行，一律视为待审
UPDATE users
   SET role_application_status = 'pending'
 WHERE role_applied_for IS NOT NULL
   AND role_application_status IS NULL;

-- 注册接口只写 role_applied_for，状态由触发器补齐，
-- 免得每个写入点都要记得多写一个字段。
CREATE TRIGGER trg_users_role_application_pending
AFTER INSERT ON users
WHEN NEW.role_applied_for IS NOT NULL
 AND NEW.role_application_status IS NULL
BEGIN
    UPDATE users
       SET role_application_status = 'pending'
     WHERE id = NEW.id;
END;

-- ---- 2. tags：知识标签树 --------------------------------------------------

CREATE TABLE tags (
    id                  TEXT PRIMARY KEY NOT NULL,
    name                TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    description         TEXT NOT NULL DEFAULT ''
                          CHECK (length(description) <= 500),
    parent_id           TEXT REFERENCES tags(id),
    status              TEXT NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'inactive')),
    -- 互斥标签 ID 列表。契约校验在这一层做不了（跨行），服务层负责
    conflicts_with_json TEXT NOT NULL DEFAULT '[]',
    owner_id            TEXT REFERENCES users(id),
    revision            INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX idx_tags_parent ON tags (parent_id);
CREATE INDEX idx_tags_status ON tags (status, name);
-- 同名只在 active 范围内禁止；停用后名字可复用
CREATE UNIQUE INDEX uq_tags_active_name ON tags (name) WHERE status = 'active';

CREATE TRIGGER trg_tags_no_self_parent_insert
BEFORE INSERT ON tags
WHEN NEW.parent_id IS NOT NULL AND NEW.parent_id = NEW.id
BEGIN
    SELECT RAISE(ABORT, 'TAG_PARENT_SELF');
END;

CREATE TRIGGER trg_tags_no_self_parent_update
BEFORE UPDATE OF parent_id ON tags
WHEN NEW.parent_id IS NOT NULL AND NEW.parent_id = NEW.id
BEGIN
    SELECT RAISE(ABORT, 'TAG_PARENT_SELF');
END;

-- ---- 3. ontology_concepts：本体概念树 ------------------------------------

CREATE TABLE ontology_concepts (
    id          TEXT PRIMARY KEY NOT NULL,
    name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    description TEXT NOT NULL DEFAULT ''
                  CHECK (length(description) <= 500),
    parent_id   TEXT REFERENCES ontology_concepts(id),
    status      TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'inactive')),
    owner_id    TEXT REFERENCES users(id),
    revision    INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX idx_concepts_parent ON ontology_concepts (parent_id);
CREATE INDEX idx_concepts_status ON ontology_concepts (status, name);
CREATE UNIQUE INDEX uq_concepts_active_name
    ON ontology_concepts (name) WHERE status = 'active';

CREATE TRIGGER trg_concepts_no_self_parent_insert
BEFORE INSERT ON ontology_concepts
WHEN NEW.parent_id IS NOT NULL AND NEW.parent_id = NEW.id
BEGIN
    SELECT RAISE(ABORT, 'CONCEPT_PARENT_SELF');
END;

CREATE TRIGGER trg_concepts_no_self_parent_update
BEFORE UPDATE OF parent_id ON ontology_concepts
WHEN NEW.parent_id IS NOT NULL AND NEW.parent_id = NEW.id
BEGIN
    SELECT RAISE(ABORT, 'CONCEPT_PARENT_SELF');
END;

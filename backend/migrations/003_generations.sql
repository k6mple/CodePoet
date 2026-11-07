-- 生成快照表。
--
-- 设计取舍：IR 整份存进 ir_json，不拆成 nodes / animations 子表。
-- 理由是生成快照「只整体读写、从不按字段检索」——拆表会把一次快照变成
-- 几十行关联数据，读的时候要 join、写的时候要事务内多次插入，
-- 换来的查询能力却用不上。真正需要按字段查的是知识库和模板，那两张表
-- 在 001 里已经是拆开的。
--
-- 不可变性用触发器强制：契约说生成快照不可变，只靠代码约定不保险，
-- 任何一次 UPDATE 都会被数据库直接拒绝。

CREATE TABLE generations (
    id                      TEXT PRIMARY KEY NOT NULL,
    owner_id                TEXT NOT NULL REFERENCES users(id),
    mode                    TEXT NOT NULL
                            CHECK (mode IN ('template', 'semantic', 'ir', 'subgraph', 'fragment')),
    backend                 TEXT NOT NULL
                            CHECK (backend IN ('turtle', 'canvas2d', 'webgl', 'css')),
    -- 模板来源：只有 template 模式有值。模板版本被归档也不影响历史快照，
    -- 所以这里不加 ON DELETE CASCADE，宁可拦住删除也不丢来源链
    template_version_id     TEXT REFERENCES template_versions(id),
    params_json             TEXT NOT NULL DEFAULT '{}',
    seed                    INTEGER NOT NULL
                            CHECK (seed >= 0 AND seed <= 2147483647),
    ir_json                 TEXT NOT NULL,
    -- 契约要求小写 64 位十六进制 SHA-256
    ir_hash                 TEXT NOT NULL CHECK (length(ir_hash) = 64),
    provenance_json         TEXT NOT NULL,
    validation_report_json  TEXT NOT NULL,
    warnings_json           TEXT NOT NULL DEFAULT '[]',
    -- 未发布来源标记：基于草稿模板生成时为 1，派生快照会继承这个值
    unpublished_source      INTEGER NOT NULL DEFAULT 0
                            CHECK (unpublished_source IN (0, 1)),
    request_id              TEXT NOT NULL,
    created_at              TEXT NOT NULL
);

-- 个人空间按时间倒序列出自己生成的快照，这条索引直接支撑那个查询
CREATE INDEX idx_generations_owner_created ON generations(owner_id, created_at DESC);
CREATE INDEX idx_generations_template ON generations(template_version_id);
-- 复现比较时要按内容哈希找相同结果的快照
CREATE INDEX idx_generations_hash ON generations(ir_hash);

-- 不可变快照：禁止 UPDATE
CREATE TRIGGER generations_forbid_update
BEFORE UPDATE ON generations
BEGIN
    SELECT RAISE(ABORT, 'generations 是不可变快照，不允许修改；请基于它派生新快照');
END;

-- 修正 001 里 supported_backends_json 的默认值笔误。
-- 契约规定 supportedBackends 的元素只能是 canvas2d，001 写成了 canvas。
-- 已应用的迁移不能回头修改（那会让新旧环境的库结构不一致），
-- 所以按迁移纪律追加幂等的修正语句，两张表都有这个字段。
UPDATE template_versions
   SET supported_backends_json = '["canvas2d"]'
 WHERE supported_backends_json = '["canvas"]';

UPDATE templates
   SET supported_backends_json = '["canvas2d"]'
 WHERE supported_backends_json = '["canvas"]';

-- 上面的 UPDATE 只能修已有的行，修不了 DDL 里的 DEFAULT：
-- SQLite 不支持 ALTER COLUMN DEFAULT，001 的笔误会一直留在表定义里，
-- 任何「插入时不显式给这个字段」的语句都会拿到错的 ["canvas"]。
-- 用 AFTER INSERT 触发器兜住这一路：值等于 ["canvas"] 就改回 ["canvas2d"]。
-- 不会误伤——canvas 这个值本身在契约里就不存在，只可能是笔误的产物。
CREATE TRIGGER template_versions_fix_backends
AFTER INSERT ON template_versions
WHEN NEW.supported_backends_json = '["canvas"]'
BEGIN
    UPDATE template_versions
       SET supported_backends_json = '["canvas2d"]'
     WHERE id = NEW.id;
END;

CREATE TRIGGER templates_fix_backends
AFTER INSERT ON templates
WHEN NEW.supported_backends_json = '["canvas"]'
BEGIN
    UPDATE templates
       SET supported_backends_json = '["canvas2d"]'
     WHERE id = NEW.id;
END;

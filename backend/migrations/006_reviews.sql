-- 006_reviews.sql
-- P1A-04：模板/作品审核记录。
--
-- 设计要点：
--
-- 1. 审核记录只追加。一次审核是一个既成事实，事后改理由等于篡改审计轨迹，
--    所以 UPDATE/DELETE 都用触发器挡掉。要纠正结论只能再走一次审核。
-- 2. 禁止自审落成数据库约束，不只写在服务层。控制器、脚本、手工 SQL
--    都可能绕过服务层，而这条规则一旦破了，整个审核体系就退化成摆设。
-- 3. target_id 不加外键：它多态指向 template_versions 或 work_versions，
--    单一外键表达不了。代价是删除版本行时这里会留下孤儿记录——这正是
--    想要的，审核历史不该因为目标被删就消失。
-- 4. target_revision 记录「审核时看到的是哪个版本号」，用于事后核对
--    审核人到底基于哪一版内容做的判断。

CREATE TABLE reviews (
    id               TEXT PRIMARY KEY NOT NULL,
    target_type      TEXT NOT NULL
                       CHECK (target_type IN ('template_version', 'work_version')),
    target_id        TEXT NOT NULL,
    target_revision  INTEGER NOT NULL CHECK (target_revision >= 1),
    submitted_by     TEXT NOT NULL REFERENCES users(id),
    reviewer_id      TEXT NOT NULL REFERENCES users(id),
    decision         TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    reason           TEXT NOT NULL DEFAULT '' CHECK (length(reason) <= 500),
    created_at       TEXT NOT NULL,
    CHECK (submitted_by <> reviewer_id)
);

CREATE INDEX idx_reviews_target ON reviews (target_type, target_id, created_at DESC);
CREATE INDEX idx_reviews_reviewer ON reviews (reviewer_id, created_at DESC);

CREATE TRIGGER reviews_forbid_update
BEFORE UPDATE ON reviews
BEGIN
    SELECT RAISE(ABORT, '审核记录不可修改：要纠正结论请重新审核');
END;

CREATE TRIGGER reviews_forbid_delete
BEFORE DELETE ON reviews
BEGIN
    SELECT RAISE(ABORT, '审核记录不可删除：审核历史必须留痕');
END;

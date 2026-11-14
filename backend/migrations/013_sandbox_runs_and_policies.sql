-- S1-A2：DSL 沙箱作业与执行策略的持久化。
--
-- 编号说明：012 已被 `feat/s0-b2-css-renderer` 的 browser_render_backends 占用
-- （尚未合入 main）。迁移器只按数字排序、不要求连续，所以这里跳到 013 而不是
-- 去抢 012——两个分支同时改编号只会让先合的那个把另一个顶成重复版本。
--
-- 两张表的分工：
--
-- * `sandbox_runs` 是「一次 DSL 执行的可追溯记录」。它同时承担审计和状态机两个
--   职责，因为契约要求**静态拒绝也要留痕**——源码连解释器都没进去就因为有非法
--   op 被拒，同样要能查到「谁、什么时候、拿什么 base、按哪版策略、被哪条规则
--   拒的」。没有 run 行的话，这类拒绝在日志里滚过去就再也拿不出来了。
--
-- * `policies` 是单行表（`id = 1`）。策略必须能改（细则允许管理员调 DSL 限额），
--   所以不能用代码常量当唯一来源；但也不能让管理员随手把限额调到比 core-demo
--   还松——那等于绕过全局 IR 硬限制。因此表里只存「当前值」，「硬上限」是代码
--   常量（`services/policy.py` 的 `HARD_LIMITS`），PATCH 时逐字段比对。
--
-- 源码存哪：`source_json` 存原始源码。它**只在归属者读取时随 run 返回**，
-- 审计日志里只落 `source_hash` 与 `run_id`。把源码倒进 audit_logs 等于让任何
-- 有审计读权限的人都能看到别人写的程序，且无法撤回。

CREATE TABLE sandbox_runs (
    id                   TEXT    PRIMARY KEY,
    owner_id             TEXT    NOT NULL REFERENCES users(id),
    base_generation_id   TEXT    NOT NULL REFERENCES generations(id),

    -- queued / running / succeeded / failed / timed_out / cancelled
    status               TEXT    NOT NULL,

    -- 提交时绑定的策略快照：run 跑起来之后管理员再 PATCH 也不能改它
    policy_revision      INTEGER NOT NULL CHECK (policy_revision >= 1),
    policy_snapshot_json TEXT    NOT NULL,

    -- 小写 64 位十六进制 SHA-256
    source_hash          TEXT    NOT NULL CHECK (length(source_hash) = 64),
    source_json          TEXT    NOT NULL,

    -- 成功时指向新产出的不可变 fragment generation；失败必须为空
    generation_id        TEXT    REFERENCES generations(id),

    metrics_json         TEXT,
    error_json           TEXT,
    -- 静态审计发现（ruleId/line/column/message），有界、脱敏
    audit_findings_json  TEXT    NOT NULL DEFAULT '[]',

    request_id           TEXT    NOT NULL,
    created_at           TEXT    NOT NULL,
    started_at           TEXT,
    finished_at          TEXT,

    CHECK (status IN ('queued', 'running', 'succeeded',
                      'failed', 'timed_out', 'cancelled')),
    -- 状态与产物一致性：succeeded 必须有 generation，非 succeeded 必须没有。
    -- 这条不是形式主义——「失败但挂了个 generationId」会让前端拿一张半成品图
    -- 当成功结果展示，而那张图从来没通过校验。
    CHECK (
        (status =  'succeeded' AND generation_id IS NOT NULL) OR
        (status <> 'succeeded' AND generation_id IS NULL)
    ),
    -- 终态必须带结束时间，未终结的不许提前填 finished_at
    CHECK (
        (status IN ('queued', 'running') AND finished_at IS NULL) OR
        (status IN ('succeeded', 'failed', 'timed_out', 'cancelled')
         AND finished_at IS NOT NULL)
    )
);

-- 并发配额要按「该用户还有几个没跑完」查，走这条索引
CREATE INDEX idx_sandbox_runs_owner_status
    ON sandbox_runs (owner_id, status);

CREATE INDEX idx_sandbox_runs_base
    ON sandbox_runs (base_generation_id);

-- 策略：单行，revision 做乐观锁。
--
-- 初始 values 与 `services/policy.py` 的 `DEFAULT_VALUES` 必须一致，
-- 由 `test_policy.py::DefaultsTests` 读库比对——手抄两份一定会漂，
-- 所以让测试盯着，而不是靠注释提醒。
CREATE TABLE policies (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    revision    INTEGER NOT NULL CHECK (revision >= 1),
    values_json TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    -- 最近一次修改人，便于审计对齐；NULL = 从未被改过（初始行）
    updated_by  TEXT    REFERENCES users(id)
);

INSERT INTO policies (id, revision, values_json, updated_at, updated_by)
VALUES (
    1,
    1,
    -- SQLite 不像 Python 那样支持相邻字符串字面量自动拼接，必须显式 ||
    '{"maxIRNodes":200,"maxVertices":2000,"maxRequestBytes":262144,'
    || '"maxIRBytes":262144,"maxParameters":16,"dslWallTimeMs":2000,'
    || '"dslCpuTimeSeconds":1,"dslMemoryMiB":256,"dslMaxDrawCalls":1000,'
    || '"dslMaxAstNodes":2000,"dslMaxRangeIterations":1,"renderWallTimeMs":120000,'
    || '"turtleMemoryMiB":1024,"browserMemoryMiB":2048,'
    || '"enabledDslFunctions":["circle","polygon","polyline","set_param"]}',
    strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'),
    NULL
);

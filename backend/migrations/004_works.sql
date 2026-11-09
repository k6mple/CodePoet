-- P0-04：作品、作品版本、渲染任务、产物文件
--
-- 设计要点：
-- 1. 产物文件（PNG/ZIP）落磁盘，数据库只存元数据与相对路径。
--    二进制不进 SQLite：备份要拷库，体积会失控，且没法流式读取。
-- 2. render_jobs.manifest_json 存整份清单，不拆帧表。
--    清单只整体读写，从没按单帧检索的场景。
-- 3. works 上冗余 latest/published/display 三个版本指针。
--    列表页每行都要显示当前版本，不冗余就要对每行做子查询（N+1）。
-- 4. work_versions 的 (work_id, version_number) 唯一。
--    版本号靠应用层取 max+1，唯一约束兜住并发写入。

CREATE TABLE artifacts (
    id                TEXT PRIMARY KEY NOT NULL,
    owner_id          TEXT NOT NULL REFERENCES users(id),
    kind              TEXT NOT NULL CHECK (kind IN ('png', 'zip')),
    content_type      TEXT NOT NULL,
    byte_size         INTEGER NOT NULL CHECK (byte_size > 0),
    -- 相对 data/artifacts/ 的路径，不含绝对路径，迁移目录时不失效
    storage_path      TEXT NOT NULL,
    sha256            TEXT NOT NULL CHECK (length(sha256) = 64),
    width             INTEGER CHECK (width IS NULL OR width > 0),
    height            INTEGER CHECK (height IS NULL OR height > 0),
    time_ms           INTEGER CHECK (time_ms IS NULL OR time_ms >= 0),
    render_job_id     TEXT,
    created_at        TEXT NOT NULL
);

CREATE INDEX idx_artifacts_owner ON artifacts (owner_id, created_at DESC);
CREATE INDEX idx_artifacts_job ON artifacts (render_job_id);

CREATE TABLE render_jobs (
    id                     TEXT PRIMARY KEY NOT NULL,
    owner_id               TEXT NOT NULL REFERENCES users(id),
    generation_id          TEXT NOT NULL REFERENCES generations(id),
    backend                TEXT NOT NULL CHECK (backend = 'canvas2d'),
    execution_mode         TEXT NOT NULL CHECK (execution_mode = 'browser_upload'),
    status                 TEXT NOT NULL
                             CHECK (status IN ('queued', 'running', 'succeeded',
                                               'failed', 'timed_out', 'cancelled')),
    phase                  TEXT NOT NULL
                             CHECK (phase IN ('awaiting_frames', 'rendering',
                                              'completed', 'finished')),
    -- 请求的采样时刻列表（JSON 数组），契约限 1~3 个、每个 0~15000
    times_ms_json          TEXT NOT NULL,
    policy_revision        INTEGER NOT NULL DEFAULT 1 CHECK (policy_revision >= 1),
    renderer_version       TEXT,
    client_environment_json TEXT,
    original_job_id        TEXT,
    started_at             TEXT,
    finished_at            TEXT,
    expires_at             TEXT,
    manifest_json          TEXT,
    quality_report_json    TEXT,
    error_json             TEXT,
    revision               INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE INDEX idx_render_jobs_owner ON render_jobs (owner_id, created_at DESC);
CREATE INDEX idx_render_jobs_generation ON render_jobs (generation_id);

CREATE TABLE works (
    id                     TEXT PRIMARY KEY NOT NULL,
    owner_id               TEXT NOT NULL REFERENCES users(id),
    title                  TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 80),
    description            TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    -- 版本指针：列表页免子查询；由服务层在同一事务里维护
    latest_version_id      TEXT,
    published_version_id   TEXT,
    display_version_id     TEXT,
    -- 草稿态的标题/简介，未提交前不影响对外展示
    working_title          TEXT,
    working_description    TEXT,
    thumbnail_artifact_id  TEXT,
    revision               INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE INDEX idx_works_owner ON works (owner_id, updated_at DESC);

CREATE TABLE work_versions (
    id                TEXT PRIMARY KEY NOT NULL,
    work_id           TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    version_number    INTEGER NOT NULL CHECK (version_number >= 1),
    created_by        TEXT NOT NULL REFERENCES users(id),
    status            TEXT NOT NULL
                        CHECK (status IN ('private', 'pending_review',
                                          'published', 'rejected')),
    title             TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 80),
    description       TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    generation_id     TEXT NOT NULL REFERENCES generations(id),
    render_job_id     TEXT NOT NULL REFERENCES render_jobs(id),
    change_note       TEXT NOT NULL DEFAULT '' CHECK (length(change_note) <= 500),
    submitted_by      TEXT,
    submitted_at      TEXT,
    published_at      TEXT,
    last_review_json  TEXT,
    revision          INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (work_id, version_number)
);

CREATE INDEX idx_work_versions_generation ON work_versions (generation_id);
CREATE INDEX idx_work_versions_render_job ON work_versions (render_job_id);

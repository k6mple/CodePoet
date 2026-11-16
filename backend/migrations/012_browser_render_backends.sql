-- S0-A1: preserve existing browser jobs and their work-version references.
-- migrate: foreign_keys_off
-- These PRAGMAs also support standalone executescript consumers. The migration
-- runner moves them outside its atomic transaction and checks all foreign keys.
PRAGMA foreign_keys=OFF;
CREATE TABLE render_jobs_new (
    id                     TEXT PRIMARY KEY NOT NULL,
    owner_id               TEXT NOT NULL REFERENCES users(id),
    generation_id          TEXT NOT NULL REFERENCES generations(id),
    backend                TEXT NOT NULL CHECK (backend IN ('canvas2d', 'css', 'webgl')),
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

INSERT INTO render_jobs_new SELECT * FROM render_jobs;
DROP TABLE render_jobs;
ALTER TABLE render_jobs_new RENAME TO render_jobs;
CREATE INDEX idx_render_jobs_owner ON render_jobs (owner_id, created_at DESC);
CREATE INDEX idx_render_jobs_generation ON render_jobs (generation_id);

PRAGMA foreign_keys=ON;

-- 给旧版本创建的上传任务补上到期时刻。
--
-- 早期实现创建任务是 `status='queued'` 且 `expires_at=NULL`（当时只落状态，
-- 没有到期约定，见问题单 BE-G0-001）。后来额度检查把 queued 也算作未完成，
-- 于是这些任务永久占用「每用户 2 个未完成上传」的名额：既不会过期，也不会
-- 被清理，用户不手工取消就再也建不出新任务，而状态字段上完全看不出原因。
--
-- 这里只补数据、不改语义：按 created_at + 300s 补上到期时刻，是否真的过期
-- 由服务层的惰性清理判断（GET 单个任务或建新任务时标 timed_out）。
-- 迁移本身幂等：只动 expires_at 为 NULL 的未完成任务。

UPDATE render_jobs
SET expires_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', created_at, '+300 seconds')
WHERE expires_at IS NULL
  AND status IN ('queued', 'running');

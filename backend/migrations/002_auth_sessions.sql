-- 002_auth_sessions.sql：服务端认证会话
--
-- 为什么用 DB 表而不是 JWT：
--   1. 契约要求 POST /auth/logout 能立即注销；JWT 签发后服务端无法作废。
--   2. 契约要求匿名会话 30 分钟、登录会话 8 小时，两种时效。
--   3. 登录成功后要「轮换」session 和 CSRF，即换发新 token 并作废旧 token。
--
-- 安全约定：
--   * token 明文只存在于浏览器 Cookie（HttpOnly），库里只存 SHA-256 哈希。
--     这样即使数据库泄露，攻击者也无法直接拿来当 Cookie 用。
--   * csrf_token 存明文——它本来就要发给前端，不算机密；
--     它的作用是证明「请求来自能读到响应的同源页面」，不是保密。
--   * user_id 为 NULL 表示匿名会话，只用于注册/登录前拿到 CSRF token。

CREATE TABLE auth_sessions (
    id           TEXT PRIMARY KEY NOT NULL,
    user_id      TEXT REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL UNIQUE,
    csrf_token   TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('anonymous', 'authenticated')),
    expires_at   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT,
    -- 匿名会话没有用户，登录会话必须有用户，用 CHECK 兜住
    CHECK (
        (kind = 'anonymous' AND user_id IS NULL)
        OR (kind = 'authenticated' AND user_id IS NOT NULL)
    )
);

CREATE INDEX idx_sessions_user ON auth_sessions (user_id);
-- 过期清理按时间扫，需要索引
CREATE INDEX idx_sessions_expires ON auth_sessions (expires_at);

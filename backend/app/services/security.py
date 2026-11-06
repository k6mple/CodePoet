"""密码哈希与会话 token 生成。

设计取舍：用标准库的 PBKDF2-HMAC-SHA256，不引入 bcrypt/argon2 依赖。

原因：这个项目要求 `uv sync --locked` 在任何人机器上都能一次装好，
多一个带 C 扩展的依赖就多一处可能编译失败的地方。PBKDF2 是 NIST SP 800-132
推荐的算法，OWASP 2023 建议 SHA-256 用 600000 轮，安全性对课设项目完全够用。

如果将来要换 argon2id，只需替换本文件的两个函数，调用方无需改动。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# OWASP 2023 对 PBKDF2-HMAC-SHA256 的建议下限
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
TOKEN_BYTES = 32
HASH_PREFIX = "pbkdf2_sha256"


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """返回 ``pbkdf2_sha256$轮数$盐$摘要`` 格式的字符串。

    把轮数和盐一起存进哈希串，是为了将来提高轮数时老密码仍能验证——
    验证时按串里记录的轮数算，不需要强制所有人重设密码。
    """
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return f"{HASH_PREFIX}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间比较，避免通过响应时间反推密码。"""
    try:
        prefix, iterations_text, salt_hex, digest_hex = stored.split("$")
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    if prefix != HASH_PREFIX:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_session_token() -> str:
    """Cookie 里承载的会话明文 token。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """入库前先哈希，数据库泄露也不能直接当 Cookie 用。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    """契约要求 32~256 字符，token_urlsafe(32) 产出 43 字符。"""
    return secrets.token_urlsafe(32)


def digest_equal(left: str, right: str) -> bool:
    """CSRF 等 token 的比较统一走这里，避免逻辑里的 == 被时序攻击利用。"""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))

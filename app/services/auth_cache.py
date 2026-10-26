"""Redis-backed expiring authentication state; never stores passwords."""

import base64
import hashlib
import hmac
import io
import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont
from redis import Redis
from redis.exceptions import RedisError

from app.config import PROJECT_ROOT, get_settings
from app.errors import AppError

PREFIX = "xinyu:auth:"
CAPTCHA_TTL = 180
FAILURE_WINDOW = 900
RATE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""
SESSION_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then return '__revoked__' end
return redis.call('GET', KEYS[2])
"""
CACHE_SESSION_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
return 1
"""


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
        health_check_interval=30,
    )


@contextmanager
def auth_cache() -> Iterator[Redis]:
    try:
        yield get_redis()
    except RedisError:
        raise AppError(
            503, "AUTH_SERVICE_UNAVAILABLE", "账号验证服务暂时不可用，请稍后重试"
        ) from None


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@lru_cache
def captcha_secret() -> bytes:
    folder = PROJECT_ROOT / ".runtime" / "secrets"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "auth-captcha.key"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(secrets.token_bytes(32))
    key = path.read_bytes()
    if len(key) != 32:
        # An initial concurrent request may see the file before its creator finishes writing.
        # Never cache an empty/partial HMAC key; a later request can retry the read.
        raise AppError(503, "AUTH_KEY_UNAVAILABLE", "验证码服务正在准备，请稍后重试")
    return key


def answer_digest(challenge: str, owner: str, code: str) -> str:
    return hmac.new(
        captcha_secret(), f"{challenge}:{owner}:{code.upper()}".encode(), hashlib.sha256
    ).hexdigest()


def consume_rate(key: str, maximum: int, seconds: int, message: str):
    with auth_cache() as cache:
        count, ttl = cache.eval(RATE_SCRIPT, 1, PREFIX + "rate:" + key, seconds)
    if count > maximum:
        raise AppError(
            429, "AUTH_RATE_LIMIT", message, {"retry_after_seconds": max(int(ttl), 1)}
        )


def failure_key(username: str, address: str) -> str:
    return PREFIX + "failures:" + digest(username.lower() + ":" + address)


def check_failures(username: str, address: str):
    with auth_cache() as cache:
        key = failure_key(username, address)
        count = int(cache.get(key) or 0)
        ttl = cache.ttl(key)
    if count >= 5:
        raise AppError(
            429,
            "ACCOUNT_TEMPORARILY_LOCKED",
            "本设备连续验证失败，请稍后重试",
            {"retry_after_seconds": max(ttl, 1)},
        )


def record_failure(username: str, address: str) -> int:
    with auth_cache() as cache:
        count, _ = cache.eval(
            RATE_SCRIPT, 1, failure_key(username, address), FAILURE_WINDOW
        )
    return max(0, 5 - int(count))


def clear_failures(username: str, address: str):
    with auth_cache() as cache:
        cache.delete(failure_key(username, address))


def render_captcha(code: str) -> str:
    image = Image.new("RGB", (360, 112), "#f4f0fb")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 55)
    except OSError:
        font = ImageFont.load_default(size=50)
    for _ in range(5):
        draw.line(
            [
                (secrets.randbelow(360), secrets.randbelow(112)),
                (secrets.randbelow(360), secrets.randbelow(112)),
            ],
            fill="#c8badf",
            width=2,
        )
    for i, letter in enumerate(code):
        tile = Image.new("RGBA", (65, 90), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text(
            (10, 10), letter, font=font, fill=(82 + secrets.randbelow(35), 66, 125, 255)
        )
        tile = tile.rotate(
            secrets.randbelow(25) - 12, resample=Image.Resampling.BICUBIC
        )
        image.paste(tile, (12 + i * 66, 7 + secrets.randbelow(8)), tile)
    for _ in range(90):
        x, y = secrets.randbelow(360), secrets.randbelow(112)
        draw.ellipse((x, y, x + 2, y + 2), fill="#bbadd1")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def create_captcha(owner: str) -> dict:
    challenge = secrets.token_hex(24)
    code = "".join(secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5))
    record = json.dumps(
        {"owner": owner, "answer": answer_digest(challenge, owner, code)}
    )
    with auth_cache() as cache:
        cache.set(PREFIX + "captcha:" + challenge, record, ex=CAPTCHA_TTL)
    return {
        "challenge_id": challenge,
        "image": render_captcha(code),
        "expires_in": CAPTCHA_TTL,
    }


def verify_captcha(owner: str, challenge: str, code: str):
    with auth_cache() as cache:
        value = cache.getdel(PREFIX + "captcha:" + challenge)
    if value:
        record = json.loads(value)
        if record.get("owner") == owner and hmac.compare_digest(
            record["answer"], answer_digest(challenge, owner, code.strip())
        ):
            return
    raise AppError(422, "CAPTCHA_INVALID", "验证码错误、已过期或已使用，请刷新后重试")

"""Shared worker presence, independent of local filesystem layout."""

import json

from app.services.auth_cache import auth_cache

WORKERS_KEY = "xinyu:workers:heartbeats"
HEARTBEAT = """
local clock=redis.call('TIME')
local now=tonumber(clock[1])
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now-30)
redis.call('ZADD',KEYS[1],now,ARGV[1])
redis.call('EXPIRE',KEYS[1],60)
return redis.call('ZCARD',KEYS[1])
"""
PRESENCE = """
local clock=redis.call('TIME')
local now=tonumber(clock[1])
return redis.call('ZCOUNT',KEYS[1],now-30,'+inf')
"""


def write_heartbeat(worker_id: str):
    with auth_cache() as cache:
        cache.eval(HEARTBEAT, 1, WORKERS_KEY, worker_id)


def worker_count() -> int:
    with auth_cache() as cache:
        return int(cache.eval(PRESENCE, 1, WORKERS_KEY))


def remove_heartbeat(worker_id: str):
    with auth_cache() as cache:
        cache.zrem(WORKERS_KEY, worker_id)


def write_progress(job_id: str, lease_token: str, node: str, step: int):
    with auth_cache() as cache:
        cache.set(
            "xinyu:progress:" + job_id,
            json.dumps({"lease": lease_token, "node": node, "model_step": step}),
            ex=1200,
        )


def job_progress(rows) -> dict:
    live = [row for row in rows if row.status == "running" and row.lease_token]
    if not live:
        return {}
    with auth_cache() as cache:
        raw = cache.mget(["xinyu:progress:" + row.id for row in live])
    progress = {}
    for row, value in zip(live, raw, strict=True):
        if value:
            entry = json.loads(value)
            if entry.get("lease") == row.lease_token:
                progress[row.id] = {key: entry[key] for key in ("node", "model_step")}
    return progress

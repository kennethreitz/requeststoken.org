"""Shared Redis snapshots and bounded refresh coordination.

The state hash has no expiry: it contains the latest snapshot and one successful
source-check record per UTC day. Only the lease and retry timer expire.
"""

from __future__ import annotations

import orjson
import re

from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

DEFAULT_PREFIX = "requeststoken:observatory:v1"
LEASE_SECONDS = 120
REFRESH_SECONDS = 3600
ERROR_SECONDS = 600

# Observe the snapshot and acquire its refresh lease in the same operation.
# Redis owns both timers, so different application clocks cannot break the lease.
READ_AND_CLAIM = """
local payload = redis.call('HGET', KEYS[1], 'snapshot') or ''
local acquired = 0
if ARGV[1] == '1' and redis.call('EXISTS', KEYS[3]) == 0 then
    if redis.call('SET', KEYS[2], ARGV[2], 'NX', 'EX', ARGV[3]) then
        acquired = 1
    end
end
return {payload, acquired, redis.call('EXISTS', KEYS[2])}
"""

# A timed-out worker cannot publish, clear another worker's lease, or change its
# backoff. One HSET keeps the snapshot and successful daily check together.
PUBLISH = """
if redis.call('GET', KEYS[2]) ~= ARGV[1] then
    return 0
end
if ARGV[4] ~= '' then
    redis.call('HSET', KEYS[1], 'snapshot', ARGV[2], ARGV[4], ARGV[5])
else
    redis.call('HSET', KEYS[1], 'snapshot', ARGV[2])
end
redis.call('PERSIST', KEYS[1])
redis.call('SET', KEYS[3], '1', 'EX', ARGV[3])
redis.call('DEL', KEYS[2])
return 1
"""


class RedisSnapshotStore:
    def __init__(self, client, prefix=DEFAULT_PREFIX):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", prefix):
            raise ValueError("Invalid observatory Redis prefix")
        self.prefix = prefix
        self.client = client
        tag = "{" + prefix + "}"
        self.state_key = tag + ":state"
        self.lease_key = tag + ":lease"
        self.cooldown_key = tag + ":cooldown"
        self.keys = (self.state_key, self.lease_key, self.cooldown_key)

    @classmethod
    def from_url(cls, url, prefix=DEFAULT_PREFIX):
        # Keep cache failures bounded. Exception messages may contain connection
        # details; callers log only their class, never the URL or credentials.
        client = Redis.from_url(
            url, decode_responses=True, protocol=2,
            socket_connect_timeout=1, socket_timeout=1,
            retry=Retry(NoBackoff(), 0), retry_on_error=[],
            max_connections=16,
        )
        return cls(client, prefix)

    def read_and_claim(self, token, refresh_enabled):
        payload, acquired, refreshing = self.client.eval(
            READ_AND_CLAIM, 3, *self.keys,
            "1" if refresh_enabled else "0", token, LEASE_SECONDS,
        )
        return payload, bool(acquired), bool(refreshing)

    def publish(self, token, envelope, *, check=None):
        check_field = "check:" + check["day"] if check is not None else ""
        return bool(self.client.eval(
            PUBLISH, 3, *self.keys, token,
            orjson.dumps(envelope).decode(),
            REFRESH_SECONDS if check is not None else ERROR_SECONDS,
            check_field, orjson.dumps(check).decode() if check is not None else "",
        ))

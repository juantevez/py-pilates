"""Rate limiting por ventana fija.

Con varios workers de uvicorn el contador tiene que ser compartido: en producción vive en
Redis. Si Redis no responde se deja pasar la request (fail open): el limitador protege la
CPU de abusos, no es razón para tirar el login.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Rate limit excedido; reintentar en {retry_after}s")
        self.retry_after = max(retry_after, 1)


class RateLimiter(ABC):
    @abstractmethod
    def _incr(self, key: str, window: int) -> tuple[int, int]:
        """Suma 1 al contador (creándolo con TTL ``window``). Devuelve (cuenta, segundos restantes)."""

    @abstractmethod
    def _peek(self, key: str) -> tuple[int, int]:
        """(cuenta actual, segundos restantes), sin modificarla."""

    @abstractmethod
    def reset(self, key: str) -> None: ...

    def hit(self, key: str, limit: int, window: int) -> None:
        """Cuenta un intento y lanza RateLimitExceeded si se pasó del límite en la ventana."""
        count, ttl = self._incr(key, window)
        if count > limit:
            raise RateLimitExceeded(ttl)

    def check(self, key: str, limit: int) -> None:
        """Lanza RateLimitExceeded si el contador ya llegó al límite (no suma)."""
        count, ttl = self._peek(key)
        if count >= limit:
            raise RateLimitExceeded(ttl)

    def record(self, key: str, window: int) -> None:
        """Suma un intento sin validar (p. ej. un login fallido)."""
        self._incr(key, window)


class NoopRateLimiter(RateLimiter):
    def _incr(self, key: str, window: int) -> tuple[int, int]:
        return 0, 0

    def _peek(self, key: str) -> tuple[int, int]:
        return 0, 0

    def reset(self, key: str) -> None:
        pass


class InMemoryRateLimiter(RateLimiter):
    """Para tests y desarrollo con un solo proceso."""

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._counters: dict[str, tuple[int, float]] = {}

    def _live(self, key: str) -> tuple[int, float] | None:
        entry = self._counters.get(key)
        if entry and entry[1] <= self._now():
            del self._counters[key]
            return None
        return entry

    def _incr(self, key: str, window: int) -> tuple[int, int]:
        with self._lock:
            count, expires = self._live(key) or (0, self._now() + window)
            self._counters[key] = (count + 1, expires)
            return count + 1, int(expires - self._now() + 0.999)

    def _peek(self, key: str) -> tuple[int, int]:
        with self._lock:
            entry = self._live(key)
            return (entry[0], int(entry[1] - self._now() + 0.999)) if entry else (0, 0)

    def reset(self, key: str) -> None:
        with self._lock:
            self._counters.pop(key, None)


class RedisRateLimiter(RateLimiter):
    PREFIX = "ratelimit:"
    PAUSE_AFTER_FAILURE = 30.0  # segundos sin consultar Redis tras un error: sólo una request paga el timeout

    def __init__(self, redis: Redis, now: Callable[[], float] = time.monotonic) -> None:
        self._r, self._now = redis, now
        self._paused_until = 0.0

    @classmethod
    def from_url(cls, url: str) -> RedisRateLimiter:
        return cls(Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5, retry=Retry(NoBackoff(), 0)))

    def _run(self, build: Callable, default):
        if self._now() < self._paused_until:
            return default
        try:
            pipe = self._r.pipeline()
            build(pipe)
            return pipe.execute()
        except RedisError:
            self._paused_until = self._now() + self.PAUSE_AFTER_FAILURE
            logger.warning(
                "Rate limiter sin Redis: se deja pasar sin límite por %.0f s", self.PAUSE_AFTER_FAILURE, exc_info=True
            )
            return default

    def _incr(self, key: str, window: int) -> tuple[int, int]:
        k = self.PREFIX + key

        def build(pipe):
            pipe.incr(k)
            pipe.expire(k, window, nx=True)
            pipe.ttl(k)

        count, _, ttl = self._run(build, (0, None, 0))
        return int(count), int(ttl)

    def _peek(self, key: str) -> tuple[int, int]:
        k = self.PREFIX + key

        def build(pipe):
            pipe.get(k)
            pipe.ttl(k)

        count, ttl = self._run(build, (0, 0))
        return int(count or 0), int(ttl)

    def reset(self, key: str) -> None:
        self._run(lambda pipe: pipe.delete(self.PREFIX + key), None)

from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import Request

from app.core.errors import DomainError


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = monotonic()
        threshold = now - window_seconds
        with self._lock:
            attempts = self._attempts[key]
            while attempts and attempts[0] <= threshold:
                attempts.popleft()
            if len(attempts) >= limit:
                raise DomainError(
                    "TOO_MANY_ATTEMPTS",
                    "尝试次数过多，请稍后再试。",
                    429,
                )
            attempts.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()


auth_attempt_limiter = SlidingWindowLimiter()


def auth_attempt_key(request: Request, action: str, identity: str) -> str:
    client_host = request.client.host if request.client else "unknown"
    return f"{action}:{client_host}:{identity.strip().lower()}"


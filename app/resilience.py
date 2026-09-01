from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from app.domain.errors import TransientProviderError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    timeout_seconds: float = 10.0
    base_delay_seconds: float = 0.05


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    retryable: tuple[type[BaseException], ...] = (TransientProviderError, TimeoutError),
) -> T:
    """Bound retries by count and per-attempt timeout; never retry permanent validation errors."""

    last_error: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            async with asyncio.timeout(policy.timeout_seconds):
                return await operation()
        except retryable as exc:
            last_error = exc
            if attempt == policy.attempts:
                raise
            await asyncio.sleep(policy.base_delay_seconds * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error

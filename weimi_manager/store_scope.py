"""Store selection rules for background network work."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TypeVar


class _StoreLike(Protocol):
    web_status: str


StoreT = TypeVar("StoreT", bound=_StoreLike)


def logged_in_stores(stores: Iterable[StoreT]) -> list[StoreT]:
    """Return only stores whose official web session is currently usable."""

    return [
        store
        for store in stores
        if str(getattr(store, "web_status", "") or "").strip() == "已登录"
    ]

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SkuCandidate:
    sku_id: str
    price_cents: int
    stock: int
    specs: Mapping[str, str] = field(default_factory=dict)
    available: bool = True
    complete: bool = True
    limit_min: int = 1
    limit_max: int | None = None
    default: bool = False


@dataclass(frozen=True, slots=True)
class SkuSelection:
    selected: SkuCandidate | None
    quantity: int
    reason_code: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.selected is not None

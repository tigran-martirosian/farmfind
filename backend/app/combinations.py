"""Brute-force package-count combination search."""
from __future__ import annotations

import math
from itertools import product as iterproduct

from .models import Product
from .pricing import EPS, effective_price
from .schemas import OptimizeRequest


def best_combination(
    packages: list[tuple[Product, float]],
    requested: float,
    max_overbuy_pct: float,
    request: OptimizeRequest,
) -> tuple[float, list[tuple[Product, int, float]]] | None:
    """Cheapest count-combination meeting requested quantity within overbuy."""
    max_total = requested * (1.0 + max_overbuy_pct / 100.0)
    caps = [max(1, math.ceil(requested / q - EPS)) for _, q in packages]

    best_cost: float | None = None
    best_combo: list[tuple[Product, int, float]] | None = None

    for counts in iterproduct(*(range(c + 1) for c in caps)):
        total_qty = sum(counts[i] * packages[i][1] for i in range(len(packages)))
        if total_qty + EPS < requested or total_qty > max_total + EPS:
            continue
        cost = sum(
            counts[i] * effective_price(packages[i][0], request)[0]
            for i in range(len(packages))
        )
        if best_cost is None or cost < best_cost - EPS:
            best_cost = cost
            best_combo = [
                (packages[i][0], counts[i], packages[i][1])
                for i in range(len(packages))
                if counts[i] > 0
            ]

    if best_cost is None or best_combo is None:
        return None
    return best_cost, best_combo

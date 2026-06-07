"""
toolpath.py
Sort pads into an optimised visit order using nearest-neighbour TSP.
"""

from __future__ import annotations
import math
from pad_model import Pad, PadType


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


def nearest_neighbour(
    pads: list[Pad],
    start_x: float = 0.0,
    start_y: float = 0.0,
) -> list[Pad]:
    """
    Greedy nearest-neighbour sort starting from (start_x, start_y).
    Returns only active (non-blacklisted, non-filled) pads in visit order.
    Uses index-based removal to avoid O(n) list.remove() scans.
    """
    candidates = [p for p in pads if not p.blacklisted and not p.filled]
    if not candidates:
        return []

    ordered: list[Pad] = []
    remaining = list(range(len(candidates)))
    cx, cy = start_x, start_y

    while remaining:
        best_i   = min(remaining,
                       key=lambda i: _dist(cx, cy,
                                           candidates[i].center_x,
                                           candidates[i].center_y))
        nearest  = candidates[best_i]
        ordered.append(nearest)
        remaining.remove(best_i)
        cx, cy = nearest.center_x, nearest.center_y

    return ordered


def estimate_travel_distance(
    ordered: list[Pad],
    start_x: float = 0.0,
    start_y: float = 0.0,
) -> float:
    """Return total XY travel distance for the given pad order (mm)."""
    total = 0.0
    cx, cy = start_x, start_y
    for p in ordered:
        total += _dist(cx, cy, p.center_x, p.center_y)
        cx, cy = p.center_x, p.center_y
    return total


def check_dense_pads(
    pads: list[Pad],
    clearance_threshold: float = 0.5,
) -> list[tuple[Pad, Pad]]:
    """
    Find pairs of active pads whose edges are closer than clearance_threshold mm.
    Checks all pad types (DOT and DRAG) against each other.
    Returns a list of (pad_a, pad_b) pairs for user review.
    """
    active = [p for p in pads if not p.blacklisted]
    flagged: list[tuple[Pad, Pad]] = []

    for i, a in enumerate(active):
        for b in active[i + 1:]:
            center_dist = _dist(a.center_x, a.center_y, b.center_x, b.center_y)
            # Approximate edge-to-edge: subtract half the diagonal of each pad's bbox
            half_a = math.hypot(a.width, a.height) / 2
            half_b = math.hypot(b.width, b.height) / 2
            edge_dist = center_dist - half_a - half_b
            if edge_dist < clearance_threshold:
                flagged.append((a, b))

    return flagged

"""
gerber_parser.py
Parse a Gerber paste layer (.GTP / .GBP) into a list of Pad objects.

pcb-tools returns flashed pads directly as Rectangle / Circle / Obround
primitives with a .position attribute — no Flash wrapper needed.
"""

from __future__ import annotations
import re
from typing import Optional

import gerber
from gerber.primitives import Rectangle, Circle, Obround

from pad_model import Pad, PadType, BlacklistEntry


# ── Pad builders ──────────────────────────────────────────────────────────────

def _make_rect_pad(cx: float, cy: float, w: float, h: float) -> Pad:
    """
    Rectangle aperture:
      w ≈ h  → DOT (square pad, single deposit)
      w > h  → DRAG horizontally
      h > w  → DRAG vertically
    """
    if abs(w - h) < 0.01:
        return Pad(PadType.DOT, cx, cy, w, h)
    if w >= h:
        half = w / 2
        return Pad(PadType.DRAG, cx, cy, w, h,
                   start_x=cx - half, start_y=cy,
                   end_x=cx + half,   end_y=cy)
    else:
        half = h / 2
        return Pad(PadType.DRAG, cx, cy, w, h,
                   start_x=cx, start_y=cy - half,
                   end_x=cx,   end_y=cy + half)


def _make_obround_pad(cx: float, cy: float, w: float, h: float) -> Pad:
    """
    Obround (stadium): drag only the straight centre section.
    Straight length = long_axis - short_axis.
    Falls back to DOT if w == h (circular obround).
    """
    if abs(w - h) < 0.01:
        # Circular obround — treat as dot
        return Pad(PadType.DOT, cx, cy, w, h)
    if w >= h:
        half = (w - h) / 2
        return Pad(PadType.DRAG, cx, cy, w, h,
                   start_x=cx - half, start_y=cy,
                   end_x=cx + half,   end_y=cy)
    else:
        half = (h - w) / 2
        return Pad(PadType.DRAG, cx, cy, w, h,
                   start_x=cx, start_y=cy - half,
                   end_x=cx,   end_y=cy + half)


# ── Test-point detection ──────────────────────────────────────────────────────

def find_test_point_from_netlist(netlist_path: str) -> Optional[tuple[float, float]]:
    """
    Scan a KiCad netlist (.net) or IPC-D-356 file for the first TP* reference.
    Returns (x, y) in mm, or None if not found.
    """
    if not netlist_path:
        return None

    try:
        with open(netlist_path, "r", errors="replace") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"[warn] Netlist not found: {netlist_path}")
        return None

    # KiCad S-expression netlist: (comp (ref TP1) ... (xy 12.4 8.7))
    m = re.search(
        r'\(comp\s+\(ref\s+(TP\w+)\).*?\(xy\s+([\d.\-]+)\s+([\d.\-]+)\)',
        text, re.DOTALL)
    if m:
        x, y = float(m.group(2)), float(m.group(3))
        print(f"[info] Found test point {m.group(1)} at ({x:.3f}, {y:.3f})")
        return (x, y)

    # IPC-D-356 netlist (coords in 1/10000 inch → mm)
    m = re.search(
        r'^3[12]7\w+\s+TP\w*\s+.*?X([+-]?\d+)Y([+-]?\d+)',
        text, re.MULTILINE)
    if m:
        x = int(m.group(1)) * 0.00254
        y = int(m.group(2)) * 0.00254
        print(f"[info] Found IPC-D-356 test point at ({x:.3f}, {y:.3f})")
        return (x, y)

    print("[warn] No test point found in netlist — will suggest largest pad.")
    return None


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_paste_layer(
    gerber_path: str,
    netlist_path: str = "",
    blacklist: list[BlacklistEntry] = None,
    blacklist_tolerance: float = 0.2,
) -> tuple[list[Pad], Optional[tuple[float, float]]]:
    """
    Parse a Gerber paste layer file into Pad objects.

    Returns:
        pads           — all pads; blacklisted ones are marked, not removed
        priming_coords — (x, y) of detected test point, or largest-pad fallback
    """
    blacklist = blacklist or []
    print(f"[info] Parsing {gerber_path} ...")

    layer  = gerber.read(gerber_path)
    pads: list[Pad] = []
    skipped = 0

    for prim in layer.primitives:
        cx, cy = prim.position

        try:
            if isinstance(prim, Rectangle):
                pad = _make_rect_pad(cx, cy, float(prim.width), float(prim.height))

            elif isinstance(prim, Circle):
                d = float(prim.diameter)
                pad = Pad(PadType.DOT, cx, cy, d, d)

            elif isinstance(prim, Obround):
                pad = _make_obround_pad(cx, cy, float(prim.width), float(prim.height))

            else:
                # Polygon / macro aperture — use bounding box as dot
                bb = getattr(prim, 'bounding_box', None)
                if bb:
                    w = bb[1][0] - bb[0][0]
                    h = bb[1][1] - bb[0][1]
                else:
                    w = h = 1.0
                pad = Pad(PadType.DOT, cx, cy, w, h)

        except Exception as e:
            print(f"[warn] Skipping primitive at ({cx:.3f}, {cy:.3f}): {e}")
            skipped += 1
            continue

        # Mark against blacklist (matched by coordinate proximity)
        for entry in blacklist:
            if pad.matches_coord(entry.x, entry.y, blacklist_tolerance):
                pad.blacklisted = True
                pad.label = entry.label
                break

        pads.append(pad)

    active = sum(1 for p in pads if not p.blacklisted)
    print(f"[info] {len(pads)} pads found "
          f"({active} active, {len(pads) - active} blacklisted"
          + (f", {skipped} skipped)" if skipped else ")"))

    # Priming pad: netlist test point → fallback to largest active pad
    priming = find_test_point_from_netlist(netlist_path)
    if priming is None:
        active_pads = [p for p in pads if not p.blacklisted]
        if active_pads:
            largest = max(active_pads, key=lambda p: p.area)
            print(f"[warn] Using largest pad as priming suggestion: "
                  f"({largest.center_x:.3f}, {largest.center_y:.3f})"
                  f"  {largest.area:.3f} mm²")
            priming = (largest.center_x, largest.center_y)

    return pads, priming


def apply_origin_shift(
    pads: list[Pad],
    origin_x: float,
    origin_y: float,
) -> list[Pad]:
    """
    Subtract (origin_x, origin_y) from all pad coordinates so the priming pad
    becomes (0, 0). Mutates the list in-place and returns it.
    """
    for p in pads:
        p.center_x -= origin_x
        p.center_y -= origin_y
        if p.pad_type == PadType.DRAG:
            p.start_x -= origin_x
            p.start_y -= origin_y
            p.end_x   -= origin_x
            p.end_y   -= origin_y
    return pads

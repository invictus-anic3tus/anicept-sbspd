"""
gcode_gen.py
Convert an ordered list of Pad objects into a Klipper-compatible G-code file.

Motion:    all relative (G91) for the entire file until the final park move
Dispenser: triggered via RUN_SHELL_COMMAND per pad (single blocking call each time)

Z notes
-------
The printer is in G91 (relative) mode throughout. z_travel and z_dispense in
the config are *absolute* positions relative to the manual tip home, so the
correct relative moves are fixed deltas derived once from those two values:

  raise delta = z_travel   - z_dispense   (always positive, e.g. 5.0 - (-0.05) = 5.05)
  lower delta = z_dispense - z_travel     (always negative, e.g. -0.05 - 5.0  = -5.05)

XY tracking is still needed to compute relative deltas between pads.
"""

from __future__ import annotations
import math
import os
from datetime import datetime

from pad_model import Pad, PadType, DispenserConfig


# ── Helpers ───────────────────────────────────────────────────────────────────

def _f(v: float, digits: int = 4) -> str:
    """Format a float for G-code, stripping unnecessary trailing zeros."""
    return f"{v:.{digits}f}".rstrip("0").rstrip(".")


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


# ── G-code builder ────────────────────────────────────────────────────────────

class GCodeBuilder:
    def __init__(self, cfg: DispenserConfig):
        self.cfg = cfg
        self.lines: list[str] = []

        # XY position tracking for relative move deltas.
        # Origin (0, 0) is the manually-jogged priming pad position.
        self._cx = 0.0
        self._cy = 0.0

        # Fixed Z deltas, computed once from config.
        # The nozzle starts at the priming pad (Z = 0 in relative terms),
        # which we treat as being at z_travel height before the first lower.
        self._z_raise = cfg.z_travel - cfg.z_dispense   # e.g.  5.0 - (-0.05) =  5.05
        self._z_lower = cfg.z_dispense - cfg.z_travel   # e.g. -0.05 - 5.0    = -5.05

    # ── Primitives ────────────────────────────────────────────────────────────

    def comment(self, text: str = ""):
        self.lines.append(f"; {text}" if text else ";")

    def raw(self, line: str):
        self.lines.append(line)

    def move_xy(self, x: float, y: float, speed: float, comment: str = ""):
        """Emit a G0 XY move using relative deltas from current tracked position."""
        dx = x - self._cx
        dy = y - self._cy
        self._cx = x
        self._cy = y

        parts = ["G0"]
        if dx != 0:
            parts.append(f"X{_f(dx)}")
        if dy != 0:
            parts.append(f"Y{_f(dy)}")
        parts.append(f"F{int(speed)}")

        if len(parts) == 2:   # only "G0 F..." — no actual motion, skip
            return

        line = " ".join(parts)
        if comment:
            line += f"   ; {comment}"
        self.lines.append(line)

    def dwell(self, ms: int, comment: str = ""):
        line = f"G4 P{ms}"
        if comment:
            line += f"   ; {comment}"
        self.lines.append(line)

    def shell_cmd(self, func: str, *args, comment: str = ""):
        """Emit RUN_SHELL_COMMAND for the configured Klipper shell command."""
        params = " ".join(str(a) for a in args)
        line = f'RUN_SHELL_COMMAND CMD={self.cfg.klipper_cmd} PARAMS="{func} {params}"'
        if comment:
            line += f"   ; {comment}"
        self.lines.append(line)

    # ── Z helpers — always correct fixed deltas in G91 ────────────────────────

    def raise_to_travel(self, comment: str = "raise to travel height"):
        self.lines.append(
            f"G0 Z{_f(self._z_raise)} F{int(self.cfg.travel_speed)}   ; {comment}"
        )

    def lower_to_dispense(self, comment: str = "lower to dispense height"):
        self.lines.append(
            f"G0 Z{_f(self._z_lower)} F{int(self.cfg.travel_speed)}   ; {comment}"
        )

    # ── Dispenser helpers ─────────────────────────────────────────────────────

    def retract_paste(self, comment: str = "retract"):
        self.shell_cmd("retract", _f(self.cfg.retract_mm),
                       int(self.cfg.dispenser_hz), comment=comment)

    def deretract_paste(self, comment: str = "deretract"):
        self.shell_cmd("deretract", _f(self.cfg.deretract_mm),
                       int(self.cfg.dispenser_hz), comment=comment)

    # ── Sequences ─────────────────────────────────────────────────────────────

    def header(self, pad_count: int):
        c = self.cfg
        self.comment("=" * 62)
        self.comment(f" Solder paste dispenser — {datetime.now():%Y-%m-%d %H:%M}")
        self.comment(f" Source  : {os.path.basename(c.gerber_file)}")
        self.comment(f" Pads    : {pad_count}")
        self.comment(f" Z       : travel={c.z_travel}  dispense={c.z_dispense}"
                     f"  (raise={_f(self._z_raise)}, lower={_f(self._z_lower)})")
        self.comment(f" Volumes : dot={c.dot_volume_ul} uL  drag={c.drag_ul_per_mm} uL/mm")
        self.comment("=" * 62)
        self.comment()
        self.comment("SETUP: G28 -> attach dispenser -> jog XY over priming pad,")
        self.comment("       jog Z down until tip just touches pad surface -> run")
        self.comment()
        self.raw("G91")   # relative positioning -- stays active until park
        self.raise_to_travel("safety raise -- normalise Z from jog position to travel height")

    def prime(self):
        c = self.cfg
        self.comment()
        self.comment("-- Prime ----------------------------------------------------------")
        self.lower_to_dispense("lower to prime position")
        self.shell_cmd("prime", _f(c.prime_volume_ul), int(c.dispenser_hz),
                       comment=f"prime {c.prime_volume_ul} uL")
        self.dwell(c.prime_dwell_ms, "let paste settle")
        self.retract_paste()
        self.raise_to_travel()
        self.comment()

    def dispense_dot(self, pad: Pad, index: int):
        c = self.cfg
        self.comment(f"-- Pad {index:03d} DOT  ({_f(pad.center_x)}, {_f(pad.center_y)})"
                     f"  {_f(pad.width)}x{_f(pad.height)} mm")
        self.move_xy(pad.center_x, pad.center_y, c.travel_speed, comment="travel")
        self.lower_to_dispense()
        self.deretract_paste()
        self.shell_cmd("dispense", _f(c.dot_volume_ul), int(c.dispenser_hz),
                       comment=f"{c.dot_volume_ul} uL")
        self.dwell(c.dot_dwell_ms, "dwell")
        self.retract_paste()
        self.raise_to_travel()
        self.comment()

    def dispense_drag(self, pad: Pad, index: int):
        c = self.cfg
        drag_len     = _dist(pad.start_x, pad.start_y, pad.end_x, pad.end_y)
        syringe_area = math.pi * (c.syringe_id_mm / 2) ** 2
        extrude_mm   = (drag_len * c.drag_ul_per_mm) / syringe_area

        self.comment(f"-- Pad {index:03d} DRAG ({_f(pad.start_x)},{_f(pad.start_y)})"
                     f"->({_f(pad.end_x)},{_f(pad.end_y)})  len={_f(drag_len)} mm")
        self.move_xy(pad.start_x, pad.start_y, c.travel_speed, comment="travel to drag start")
        self.lower_to_dispense()
        self.deretract_paste()

        self.shell_cmd("dispense_drag", _f(extrude_mm), int(c.dispenser_hz),
                       comment=f"extrude for {_f(drag_len)} mm drag")
        dx = pad.end_x - pad.start_x
        dy = pad.end_y - pad.start_y
        self.lines.append(
            f"G1 X{_f(dx)} Y{_f(dy)} F{int(c.dispense_speed)}   ; drag"
        )
        self._cx = pad.end_x
        self._cy = pad.end_y

        self.dwell(c.dot_dwell_ms, "dwell")
        self.retract_paste()
        self.raise_to_travel()
        self.comment()

    def park(self):
        c = self.cfg
        self.comment("-- Done -----------------------------------------------------------")
        self.raw("G90")   # back to absolute for a deterministic park position
        self.raw(f"G0 X{_f(c.park_x)} Y{_f(c.park_y)} Z{_f(c.park_z)}"
                 f" F{int(c.travel_speed)}   ; park")
        self.comment("Dispense complete.")

    def build(self) -> str:
        return "\n".join(self.lines)


# ── Public entry point ────────────────────────────────────────────────────────

def generate_gcode(
    ordered_pads: list[Pad],
    cfg: DispenserConfig,
    output_path: str,
) -> str:
    """
    Generate a complete G-code file and write it to output_path.
    Returns the G-code string.
    """
    active  = [p for p in ordered_pads if not p.blacklisted]
    builder = GCodeBuilder(cfg)

    builder.header(len(active))
    builder.prime()

    for i, pad in enumerate(active, start=1):
        if pad.pad_type == PadType.DRAG:
            builder.dispense_drag(pad, i)
        else:
            builder.dispense_dot(pad, i)

    builder.park()

    gcode = builder.build()
    with open(output_path, "w") as f:
        f.write(gcode)

    print(f"[info] G-code written -> {output_path}"
          f"  ({len(active)} pads, {len(gcode.splitlines())} lines)")
    return gcode

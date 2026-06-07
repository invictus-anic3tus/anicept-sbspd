"""
gcode_gen.py
Convert an ordered list of Pad objects into a Klipper-compatible G-code file.

Motion:    all relative (G91) until the final park move
Dispenser: triggered via RUN_SHELL_COMMAND per pad (single blocking call each time)
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
        # Track position so we can emit relative deltas correctly.
        # All Z values are treated as absolute targets; move() computes the delta.
        self._cx = 0.0
        self._cy = 0.0
        self._cz = 0.0

    # ── Primitives ────────────────────────────────────────────────────────────

    def comment(self, text: str = ""):
        self.lines.append(f"; {text}" if text else ";")

    def raw(self, line: str):
        self.lines.append(line)

    def move(self, x: float = None, y: float = None, z: float = None,
             speed: float = None, comment: str = ""):
        """
        Emit a G0 rapid move. All axes are absolute targets; the delta vs.
        current tracked position is computed and emitted (G91 mode on printer).
        Skips axes that haven't changed.
        """
        parts = ["G0"]
        if x is not None:
            dx = x - self._cx
            if dx != 0:
                parts.append(f"X{_f(dx)}")
            self._cx = x
        if y is not None:
            dy = y - self._cy
            if dy != 0:
                parts.append(f"Y{_f(dy)}")
            self._cy = y
        if z is not None:
            dz = z - self._cz
            if dz != 0:
                parts.append(f"Z{_f(dz)}")
            self._cz = z
        if speed is not None:
            parts.append(f"F{int(speed)}")

        # Don't emit a bare "G0" with no axes — that's a no-op
        if len(parts) == 1:
            return
        # Don't emit "G0 F..." with no motion — use M220 or just skip
        if len(parts) == 2 and parts[1].startswith("F"):
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

    # ── Z helpers ─────────────────────────────────────────────────────────────

    def raise_to_travel(self, comment: str = "raise to travel height"):
        self.move(z=self.cfg.z_travel, speed=self.cfg.travel_speed, comment=comment)

    def lower_to_dispense(self, comment: str = "lower to dispense height"):
        self.move(z=self.cfg.z_dispense, speed=self.cfg.travel_speed, comment=comment)

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
        self.comment(f" Z       : travel={c.z_travel}  dispense={c.z_dispense}")
        self.comment(f" Volumes : dot={c.dot_volume_ul} µL  drag={c.drag_ul_per_mm} µL/mm")
        self.comment("=" * 62)
        self.comment()
        self.comment("SETUP: G28 → attach dispenser → jog tip to priming pad → run")
        self.comment()
        self.raw("G91")   # relative positioning for all dispense moves

    def prime(self):
        c = self.cfg
        self.comment("── Prime ──────────────────────────────────────────────────")
        # Go straight to dispense height — no intermediate contact stop
        self.lower_to_dispense("lower to prime")
        self.shell_cmd("prime", _f(c.prime_volume_ul), int(c.dispenser_hz),
                       comment=f"prime {c.prime_volume_ul} µL")
        self.dwell(c.prime_dwell_ms, "let paste settle")
        self.retract_paste()
        self.raise_to_travel()
        self.comment()

    def dispense_dot(self, pad: Pad, index: int):
        c = self.cfg
        self.comment(f"── Pad {index:03d} DOT  ({_f(pad.center_x)}, {_f(pad.center_y)})"
                     f"  {_f(pad.width)}×{_f(pad.height)} mm")
        self.move(x=pad.center_x, y=pad.center_y,
                  speed=c.travel_speed, comment="travel")
        self.lower_to_dispense()
        self.deretract_paste()
        self.shell_cmd("dispense", _f(c.dot_volume_ul), int(c.dispenser_hz),
                       comment=f"{c.dot_volume_ul} µL")
        self.dwell(c.dot_dwell_ms, "dwell")
        self.retract_paste()
        self.raise_to_travel()
        self.comment()

    def dispense_drag(self, pad: Pad, index: int):
        c = self.cfg
        drag_len = _dist(pad.start_x, pad.start_y, pad.end_x, pad.end_y)

        # Volume of paste to extrude during the drag, converted to plunger mm
        import math as _math
        syringe_area = _math.pi * (c.syringe_id_mm / 2) ** 2
        extrude_mm   = (drag_len * c.drag_ul_per_mm) / syringe_area

        self.comment(f"── Pad {index:03d} DRAG ({_f(pad.start_x)},{_f(pad.start_y)})"
                     f"→({_f(pad.end_x)},{_f(pad.end_y)})  len={_f(drag_len)} mm")

        # Travel to drag start
        self.move(x=pad.start_x, y=pad.start_y,
                  speed=c.travel_speed, comment="travel to drag start")
        self.lower_to_dispense()
        self.deretract_paste()

        # The dispense_drag shell command is a single blocking call.
        # It runs the motor for the full extrude distance while Klipper
        # simultaneously executes the G1 drag move on the next line.
        # Both finish in roughly the same time if speeds are matched.
        self.shell_cmd("dispense_drag", _f(extrude_mm), int(c.dispenser_hz),
                       comment=f"extrude {drag_len:.2f} mm drag")
        dx = pad.end_x - pad.start_x
        dy = pad.end_y - pad.start_y
        self.lines.append(
            f"G1 X{_f(dx)} Y{_f(dy)} F{int(c.dispense_speed)}   ; drag"
        )
        self._cx = pad.end_x
        self._cy = pad.end_y

        self.dwell(100, "dwell")
        self.retract_paste()
        self.raise_to_travel()
        self.comment()

    def park(self):
        c = self.cfg
        self.comment("── Done ───────────────────────────────────────────────────")
        self.raise_to_travel("final raise")
        self.raw("G90")   # back to absolute for deterministic park
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
    active = [p for p in ordered_pads if not p.blacklisted]
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

    print(f"[info] G-code written → {output_path}"
          f"  ({len(active)} pads, {len(gcode.splitlines())} lines)")
    return gcode

"""
pad_model.py
Dataclasses for pads and project config.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional
import json


class PadType(Enum):
    DOT  = auto()   # square / circle — single deposit at centre
    DRAG = auto()   # rectangle / obround — nozzle dragged from start to end


@dataclass
class Pad:
    """
    A single solder-paste pad.

    DOT pads:  deposit at (center_x, center_y); width and height may differ
               for non-square rectangles that fall below the aspect threshold.
    DRAG pads: nozzle travels from (start_x, start_y) to (end_x, end_y);
               width is the aperture dimension perpendicular to travel.
    """
    pad_type:    PadType
    center_x:    float
    center_y:    float
    width:       float        # aperture width  (mm)
    height:      float        # aperture height (mm)

    # DRAG only — None for DOT pads
    start_x:     Optional[float] = None
    start_y:     Optional[float] = None
    end_x:       Optional[float] = None
    end_y:       Optional[float] = None

    # Runtime state
    filled:      bool = False
    blacklisted: bool = False
    label:       str  = ""

    @property
    def area(self) -> float:
        return self.width * self.height

    def matches_coord(self, x: float, y: float, tolerance: float = 0.1) -> bool:
        """True if this pad's centre is within tolerance mm of (x, y)."""
        return abs(self.center_x - x) < tolerance and abs(self.center_y - y) < tolerance

    def __repr__(self) -> str:
        if self.pad_type == PadType.DRAG:
            return (f"Pad(DRAG ({self.start_x:.3f},{self.start_y:.3f})"
                    f"→({self.end_x:.3f},{self.end_y:.3f})"
                    f" w={self.width:.3f})")
        return (f"Pad(DOT @ ({self.center_x:.3f},{self.center_y:.3f})"
                f" {self.width:.3f}×{self.height:.3f})")


@dataclass
class BlacklistEntry:
    x:     float
    y:     float
    label: str = ""


@dataclass
class DispenserConfig:
    """
    Full project configuration.
    Generated as a template by `parse`, edited by the user, then consumed by `run`.
    """

    # ── Files ─────────────────────────────────────────────────────────────────
    gerber_file:       str   = ""
    netlist_file:      str   = ""

    # ── Priming pad (in original Gerber coordinates, before origin shift) ─────
    priming_x:         float = 0.0
    priming_y:         float = 0.0

    # ── Z positions (relative to manual tip home) ─────────────────────────────
    z_travel:          float = 5.0      # safe XY travel height (mm above home)
    z_dispense:        float = -5.05    # offset from 3DP nozzle to paste tip

    # ── Dispenser volumes and distances ───────────────────────────────────────
    prime_volume_ul:   float = 3.0      # µL to prime
    dot_volume_ul:     float = 2.0      # µL per dot pad
    drag_ul_per_mm:    float = 1.0      # µL per mm of drag travel
    retract_mm:        float = 0.15     # anti-drool retract after each deposit
    deretract_mm:      float = 0.15     # deretract before each deposit

    # ── Dwell times ───────────────────────────────────────────────────────────
    prime_dwell_ms:    int   = 500      # ms after priming
    dot_dwell_ms:      int   = 150      # ms after each dot deposit

    # ── Speeds ────────────────────────────────────────────────────────────────
    travel_speed:      float = 3000.0   # mm/min — printer XY travel
    dispense_speed:    float = 500.0    # mm/min — drag move speed
    dispenser_hz:      float = 300.0    # steps/sec — motor speed

    # ── Syringe geometry ──────────────────────────────────────────────────────
    syringe_id_mm:     float = 5.5      # inner diameter of syringe barrel (mm)

    # ── Blacklist ─────────────────────────────────────────────────────────────
    blacklist: list[BlacklistEntry] = field(default_factory=list)

    # ── Park position (absolute, post-dispense) ───────────────────────────────
    park_x:            float = 0.0
    park_y:            float = 0.0
    park_z:            float = 50.0

    # ── Klipper shell command name ─────────────────────────────────────────────
    klipper_cmd:       str   = "dispense"

    # ── Serialisation ─────────────────────────────────────────────────────────

    def save(self, path: str):
        data = {
            "gerber_file":   self.gerber_file,
            "netlist_file":  self.netlist_file,
            "priming_pad":   {"x": self.priming_x, "y": self.priming_y},
            "z_offsets": {
                "travel":    self.z_travel,
                "dispense":  self.z_dispense,
            },
            "dispenser": {
                "prime_volume_ul":  self.prime_volume_ul,
                "dot_volume_ul":    self.dot_volume_ul,
                "drag_ul_per_mm":   self.drag_ul_per_mm,
                "retract_mm":       self.retract_mm,
                "deretract_mm":     self.deretract_mm,
                "syringe_id_mm":    self.syringe_id_mm,
                "dispenser_hz":     self.dispenser_hz,
            },
            "dwell_ms": {
                "prime": self.prime_dwell_ms,
                "dot":   self.dot_dwell_ms,
            },
            "speeds": {
                "travel":   self.travel_speed,
                "dispense": self.dispense_speed,
            },
            "blacklist": [
                {"x": b.x, "y": b.y, "label": b.label}
                for b in self.blacklist
            ],
            "park":        {"x": self.park_x, "y": self.park_y, "z": self.park_z},
            "klipper_cmd": self.klipper_cmd,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[info] Config saved → {path}")

    @classmethod
    def load(cls, path: str) -> "DispenserConfig":
        try:
            with open(path) as f:
                d = json.load(f)
        except FileNotFoundError:
            raise SystemExit(f"[error] Config file not found: {path}")
        except json.JSONDecodeError as e:
            raise SystemExit(f"[error] Config file is not valid JSON: {e}")

        cfg = cls()

        cfg.gerber_file  = d.get("gerber_file", "")
        cfg.netlist_file = d.get("netlist_file", "")

        pp = d.get("priming_pad", {})
        cfg.priming_x = float(pp.get("x", 0.0))
        cfg.priming_y = float(pp.get("y", 0.0))

        z = d.get("z_offsets", {})
        cfg.z_travel   = float(z.get("travel",   5.0))
        cfg.z_dispense = float(z.get("dispense", -0.05))
        # Backwards compat: old configs had a "contact" key that's now removed
        # If someone still has it, silently ignore it.

        disp = d.get("dispenser", {})
        cfg.prime_volume_ul = float(disp.get("prime_volume_ul", 3.0))
        cfg.dot_volume_ul   = float(disp.get("dot_volume_ul",   2.0))
        cfg.drag_ul_per_mm  = float(disp.get("drag_ul_per_mm",  1.0))
        cfg.retract_mm      = float(disp.get("retract_mm",      0.15))
        cfg.deretract_mm    = float(disp.get("deretract_mm",    0.15))
        cfg.syringe_id_mm   = float(disp.get("syringe_id_mm",   5.5))
        cfg.dispenser_hz    = float(disp.get("dispenser_hz",    300.0))

        dw = d.get("dwell_ms", {})
        cfg.prime_dwell_ms = int(dw.get("prime", 500))
        cfg.dot_dwell_ms   = int(dw.get("dot",   150))

        sp = d.get("speeds", {})
        cfg.travel_speed   = float(sp.get("travel",   3000.0))
        cfg.dispense_speed = float(sp.get("dispense",  500.0))

        cfg.blacklist = [
            BlacklistEntry(float(b["x"]), float(b["y"]), b.get("label", ""))
            for b in d.get("blacklist", [])
        ]

        pk = d.get("park", {})
        cfg.park_x = float(pk.get("x", 0.0))
        cfg.park_y = float(pk.get("y", 0.0))
        cfg.park_z = float(pk.get("z", 50.0))

        cfg.klipper_cmd = d.get("klipper_cmd", "dispense")
        return cfg

    @classmethod
    def template(cls, gerber_file: str = "",
                 priming_x: float = 0.0,
                 priming_y: float = 0.0) -> "DispenserConfig":
        """Return a default config pre-filled with the detected priming pad."""
        cfg = cls()
        cfg.gerber_file = gerber_file
        cfg.priming_x   = priming_x
        cfg.priming_y   = priming_y
        return cfg

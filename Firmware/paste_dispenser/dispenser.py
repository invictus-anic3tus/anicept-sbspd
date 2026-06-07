"""
dispenser.py
RPi + TMC2209 solder paste dispenser motor controller.

Called by Klipper via gcode_shell_command:
    RUN_SHELL_COMMAND CMD=dispense PARAMS="dispense 5.0 300"

Supported commands:
    prime          <volume_ul> <speed_hz>
    dispense       <volume_ul> <speed_hz>
    dispense_drag  <dist_mm>   <speed_hz>   ← single blocking call for drag moves
    retract        <dist_mm>   <speed_hz>
    deretract      <dist_mm>   <speed_hz>
    move_mm        <dist_mm>   <speed_hz>
"""

from __future__ import annotations
import math
import sys
import time

try:
    import RPi.GPIO as GPIO
    MOCK = False
except ImportError:
    print("[warn] RPi.GPIO not available — running in mock mode")
    MOCK = True


# ── Pin config (edit to match your wiring) ────────────────────────────────────
STEP_PIN = 17
DIR_PIN  = 27
EN_PIN   = 22

# ── Mechanical config (edit to match your hardware) ───────────────────────────
MICROSTEPS       = 16
STEPS_PER_REV    = 200 * MICROSTEPS   # 200-step motor × 16 µsteps = 3200
GEAR_RATIO       = 5.0                # your gearbox ratio
LEAD_MM_PER_REV  = 1.0               # lead screw pitch in mm/rev
STEPS_PER_MM     = STEPS_PER_REV * GEAR_RATIO / LEAD_MM_PER_REV

SYRINGE_ID_MM    = 5.5               # inner diameter of syringe barrel (mm)


# ── GPIO wrapper (transparent mock when not on RPi) ───────────────────────────

class _GPIO:
    BCM  = "BCM"
    OUT  = "OUT"
    HIGH = 1
    LOW  = 0

    @staticmethod
    def setmode(m):
        if not MOCK: GPIO.setmode(GPIO.BCM)

    @staticmethod
    def setup(pin, mode):
        if not MOCK: GPIO.setup(pin, GPIO.OUT)

    @staticmethod
    def output(pin, val):
        if not MOCK: GPIO.output(pin, val)

    @staticmethod
    def cleanup():
        if not MOCK: GPIO.cleanup()


_gpio = _GPIO()


# ── Dispenser ─────────────────────────────────────────────────────────────────

class PasteDispenser:
    def __init__(self, syringe_id_mm: float = SYRINGE_ID_MM):
        self.syringe_id_mm = syringe_id_mm

        _gpio.setmode(_gpio.BCM)
        _gpio.setup(STEP_PIN, _gpio.OUT)
        _gpio.setup(DIR_PIN,  _gpio.OUT)
        _gpio.setup(EN_PIN,   _gpio.OUT)
        _gpio.output(EN_PIN, _gpio.HIGH)   # keep disabled until a move

        self._configure_driver()

    def _configure_driver(self):
        """
        Configure TMC2209 over UART on /dev/serial0.
        Runs on every startup (driver has no non-volatile memory).
        Skipped gracefully if pytrinamic is not installed.
        """
        try:
            from pytrinamic.connections import UartTmclInterface
            import pytrinamic.ic.TMC2209 as TMC2209_IC

            iface = UartTmclInterface("/dev/serial0", datarate=115200)
            drv   = TMC2209_IC(iface, address=0)
            drv.set_motor_driver_current(400)   # mA — match your NEMA 11 rating
            drv.CHOPCONF.mres = 4               # 16 microsteps
            drv.GCONF.en_spreadcycle = 0        # StealthChop for quiet/slow moves
            drv.SGTHRS = 50                     # StallGuard threshold (tune per paste)
            iface.close()
            print("[info] TMC2209 configured via UART")
        except Exception as e:
            print(f"[warn] TMC2209 UART config skipped: {e}")

    # ── Motor primitives ──────────────────────────────────────────────────────

    def _enable(self):
        _gpio.output(EN_PIN, _gpio.LOW)

    def _disable(self):
        _gpio.output(EN_PIN, _gpio.HIGH)

    def _step(self, delay_s: float):
        _gpio.output(STEP_PIN, _gpio.HIGH)
        time.sleep(delay_s / 2)
        _gpio.output(STEP_PIN, _gpio.LOW)
        time.sleep(delay_s / 2)

    def move_mm(self, dist_mm: float, speed_hz: float = 300.0):
        """
        Move plunger by dist_mm.
        Positive = extrude (push paste), negative = retract.
        Blocks until move is complete.
        """
        if dist_mm == 0:
            return

        steps   = int(abs(dist_mm) * STEPS_PER_MM)
        forward = dist_mm > 0
        delay   = 1.0 / max(speed_hz, 1.0)

        _gpio.output(DIR_PIN, _gpio.LOW if forward else _gpio.HIGH)

        if MOCK:
            direction = "extrude" if forward else "retract"
            print(f"  [mock] {direction} {abs(dist_mm):.3f}mm "
                  f"({steps} steps @ {speed_hz:.0f} Hz)")
            return

        self._enable()
        for _ in range(steps):
            self._step(delay)
        self._disable()

    def _volume_to_mm(self, volume_ul: float) -> float:
        """Convert a volume in µL to plunger travel in mm (1 µL = 1 mm³)."""
        area_mm2 = math.pi * (self.syringe_id_mm / 2) ** 2
        return volume_ul / area_mm2

    # ── High-level commands ───────────────────────────────────────────────────

    def prime(self, volume_ul: float = 3.0, speed_hz: float = 200.0):
        print(f"[cmd] prime {volume_ul} µL @ {speed_hz} Hz")
        self.move_mm(self._volume_to_mm(volume_ul), speed_hz)

    def dispense(self, volume_ul: float = 2.0, speed_hz: float = 300.0):
        print(f"[cmd] dispense {volume_ul} µL @ {speed_hz} Hz")
        self.move_mm(self._volume_to_mm(volume_ul), speed_hz)

    def dispense_drag(self, dist_mm: float, speed_hz: float = 300.0):
        """
        Extrude continuously for the duration of a drag move.
        dist_mm is the XY drag length; extrusion volume = dist_mm × drag_ul_per_mm
        (drag_ul_per_mm is baked into dist_mm by the G-code generator).
        Blocks until complete — safe to call from a single shell command.
        """
        print(f"[cmd] dispense_drag {dist_mm:.3f} mm @ {speed_hz} Hz")
        self.move_mm(dist_mm, speed_hz)

    def retract(self, dist_mm: float = 0.15, speed_hz: float = 300.0):
        print(f"[cmd] retract {dist_mm} mm @ {speed_hz} Hz")
        self.move_mm(-abs(dist_mm), speed_hz)

    def deretract(self, dist_mm: float = 0.15, speed_hz: float = 300.0):
        print(f"[cmd] deretract {dist_mm} mm @ {speed_hz} Hz")
        self.move_mm(abs(dist_mm), speed_hz)

    def cleanup(self):
        _gpio.cleanup()


# ── CLI entry point ───────────────────────────────────────────────────────────

_COMMANDS = {
    "prime":         ("volume_ul", "speed_hz"),
    "dispense":      ("volume_ul", "speed_hz"),
    "dispense_drag": ("dist_mm",   "speed_hz"),
    "retract":       ("dist_mm",   "speed_hz"),
    "deretract":     ("dist_mm",   "speed_hz"),
    "move_mm":       ("dist_mm",   "speed_hz"),
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print("Usage: dispenser.py <command> [args...]")
        print("Commands:")
        for cmd, params in _COMMANDS.items():
            print(f"  {cmd:<16} {' '.join(params)}")
        sys.exit(1)

    cmd  = sys.argv[1]
    args = [float(a) for a in sys.argv[2:]]

    d = PasteDispenser()
    try:
        getattr(d, cmd)(*args)
    except TypeError as e:
        print(f"[error] Wrong arguments for {cmd!r}: {e}")
        sys.exit(1)
    finally:
        d.cleanup()


if __name__ == "__main__":
    main()

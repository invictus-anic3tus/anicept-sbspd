# Firmware

This is the firmware for the Anicept SBSPD. Because I'm a hardware guy and not a software guy, this was made using Claude AI, but I manually reviewed it to ensure that it works.

Probably.

## File Structure

```
paste_dispenser/
├── main.py              # CLI — handles parse, blacklist run, info commands
├── pad_model.py         # Defines solder pads, contains main config
├── gerber_parser.py     # Converts top/bottom solder paste gerber file into a list of pads, checks for the priming point and blacklisted pads
├── toolpath.py          # Uses nearest-neighbor to sort pads, checks for pads that are too close
├── gcode_gen.py         # Converts pad x/y coords into gcode, generates dispensing commands
├── dispenser.py         # Initiates the dispenser TMC2209, contains functions to be controlled via Klipper
└── klipper_config.cfg   # Basic additional Klipper config
```

## Installation

First, download the `paste_dispenser` folder and add it to the RPi's root. Then, on the Pi, install pcb-tools, shapely, RPi.GPIO (probably already installed) and pytrinamic. Look through the base dispenser config in pad_model.py, and input any custom values. As I haven't built this yet myself (as of June 2026) these values are currently NOT correct. You will be able to edit this config when exporting the config .json file later, but it's a good idea to set constants here first.

Add the Klipper config to your configuration; I recommend reviewing the file first to make sure it lines up with your setup.

## Workflow

Note: the system accepts both .GTP and .GBP files. The former is for the top layer, the latter for the bottom.

### Gerber Parsing

First, run `python3 main.py parse [YOUR_GERBER_FILE].GTP --netlist [YOUR_NETLIST].net` to check out the paste layer, find pads, and write a config .json file in the folder. The netlist file is for detecting the first test point, which is by default the priming pad. If you don't have one, the biggest pad on the board is used as the fallback priming pad. This is useful if the largest pad is like a capcitor or something, but if it happens to just be a heatsink you probably don't wanna prime on it, since it'd attempt to fill the entire thing up.

Next, on a related note, you can blacklist any pads that you don't want solder on. This could be heatsinks, decorative pads, etc. To do this, run `python3 main.py blacklist [YOUR_GERBER_FILE].GTP --config [GENERATED_CONFIG_FILE].json`. It'll open the CLI, which allows you to select pads based on location (these coords are relative to the priming pad, so you can measure easily off of it. It'll show up as at 0, 0 in this CLI) and toggle their status.

Now you can edit the configuration. Open the json file to adjust the values as needed.

### Printing Files

To generate the gcode file, run the command `python3 main.py run [YOUR_GERBER_FILE].GTP --config [GENERATED_CONFIG_FILE].json`. Rename it to something descriptive of your project, upload it to your printer, and attach the PCB using magnets, a clamp (making sure that it does not interfere with the printhead), etc. Home all three axes of the printer. Now, employ one of the following options to account for the rotational skew of the PCB on the build plate:

1. Align the PCB by a straight edge - using a straight edge on the PCB (or, ideally, a corner between two straight edges), line the PCB up against the build plate's edge and hold it in place. Note that if you change the orientation of the PCB relative to its gerber files, you must rotate the gerber files likewise BEFORE PARSING!
2. 3D print a casing for the PCB - if the design has no readily accessible straight edges, you can 3D print a square casing around it to more easily align it to the build plate.
3. Add a skew guide to the PCB - you can add a horizontal line or two points on the same axis coordinate and jog the printhead along them, simultaneously manually adjusting the PCB rotation to align with them.

Whichever method you use, the next step is to attach the solder paste toolhead, which simply uses magnets to snap into place on the pre-installed mount. After doing so, use gcode commands or your Klipper interface to move the solder dispenser's tip on top of the priming test point. Center it as accurately as possible, because it'll set the course for the entire operation. To gauge the z distance, use the paper method to get a consistent drag resistance every time. Always make sure that you run the gcode file with the z axis in position: nearly touching the test pad, just a paper's width offset.

Finally, you can run the gcode!

### Operation Details

First, the printhead will lower to the priming height above the priming pad and dispenses some solder to prime the tip. It then retracts, raises, and deposits paste on every pad in order of proximity. Finally, it uses an absolute move command to park and conclude the process.

## Config Reference & Tuning Tips

Sample configuration file:

```
{
  "gerber_file":  "/path/to/gerber.GTP",
  "netlist_file": "/path/to/netlist.net",

  "priming_pad": { "x": 12.4, "y": 8.7 },

  "z_offsets": {
    "travel":   5.0,
    "dispense": -0.05
  },

  "dispenser": {
    "prime_volume_ul": 3.0,
    "dot_volume_ul":   2.0,
    "drag_ul_per_mm":  1.0,
    "retract_mm":      0.15,
    "deretract_mm":    0.15,
    "syringe_id_mm":   5.5,
    "dispenser_hz":    300.0
  },

  "dwell_ms": {
    "prime": 500,
    "dot":   150
  },

  "speeds": {
    "travel":   3000.0,
    "dispense": 500.0
  },

  "blacklist": [
    { "x": 45.2, "y": 23.1, "label": "heatsink" }
  ],

  "park":        { "x": 0.0, "y": 0.0, "z": 50.0 },
  "klipper_cmd": "dispense"
}
```

| Field | Description |
|---|---|
| `priming_pad` | The X/Y coordinates of your priming test point |
| `z_offsets.travel` | How far in mm the dispenser should move up while travelling |
| `z_offsets.dispense` | The Z coordinate at dispensing. This controls the "squish"; you should tune it first |
| `dispenser.dot_volume_ul` | Volume dispensed for a dot. Tune this to accurately fill the pad |
| `dispenser.drag_ul_per_mm` | Volume per mm dispensed for a drag. Tune this to accurately fill the pad |
| `dispenser.retract_mm` | Distance retracted. Increase if paste oozes |
| `dispenser.deretract_mm` | Distance deretracted. Increase if the beginning of the pad is light |
| `dispenser.syringe_id_mm` | Inner diameter of the syringe barrel |
| `dispenser.dispenser_hz` | The motor's step rate: lower for torque and pressure spike handling, increase for speed |
| `dwell_ms.prime` | Millisecond wait time after priming. Increase if paste needs more flowing time |
| `dwell_ms.dot` | Millisecond wait time after each dot & before retract. Increase if paste needs more flowing time |
| `speeds.travel` | Printer travel speed in mm/min |
| `speeds.dispense` | Printer drag speed in mm/min. Decrease for more paste per mm, and vice versa  |
| `blacklist` | X/Y coordinates and label for ignored solder pads |
| `park` | Absolute position the toolhead moves to after the run completes |
| `klipper_cmd` | Name of the gcode_shell_command |

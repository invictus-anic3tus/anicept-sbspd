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

First, download the `paste_dispenser` folder and add it to the RPi's root. Then, on the Pi, install pcb-tools, shapely, RPi.GPIO (probably already installed) and pytrinamic. Look through the main dispenser config in pad_model.py, and input any custom values. As I haven't built this yet myself (as of June 2026) these values are currently NOT correct.

Add the Klipper config to your configuration; I recommend reviewing the file first to make sure it lines up with your setup.

## Workflow

### Gerber Parsing

First, run `python3 main.py parse [YOUR_GERBER_TOP_FILE].GTP --netlist board.net` to check out the paste layer, find pads, and write a config .json file in the folder. The netlist file is for detecting the first test point, which is by default the priming pad. If you don't have one, the biggest pad on the board is used as the fallback priming pad. This is useful if the largest pad is like a capcitor or something, but if it happens to just be a heatsink you probably don't wanna prime on it, since it'd attempt to fill the entire thing up.

Next, on a related note, you can blacklist any pads that you don't want solder on. This could be heatsinks, decorative pads, etc. To do this, run `python3 main.py blacklist [YOUR_GERBER_TOP_FILE].GTP --config [GENERATED_CONFIG_FILE].json`. It'll open the CLI, which allows you to select pads based on location (these coords are relative to the priming pad, so you can measure easily off of it. It'll show up as at 0, 0 in this CLI) and toggle their status.

Now you can edit the configuration. Open the json file to adjust the values as needed.

To generate the gcode file,

MORE LATER MWAHAHAHA

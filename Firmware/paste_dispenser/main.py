"""
main.py
Orchestrates the full paste-dispenser pipeline:

    python3 main.py parse     <gerber> [--netlist <file>] [--config <out.json>]
    python3 main.py blacklist <gerber> --config <cfg.json>
    python3 main.py run       <gerber> --config <cfg.json> [--output <out.gcode>]
    python3 main.py info      <gerber>
"""

from __future__ import annotations
import argparse
import os
import sys

from pad_model       import DispenserConfig, BlacklistEntry
from gerber_parser   import parse_paste_layer, apply_origin_shift
from toolpath        import nearest_neighbour, estimate_travel_distance, check_dense_pads
from gcode_gen       import generate_gcode


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_parse(args):
    """
    Parse a Gerber file, detect priming pad, generate a config template.
    The user edits the generated .json before running `run`.
    """
    pads, priming = parse_paste_layer(
        args.gerber,
        netlist_path=args.netlist or "",
    )

    if not pads:
        print("[error] No pads found in Gerber file.")
        sys.exit(1)

    px, py = priming if priming else (pads[0].center_x, pads[0].center_y)

    cfg = DispenserConfig.template(
        gerber_file=os.path.abspath(args.gerber),
        priming_x=px,
        priming_y=py,
    )
    if args.netlist:
        cfg.netlist_file = os.path.abspath(args.netlist)

    config_path = args.config or (os.path.splitext(args.gerber)[0] + "_config.json")
    cfg.save(config_path)

    print()
    print("─" * 55)
    print(f"  Config template saved → {config_path}")
    print(f"  Priming pad detected  → ({px:.3f}, {py:.3f})")
    print()
    print("  Next steps:")
    print("    1. Review / adjust priming pad in config if needed")
    print("    2. Run blacklist command to mark pads to skip:")
    print(f"         python3 main.py blacklist {args.gerber} --config {config_path}")
    print("    3. Tune Z offsets, volumes, and speeds in config")
    print(f"    4. python3 main.py run {args.gerber} --config {config_path}")
    print("─" * 55)


def cmd_blacklist(args):
    """
    Interactively review all pads and toggle blacklist entries,
    then save the updated config. No manual coordinate entry needed.
    """
    cfg = DispenserConfig.load(args.config)
    gerber_path = args.gerber or cfg.gerber_file

    pads, _ = parse_paste_layer(gerber_path, blacklist=cfg.blacklist)
    if not pads:
        print("[error] No pads found.")
        sys.exit(1)

    # Shift to origin so coordinates match what G-code will use
    apply_origin_shift(pads, cfg.priming_x, cfg.priming_y)

    # Sort by area descending — large pads (heatsinks etc.) float to top
    sorted_pads = sorted(pads, key=lambda p: p.area, reverse=True)

    print()
    print("  Pads sorted by area — largest first (most likely blacklist candidates).")
    print("  Type pad number(s) to toggle blacklist on/off.")
    print("  Blacklisted pads are marked [X] and will be skipped in G-code.")
    print()

    while True:
        # Print table
        print(f"  {'#':>4}  {'':3}  {'Type':4}  {'Center X':>9}  {'Center Y':>9}"
              f"  {'W':>6}  {'H':>6}  {'Area mm²':>8}  Label")
        print("  " + "─" * 72)
        for i, p in enumerate(sorted_pads):
            status = "[X]" if p.blacklisted else "   "
            ptype  = "DRAG" if p.pad_type.name == "DRAG" else "DOT "
            label  = p.label or ""
            print(f"  {i+1:>4}  {status}  {ptype}  "
                  f"{p.center_x:>9.3f}  {p.center_y:>9.3f}  "
                  f"{p.width:>6.3f}  {p.height:>6.3f}  "
                  f"{p.area:>8.4f}  {label}")

        print()
        raw = input("  Toggle pad(s) [e.g. 1  or  1,3,5], or Enter to save: ").strip()
        if not raw:
            break

        for token in raw.split(","):
            token = token.strip()
            if not token.isdigit():
                print(f"  [skip] not a number: {token!r}")
                continue
            idx = int(token) - 1
            if not (0 <= idx < len(sorted_pads)):
                print(f"  [skip] out of range: {token}")
                continue
            p = sorted_pads[idx]
            p.blacklisted = not p.blacklisted
            if p.blacklisted:
                label = input(f"  Label for pad {idx+1} (e.g. 'heatsink', or Enter to skip): ").strip()
                p.label = label or ""
                print(f"  → Blacklisted pad {idx+1}  {p}")
            else:
                p.label = ""
                print(f"  → Removed from blacklist: pad {idx+1}  {p}")
        print()

    # Rebuild blacklist — un-shift coordinates back to original Gerber space
    cfg.blacklist = [
        BlacklistEntry(
            x     = p.center_x + cfg.priming_x,
            y     = p.center_y + cfg.priming_y,
            label = p.label,
        )
        for p in pads if p.blacklisted
    ]

    cfg.save(args.config)
    active = sum(1 for p in pads if not p.blacklisted)
    bl     = len(cfg.blacklist)
    print(f"\n  Saved — {bl} blacklisted, {active} active pad(s).\n")


def cmd_run(args):
    """
    Load config, parse Gerber with blacklist applied, sort toolpath,
    generate G-code file.
    """
    if not args.config:
        print("[error] --config <cfg.json> is required for the run command.")
        sys.exit(1)

    cfg = DispenserConfig.load(args.config)

    gerber_path = args.gerber or cfg.gerber_file
    if not gerber_path or not os.path.exists(gerber_path):
        print(f"[error] Gerber file not found: {gerber_path}")
        sys.exit(1)

    pads, _ = parse_paste_layer(
        gerber_path,
        netlist_path=cfg.netlist_file,
        blacklist=cfg.blacklist,
    )

    if not pads:
        print("[error] No pads parsed.")
        sys.exit(1)

    print(f"[info] Origin → priming pad ({cfg.priming_x:.3f}, {cfg.priming_y:.3f})")
    apply_origin_shift(pads, cfg.priming_x, cfg.priming_y)

    ordered = nearest_neighbour(pads, start_x=0.0, start_y=0.0)
    dist    = estimate_travel_distance(ordered)
    print(f"[info] Toolpath: {len(ordered)} pads, ~{dist:.1f}mm total travel")

    dense = check_dense_pads(pads)
    if dense:
        print(f"[warn] {len(dense)} closely-spaced pad pair(s) — review for bridging risk:")
        for a, b in dense[:5]:
            print(f"         ({a.center_x:.2f},{a.center_y:.2f}) ↔ "
                  f"({b.center_x:.2f},{b.center_y:.2f})")

    out_path = args.output or (os.path.splitext(gerber_path)[0] + "_paste.gcode")
    generate_gcode(ordered, cfg, out_path)

    print()
    print("─" * 55)
    print(f"  G-code ready → {out_path}")
    print()
    print("  To run on printer:")
    print("    1. Home printer (G28)")
    print("    2. Attach solder paste extruder")
    print("    3. Jog tip to priming test point")
    print("    4. Upload and start the G-code file")
    print("─" * 55)


def cmd_info(args):
    """Print a summary of pads found in the Gerber file."""
    from pad_model import PadType

    pads, priming = parse_paste_layer(args.gerber)
    if not pads:
        print("No pads found.")
        return

    dots  = sum(1 for p in pads if p.pad_type == PadType.DOT)
    drags = sum(1 for p in pads if p.pad_type == PadType.DRAG)

    areas = sorted(p.area for p in pads)
    avg   = sum(areas) / len(areas)

    print()
    print(f"  File    : {args.gerber}")
    print(f"  Pads    : {len(pads)}  ({dots} dot, {drags} drag)")
    print(f"  Area    : min={areas[0]:.3f}  avg={avg:.3f}  max={areas[-1]:.3f} mm²")
    if priming:
        print(f"  Priming : ({priming[0]:.3f}, {priming[1]:.3f})")
    print()
    print("  First 10 pads:")
    for p in pads[:10]:
        print(f"    {p}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Solder paste dispenser — Gerber to G-code pipeline"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # parse
    p_parse = sub.add_parser("parse", help="Parse Gerber + generate config template")
    p_parse.add_argument("gerber",                help="Gerber paste layer (.GTP / .GBP)")
    p_parse.add_argument("--netlist", default="", help="KiCad netlist for test-point detection")
    p_parse.add_argument("--config",  default="", help="Output config JSON path")

    # blacklist
    p_bl = sub.add_parser("blacklist", help="Interactively mark pads to skip")
    p_bl.add_argument("gerber", nargs="?", default="", help="Gerber paste layer (or use path in config)")
    p_bl.add_argument("--config", required=True, help="Config JSON to update")

    # run
    p_run = sub.add_parser("run", help="Generate G-code from Gerber + config")
    p_run.add_argument("gerber", nargs="?", default="", help="Gerber paste layer (or use path in config)")
    p_run.add_argument("--config",  required=True, help="Config JSON (from `parse` step)")
    p_run.add_argument("--output",  default="",    help="Output .gcode path")

    # info
    p_info = sub.add_parser("info", help="Summarise pads in a Gerber file")
    p_info.add_argument("gerber", help="Gerber paste layer")

    args = parser.parse_args()

    if   args.command == "parse":     cmd_parse(args)
    elif args.command == "blacklist": cmd_blacklist(args)
    elif args.command == "run":       cmd_run(args)
    elif args.command == "info":      cmd_info(args)


if __name__ == "__main__":
    main()

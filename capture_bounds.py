#!/usr/bin/env python
"""
Interactive helper for authoring find_fishing_spot events.

Steps:
  1. Hover over top-left corner of NEAR region,  press ENTER
  2. Hover over bottom-right corner of NEAR region, press ENTER
  3. Hover over top-left corner of REGION,          press ENTER
  4. Hover over bottom-right corner of REGION,      press ENTER
  5. Hover over your character sprite center,        press ENTER
  6. Hover over a tile marker pixel,                 press ENTER

Outputs a ready-to-paste find_fishing_spot event line.

Usage:
    python capture_bounds.py [--tol 20] [--timeout 15] [--button left]
"""

import argparse
import sys
import time
import select

try:
    import mss
except ImportError:
    print("pip install mss"); sys.exit(1)

try:
    from pynput import mouse as _mouse
except ImportError:
    print("pip install pynput"); sys.exit(1)


def cursor_pos():
    return _mouse.Controller().position


def get_pixel(x, y):
    with mss.MSS() as sct:
        shot = sct.grab({"left": int(x), "top": int(y), "width": 1, "height": 1})
        b, g, r = shot.raw[0], shot.raw[1], shot.raw[2]
        return r, g, b


def wait_enter(prompt):
    """Show live cursor position, return position when ENTER pressed."""
    print(f"\n{prompt}")
    print("(Move cursor to position, then press ENTER)")
    while True:
        x, y = cursor_pos()
        r, g, b = get_pixel(x, y)
        sys.stdout.write(f"\r  pos=({int(x):4d},{int(y):4d})  rgb=({r:3d},{g:3d},{b:3d})  ")
        sys.stdout.flush()
        if select.select([sys.stdin], [], [], 0.05)[0]:
            sys.stdin.readline()
            print()
            return int(x), int(y), r, g, b


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tol',     type=int,   default=20,   help='Color tolerance (default 20)')
    parser.add_argument('--timeout', type=float, default=15.0, help='Search timeout in seconds (default 15)')
    parser.add_argument('--button',  type=str,   default='left', choices=['left', 'right'])
    args = parser.parse_args()

    print("=== find_fishing_spot event builder ===")

    reg_tl = wait_enter("Step 1/4 — SEARCH REGION TOP-LEFT (all possible spot tiles)")
    reg_br = wait_enter("Step 2/4 — SEARCH REGION BOTTOM-RIGHT")
    char   = wait_enter("Step 3/4 — CHARACTER center position")
    tile   = wait_enter("Step 4/4 — Sample a TILE MARKER pixel")

    region = (reg_tl[0], reg_tl[1], reg_br[0], reg_br[1])
    cx, cy = char[0], char[1]
    r, g, b = tile[2], tile[3], tile[4]

    line = (
        f"find_fishing_spot|"
        f"region=({region[0]},{region[1]},{region[2]},{region[3]});"
        f"color=({r},{g},{b});"
        f"tol={args.tol};timeout={args.timeout};"
        f"char=({cx},{cy});"
        f"button={args.button}"
        f"|0.0"
    )

    print("\n=== Paste this into your event file ===")
    print(line)
    print()


if __name__ == '__main__':
    main()

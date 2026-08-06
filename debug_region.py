#!/usr/bin/env python
"""
Debug tool: renders a colour grid of a screen region in the terminal.

Usage:
    python debug_region.py                               # click two corners
    python debug_region.py --region 259,481,1262,771     # fixed region
    python debug_region.py --region ... --color 0,255,0  # highlight matches
    python debug_region.py --region ... --stride 6       # finer grid
    python debug_region.py --region ... --top 20         # more top colours
    python debug_region.py --region ... --save           # also save PNG
"""

import argparse
import select
import subprocess
import sys
from collections import Counter

try:
    import mss
    import mss.tools
except ImportError:
    print("pip install mss"); sys.exit(1)

try:
    from pynput import mouse as _mouse
except ImportError:
    print("pip install pynput"); sys.exit(1)


# ── helpers ──────────────────────────────────────────────────────────────────

RESET = "\033[0m"

def _bg(r, g, b):
    return f"\033[48;2;{r};{g};{b}m"

def _cursor():
    return _mouse.Controller().position

def _pixel(sct, x, y):
    shot = sct.grab({"left": int(x), "top": int(y), "width": 1, "height": 1})
    b, g, r = shot.raw[0], shot.raw[1], shot.raw[2]
    return r, g, b

def _wait_enter(sct, prompt):
    print(f"\n{prompt}\n(Move cursor, press ENTER)")
    while True:
        x, y = _cursor()
        r, g, b = _pixel(sct, x, y)
        sys.stdout.write(f"\r  pos=({int(x):4d},{int(y):4d})  rgb=({r:3d},{g:3d},{b:3d})  ")
        sys.stdout.flush()
        if select.select([sys.stdin], [], [], 0.05)[0]:
            sys.stdin.readline()
            print()
            return int(x), int(y)


# ── drawing ───────────────────────────────────────────────────────────────────

def draw_grid(shot, match_color, tol, stride):
    """
    Renders the region as an ANSI colour grid.
    Each cell = 2 spaces with background set to the sampled pixel colour.
    Matching pixels are marked with '██' in bright white.
    """
    raw, w, h = shot.raw, shot.width, shot.height
    r_t, g_t, b_t = match_color or (None, None, None)

    print()
    for py in range(0, h, stride):
        row_off = py * w
        for px in range(0, w, stride):
            idx = (row_off + px) * 4
            b, g, r = raw[idx], raw[idx+1], raw[idx+2]
            if match_color and (
                abs(r - r_t) <= tol and
                abs(g - g_t) <= tol and
                abs(b - b_t) <= tol
            ):
                sys.stdout.write(f"{_bg(r,g,b)}\033[97m██{RESET}")
            else:
                sys.stdout.write(f"{_bg(r,g,b)}  {RESET}")
        sys.stdout.write("\n")
    print()


def top_colors(shot, n, stride=4):
    raw, w, h = shot.raw, shot.width, shot.height
    counter = Counter()
    for py in range(0, h, stride):
        row_off = py * w
        for px in range(0, w, stride):
            idx = (row_off + px) * 4
            b, g, r = raw[idx], raw[idx+1], raw[idx+2]
            counter[(r, g, b)] += 1

    print(f"Top {n} most common colours (stride={stride}):")
    for (r, g, b), count in counter.most_common(n):
        bar = f"{_bg(r,g,b)}    {RESET}"
        print(f"  {bar}  ({r:3d},{g:3d},{b:3d})  ×{count}")
    print()


def count_matches(shot, r_t, g_t, b_t, tol, stride=4):
    raw, w, h = shot.raw, shot.width, shot.height
    n = 0
    for py in range(0, h, stride):
        row_off = py * w
        for px in range(0, w, stride):
            idx = (row_off + px) * 4
            if (abs(raw[idx+2] - r_t) <= tol and
                    abs(raw[idx+1] - g_t) <= tol and
                    abs(raw[idx]   - b_t) <= tol):
                n += 1
    return n


def save_png(shot, path="debug_region.png"):
    try:
        mss.tools.to_png(shot.raw, shot.size, output=path)
        print(f"Saved: {path}")
        subprocess.Popen(["open", path])
    except Exception:
        try:
            from PIL import Image
            img = Image.frombytes("RGBA", (shot.width, shot.height),
                                  bytes(shot.raw), "raw", "BGRA")
            img.save(path)
            print(f"Saved: {path}")
            subprocess.Popen(["open", path])
        except Exception as e:
            print(f"Could not save PNG: {e}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Debug screen region colour grid")
    parser.add_argument('--region', type=str,
                        help='x1,y1,x2,y2 — skip interactive corner selection')
    parser.add_argument('--color',  type=str,
                        help='r,g,b — highlight matching pixels with ██')
    parser.add_argument('--tol',    type=int, default=20,
                        help='Colour tolerance per channel (default 20)')
    parser.add_argument('--stride', type=int, default=8,
                        help='Sampling stride in px (default 8); smaller = finer grid')
    parser.add_argument('--top',    type=int, default=15,
                        help='Show top N most common colours (default 15)')
    parser.add_argument('--save',   action='store_true',
                        help='Save full-res PNG and open it')
    args = parser.parse_args()

    match_color = tuple(map(int, args.color.split(','))) if args.color else None

    with mss.MSS() as sct:
        if args.region:
            x1, y1, x2, y2 = map(int, args.region.split(','))
        else:
            x1, y1 = _wait_enter(sct, "Step 1/2 — TOP-LEFT corner of region")
            x2, y2 = _wait_enter(sct, "Step 2/2 — BOTTOM-RIGHT corner of region")

        monitor = {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}
        print(f"\nRegion ({x1},{y1})→({x2},{y2})  {x2-x1}×{y2-y1}px  stride={args.stride}")
        if match_color:
            r_c, g_c, b_c = match_color
            print(f"Highlighting {_bg(r_c,g_c,b_c)}    {RESET} ({r_c},{g_c},{b_c}) ±{args.tol} as ██")

        shot = sct.grab(monitor)

    if args.save:
        save_png(shot)

    draw_grid(shot, match_color=match_color, tol=args.tol, stride=args.stride)
    top_colors(shot, n=args.top)

    if match_color:
        n = count_matches(shot, *match_color, tol=args.tol, stride=4)
        print(f"Matching pixels (stride=4, tol={args.tol}): {n}")


if __name__ == '__main__':
    main()

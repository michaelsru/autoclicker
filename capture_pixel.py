#!/usr/bin/env python
"""
Live pixel color inspector. Move your cursor to the target pixel,
press ENTER to capture it and print the wait_pixel event line.
Press Ctrl-C to quit.

Usage:
    python capture_pixel.py [--timeout 10] [--tolerance 15]
"""

import argparse
import os
import select
import time
import sys

try:
    import mss
except ImportError:
    print("Requires mss: pip install mss")
    sys.exit(1)

try:
    from pynput import mouse
except ImportError:
    print("Requires pynput: pip install pynput")
    sys.exit(1)


def get_cursor_pos():
    return mouse.Controller().position


def get_pixel(sct, x, y):
    shot = sct.grab({"top": int(y), "left": int(x), "width": 1, "height": 1})
    b, g, r = shot.raw[0], shot.raw[1], shot.raw[2]  # BGRA
    return r, g, b


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=float, default=10.0,
                        help='timeout value to embed in event (default: 10)')
    parser.add_argument('--tolerance', type=int, default=15,
                        help='per-channel tolerance to embed in event (default: 15)')
    args = parser.parse_args()

    print("Move cursor to target pixel. Press ENTER to capture. Ctrl-C to quit.")
    print(f"(tolerance={args.tolerance}, timeout={args.timeout}s)\n")

    try:
        # open mss once for the session, not once per frame
        with mss.MSS() as sct:
            while True:
                x, y = get_cursor_pos()
                r, g, b = get_pixel(sct, x, y)
                sys.stdout.write(f"\r  pos=({int(x)}, {int(y)})  rgb=({r}, {g}, {b})   ")
                sys.stdout.flush()

                # os.read drains available bytes without blocking on \n
                if select.select([sys.stdin], [], [], 0)[0]:
                    os.read(sys.stdin.fileno(), 4096)
                    print()
                    line = (
                        f"wait_pixel|({int(x)}, {int(y)}, {r}, {g}, {b}, "
                        f"{args.tolerance}, {args.timeout})|0.0"
                    )
                    print(f"\nCaptured:\n  {line}\n")

                time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nDone.")


if __name__ == '__main__':
    main()

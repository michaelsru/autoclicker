#!/usr/bin/env python
"""Test mss pixel grab vs PIL full grab at cursor position."""

import time
from pynput import mouse

try:
    import mss
except ImportError:
    print("pip install mss")
    exit(1)

try:
    from PIL import ImageGrab
except ImportError:
    print("pip install Pillow")
    exit(1)


def grab_mss(x, y):
    with mss.MSS() as sct:
        shot = sct.grab({"top": y, "left": x, "width": 1, "height": 1})
        b, g, r = shot.raw[0], shot.raw[1], shot.raw[2]  # mss is BGRA
        return r, g, b


def grab_pil(x, y):
    return ImageGrab.grab().getpixel((x, y))[:3]


def main():
    print("Move cursor to a pixel. Ctrl-C to stop.\n")
    ctrl = mouse.Controller()
    try:
        while True:
            x, y = int(ctrl.position[0]), int(ctrl.position[1])

            t0 = time.perf_counter()
            pil = grab_pil(x, y)
            pil_ms = (time.perf_counter() - t0) * 1000

            t0 = time.perf_counter()
            m = grab_mss(x, y)
            mss_ms = (time.perf_counter() - t0) * 1000

            match = pil == m
            print(
                f"pos=({x:4d},{y:4d})  "
                f"PIL={pil}  {pil_ms:5.1f}ms  "
                f"mss={m}  {mss_ms:5.1f}ms  "
                f"{'✓' if match else '✗ MISMATCH'}"
            )
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nDone.")


if __name__ == "__main__":
    main()

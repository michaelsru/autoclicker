#!/usr/bin/env python
"""
Compress mouse_event .txt files by removing redundant move events.

Usage:
    python compress_events.py input.txt [--out output.txt] [--min-dist 2] [--epsilon 1] [--dry-run]
"""

import argparse
import math
import sys
from autoplayback import Reader, Writer, MoveEvent, LoopEvent


def _point_line_dist(p, a, c):
    """Perpendicular distance from MoveEvent p to line a->c."""
    dx, dy = c.x - a.x, c.y - a.y
    if dx == 0 and dy == 0:
        return math.hypot(p.x - a.x, p.y - a.y)
    t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy)
    return math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))


def _count_events(events):
    total = 0
    for event in events:
        if isinstance(event, LoopEvent):
            total += _count_events(event.events)
        else:
            total += 1
    return total


def _distance_filter(events, min_dist):
    """Drop move events closer than min_dist px from the last kept move.
    Accumulated delay is folded into the next kept event."""
    result = []
    last_move = None
    pending_delay = 0.0

    for event in events:
        if isinstance(event, LoopEvent):
            if last_move is not None and pending_delay > 0:
                last_move.delay += pending_delay
                pending_delay = 0.0
            last_move = None
            event.events = _distance_filter(event.events, min_dist)
            result.append(event)
        elif isinstance(event, MoveEvent):
            if last_move is None:
                result.append(event)
                last_move = event
            else:
                dx = event.x - last_move.x
                dy = event.y - last_move.y
                if dx * dx + dy * dy >= min_dist ** 2:
                    event.delay += pending_delay
                    pending_delay = 0.0
                    result.append(event)
                    last_move = event
                else:
                    pending_delay += event.delay
        else:
            if last_move is not None and pending_delay > 0:
                last_move.delay += pending_delay
                pending_delay = 0.0
            last_move = None
            result.append(event)

    if last_move is not None and pending_delay > 0:
        last_move.delay += pending_delay

    return result


def _collinear_collapse(events, epsilon):
    """For three consecutive MoveEvents A->B->C, drop B if its perpendicular
    deviation from line A->C is within epsilon px. B's delay folds into C."""
    result = []

    for event in events:
        if isinstance(event, LoopEvent):
            event.events = _collinear_collapse(event.events, epsilon)
            result.append(event)
            continue

        if not isinstance(event, MoveEvent):
            result.append(event)
            continue

        if (len(result) >= 2
                and isinstance(result[-1], MoveEvent)
                and isinstance(result[-2], MoveEvent)):
            a, b = result[-2], result[-1]
            if _point_line_dist(b, a, event) < epsilon:
                event.delay += b.delay
                result.pop()

        result.append(event)

    return result


def compress(events, min_dist=2.0, epsilon=1.0):
    events = _distance_filter(events, min_dist)
    events = _collinear_collapse(events, epsilon)
    return events


def main():
    parser = argparse.ArgumentParser(description="Compress mouse event files")
    parser.add_argument("input", help="Input .txt file")
    parser.add_argument("--out", help="Output file (default: overwrite input)")
    parser.add_argument("--min-dist", type=float, default=2.0,
                        help="Min pixel distance between kept move events (default: 2)")
    parser.add_argument("--epsilon", type=float, default=1.0,
                        help="Collinear collapse tolerance in px (default: 1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't write output, just print stats")
    args = parser.parse_args()

    reader = Reader()
    writer = Writer()

    events = reader.load(args.input)
    if not events:
        print(f"No events loaded from {args.input}")
        sys.exit(1)

    before = _count_events(events)
    events = compress(events, min_dist=args.min_dist, epsilon=args.epsilon)
    after = _count_events(events)

    pct = (1 - after / before) * 100 if before > 0 else 0
    print(f"Events: {before} -> {after} ({pct:.1f}% reduction)")

    if not args.dry_run:
        out = args.out or args.input
        writer.save(out, events)
        print(f"Saved to {out}")


if __name__ == "__main__":
    main()

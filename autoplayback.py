#!/usr/bin/env python

import time
import sys
import threading
import random
import argparse
import re
import math
import logging
from dataclasses import dataclass
from datetime import datetime
from pynput import keyboard, mouse

# --- Event types ---

@dataclass(slots=True)
class MoveEvent:
    x: float
    y: float
    delay: float

@dataclass(slots=True)
class SmoothMoveEvent:
    """Non-linear mouse glide to target coordinate with optional click and pre-click settle delay."""
    x: float
    y: float
    button: str | None
    move_time: float
    bezier_ratio: float
    delay: float

@dataclass(slots=True)
class ClickEvent:
    x: float
    y: float
    button: object
    pressed: bool
    delay: float

@dataclass(slots=True)
class ScrollEvent:
    x: float
    y: float
    dx: float
    dy: float
    delay: float

@dataclass(slots=True)
class SpacebarEvent:
    pressed: bool
    delay: float

@dataclass(slots=True)
class LoopEvent:
    count: int
    events: list

@dataclass(slots=True)
class SetTimerEvent:
    """Sets or resets a named timer to current timestamp (creates if missing, resets if existing)."""
    name: str
    delay: float

@dataclass(slots=True)
class CheckTimerEvent:
    """Runs inner events if elapsed time since timer was set >= duration (or if timer was never set)."""
    name: str
    duration: float
    events: list
    delay: float

@dataclass(slots=True)
class CheckPixelEvent:
    """Runs inner events only if pixel at (x,y) matches (r,g,b) within tolerance."""
    x: int
    y: int
    r: int
    g: int
    b: int
    tolerance: int
    events: list
    delay: float

@dataclass(slots=True)
class WaitPixelEvent:
    x: int
    y: int
    r: int
    g: int
    b: int
    tolerance: int
    timeout: float
    delay: float

@dataclass(slots=True)
class LogEvent:
    message: str
    delay: float

@dataclass(slots=True)
class WaitPixelsEvent:
    """Multi-pixel wait with AND/OR logic.
    pixels: tuple of (x, y, r, g, b, tolerance) tuples
    mode: 'and' | 'or'
    """
    pixels: tuple
    mode: str
    timeout: float
    delay: float

@dataclass(slots=True)
class RandomWaitEvent:
    min_t: float
    max_t: float
    delay: float

@dataclass(slots=True)
class FindFishingSpotEvent:
    """Scans `region` for tile-colored pixels, clusters into spots,
    clicks the one closest to `char`."""
    region: tuple    # (x1,y1,x2,y2) — full search area
    color: tuple     # (r,g,b)        — tile marker color
    tol: int         # per-channel tolerance
    timeout: float   # seconds to wait for a spot to appear
    char: tuple | None  # (cx,cy) for closest-spot; None = pick randomly
    button: str | None  # 'left' | 'right' | None = move only, no click
    move_time: float # seconds to glide cursor to target (0 = instant)
    bezier_ratio: float  # 0.0–1.0: chance of Bézier arc vs overshoot path
    delay: float

# --- Exceptions ---

class _PixelTimeout(Exception):
    """Raised when a wait_pixel event times out."""

# --- Classes ---

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s %(levelname)s:%(name)s:%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

def _ts():
    """Current time as HH:MM:SS for inline stdout messages."""
    return datetime.now().strftime('%H:%M:%S')

class Reader:
    def load(self, filename, loaded_files=None):
        """Load mouse events from a text file, supporting nested blocks"""
        if loaded_files is None:
            loaded_files = set()
        
        if filename in loaded_files:
            logger.warning(f"Circular reference detected for file: {filename}")
            return []
        
        # Pass a copy of the stack set so sibling sub-files can be loaded sequentially
        new_loaded = loaded_files | {filename}
        
        try:
            with open(filename, 'r') as f:
                lines = f.readlines()
            events, _ = self._parse_lines(lines, indent_level=0, loaded_files=new_loaded)
            return events
        except FileNotFoundError:
            logger.warning(f"File not found: {filename}")
            return []
        except Exception as e:
            logger.warning(f"Error loading file {filename}: {e}")
            return []

    def _parse_lines(self, lines, current_line_idx=0, indent_level=0, loaded_files=None):
        events = []
        i = current_line_idx
        while i < len(lines):
            line = lines[i].strip()
            i += 1
            
            if not line or line.startswith('#'):
                continue
            
            if line == '}':
                return events, i

            if line.startswith('run '):
                target_file = line[4:].strip()
                logger.info(f"{'  '*indent_level}Loading events from file: {target_file}")
                # Recursively load using the same loaded_files set
                imported_events = self.load(target_file, loaded_files) 
                events.extend(imported_events)
                
            elif line.startswith('loop '):
                parts = line[5:].strip().split(' ', 1)
                if len(parts) >= 1:
                    loop_count = int(parts[0])
                    rest = parts[1].strip() if len(parts) > 1 else ""

                    if rest == '{':
                        logger.info(f"{'  '*indent_level}Parsing loop block: {loop_count} times")
                        block_events, new_i = self._parse_lines(lines, i, indent_level + 1, loaded_files)
                        i = new_i
                        events.append(LoopEvent(count=loop_count, events=block_events))
                    elif rest:
                        # Legacy: loop <count> <filename>
                        target_file = rest
                        logger.info(f"{'  '*indent_level}Looping explicitly from file: {loop_count} times: {target_file}")
                        file_events = self.load(target_file, loaded_files)
                        events.append(LoopEvent(count=loop_count, events=file_events))
                    else:
                        logger.warning(f"Syntax error on loop command: {line}")

            elif line.startswith('check_timer'):
                # Supported formats:
                #   check_timer|(name, duration)|delay {
                #   check_timer|name, duration|delay
                #   {
                header_line = line.rstrip()
                has_brace = header_line.endswith('{')
                if has_brace:
                    header_line = header_line[:-1].rstrip()

                try:
                    parts = header_line.split('|')
                    data_str = parts[1].strip(' ()')
                    subparts = [p.strip() for p in data_str.split(',')]
                    name = subparts[0]
                    duration = float(subparts[1])
                    delay = float(parts[2].strip()) if len(parts) > 2 else 0.0

                    if not has_brace:
                        j = i
                        while j < len(lines) and not lines[j].strip():
                            j += 1
                        if j < len(lines) and lines[j].strip() == '{':
                            i = j + 1
                        else:
                            logger.warning(f"check_timer missing '{{': {line}")
                            continue

                    block_events, new_i = self._parse_lines(lines, i, indent_level + 1, loaded_files)
                    i = new_i
                    events.append(CheckTimerEvent(
                        name=name, duration=duration,
                        events=block_events, delay=delay,
                    ))
                except Exception as e:
                    logger.warning(f"Syntax error on check_timer: {line} — {e}")

            elif line.startswith('check_pixel'):
                # Supported formats:
                #   check_pixel|(x,y,r,g,b,tol)|delay {   ← { same line
                #   check_pixel|(x,y,r,g,b,tol)|delay     ← { next line
                #   (extra numbers like timeout are ignored)
                header_line = line.rstrip()
                has_brace = header_line.endswith('{')
                if has_brace:
                    header_line = header_line[:-1].rstrip()

                try:
                    parts = header_line.split('|')
                    # parts: ['check_pixel', '(x,y,r,g,b,tol[,...])', 'delay']
                    nums = list(map(float, re.findall(r'[\d.]+', parts[1])))
                    delay = float(parts[2].strip()) if len(parts) > 2 else 0.0

                    if not has_brace:
                        # look ahead for { on next non-blank line
                        j = i
                        while j < len(lines) and not lines[j].strip():
                            j += 1
                        if j < len(lines) and lines[j].strip() == '{':
                            i = j + 1  # consume the { line
                        else:
                            logger.warning(f"check_pixel missing '{{': {line}")
                            continue

                    block_events, new_i = self._parse_lines(lines, i, indent_level + 1, loaded_files)
                    i = new_i
                    events.append(CheckPixelEvent(
                        x=int(nums[0]), y=int(nums[1]),
                        r=int(nums[2]), g=int(nums[3]), b=int(nums[4]),
                        tolerance=int(nums[5]),  # nums[6+] (e.g. timeout) ignored
                        events=block_events, delay=delay,
                    ))
                except Exception as e:
                    logger.warning(f"Syntax error on check_pixel: {line} — {e}")

            else:
                try:
                    event_type, event_data_str, delay_str = line.split('|')
                    delay = float(delay_str)

                    if event_type == 'move':
                        nums = re.findall(r'([-+]?\d*\.?\d+)', event_data_str)
                        events.append(MoveEvent(x=float(nums[0]), y=float(nums[1]), delay=delay))
                    elif event_type == 'smooth_move':
                        m_pos = re.search(r'\(?\s*([-+]?\d*\.?\d+)\s*,\s*([-+]?\d*\.?\d+)\s*\)?', event_data_str)
                        x_val = float(m_pos.group(1))
                        y_val = float(m_pos.group(2))
                        def _gn_opt(name, default=0.0):
                            m = re.search(rf'{name}=([\d.]+)', event_data_str)
                            return float(m.group(1)) if m else default
                        def _gs_opt(name):
                            m = re.search(rf'(?:^|;){name}=(\w+)(?:;|$)', event_data_str)
                            return m.group(1) if m else None

                        events.append(SmoothMoveEvent(
                            x=x_val, y=y_val,
                            button=_gs_opt('button'),
                            move_time=_gn_opt('move_time', 0.0),
                            bezier_ratio=_gn_opt('bezier_ratio', 0.7),
                            delay=delay,
                        ))
                    elif event_type in ('set_timer', 'reset_timer'):
                        events.append(SetTimerEvent(name=event_data_str.strip(' ()'), delay=delay))
                    elif event_type == 'click':
                        match = re.match(r"\(([-+]?\d*\.\d+|\d+), ([-+]?\d*\.\d+|\d+), <Button\.(\w+): .+>, (True|False)\)", event_data_str)
                        if match:
                            x, y, button_name, pressed_str = match.groups()
                            button = getattr(mouse.Button, button_name)
                            events.append(ClickEvent(x=float(x), y=float(y), button=button, pressed=pressed_str == 'True', delay=delay))
                    elif event_type == 'scroll':
                        x, y, dx, dy = map(float, re.findall(r'([-+]?\d*\.?\d+)', event_data_str))
                        events.append(ScrollEvent(x=x, y=y, dx=dx, dy=dy, delay=delay))
                    elif event_type == 'spacebar':
                        events.append(SpacebarEvent(pressed=event_data_str.strip() == 'True', delay=delay))
                    elif event_type == 'wait_pixel':
                        match = re.match(r"\(([-+]?\d*\.\d+|\d+), ([-+]?\d*\.\d+|\d+), ([-+]?\d*\.\d+|\d+), ([-+]?\d*\.\d+|\d+), ([-+]?\d*\.\d+|\d+), ([-+]?\d*\.\d+|\d+), ([-+]?\d*\.\d+|\d+)\)", event_data_str)
                        if match:
                            events.append(WaitPixelEvent(
                                x=int(match.group(1)), y=int(match.group(2)),
                                r=int(match.group(3)), g=int(match.group(4)), b=int(match.group(5)),
                                tolerance=int(match.group(6)), timeout=float(match.group(7)),
                                delay=delay
                            ))
                    elif event_type == 'wait_pixels':
                        raw_pixels = re.findall(r'\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', event_data_str)
                        px_tuples = tuple((int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5])) for p in raw_pixels)
                        m_mode = re.search(r'mode=(\w+)', event_data_str)
                        mode_val = m_mode.group(1) if m_mode else 'and'
                        m_tout = re.search(r'timeout=([\d.]+)', event_data_str)
                        tout_val = float(m_tout.group(1)) if m_tout else 10.0
                        events.append(WaitPixelsEvent(pixels=px_tuples, mode=mode_val, timeout=tout_val, delay=delay))
                    elif event_type == 'log':
                        events.append(LogEvent(message=event_data_str.strip(), delay=delay))
                    elif event_type == 'random_wait':
                        nums = re.findall(r'[\d.]+', event_data_str)
                        events.append(RandomWaitEvent(
                            min_t=float(nums[0]), max_t=float(nums[1]),
                            delay=delay,
                        ))
                    elif event_type == 'find_fishing_spot':
                        def _g4(name):
                            m = re.search(rf'{name}=\((\d+),(\d+),(\d+),(\d+)\)', event_data_str)
                            return tuple(int(x) for x in m.groups())
                        def _g2(name):
                            m = re.search(rf'{name}=\((\d+),(\d+)\)', event_data_str)
                            return tuple(int(x) for x in m.groups())
                        def _g3(name):
                            m = re.search(rf'{name}=\((\d+),(\d+),(\d+)\)', event_data_str)
                            return tuple(int(x) for x in m.groups())
                        def _gn(name):
                            return re.search(rf'{name}=([\d.]+)', event_data_str).group(1)
                        def _gs(name):
                            return re.search(rf'{name}=(\w+)', event_data_str).group(1)
                        def _gn_opt(name, default=0.0):
                            m = re.search(rf'{name}=([\d.]+)', event_data_str)
                            return float(m.group(1)) if m else default
                        def _g2_opt(name):
                            m = re.search(rf'{name}=\((\d+),(\d+)\)', event_data_str)
                            return tuple(int(x) for x in m.groups()) if m else None
                        def _gs_opt(name):
                            # require key to be preceded by start/semicolon and
                            # value followed by semicolon/pipe/end to avoid
                            # partial matches in other key names
                            m = re.search(rf'(?:^|;){name}=(\w+)(?:;|$)', event_data_str)
                            return m.group(1) if m else None
                        events.append(FindFishingSpotEvent(
                            region=_g4('region'),
                            color=_g3('color'), tol=int(_gn('tol')),
                            timeout=float(_gn('timeout')),
                            char=_g2_opt('char'), button=_gs_opt('button'),
                            move_time=_gn_opt('move_time', 0.0),
                            bezier_ratio=_gn_opt('bezier_ratio', 0.7),
                            delay=delay,
                        ))
                except Exception as e:
                    logger.warning(f"Error parsing line: {line} - {e}")
                    continue
                    
        return events, i

class Writer:
    def save(self, filename, events):
        """Save mouse events to a text file"""
        try:
            with open(filename, 'w') as f:
                self._write_events(f, events)
            logger.info(f"Events saved to {filename}")
        except Exception as e:
            logger.warning(f"Error saving file {filename}: {e}")

    def _write_events(self, f, events, indent_level=0):
        indent = "  " * indent_level

        for event in events:
            if isinstance(event, LoopEvent):
                f.write(f"{indent}loop {event.count} {{\n")
                self._write_events(f, event.events, indent_level + 1)
                f.write(f"{indent}}}\n")
            elif isinstance(event, SetTimerEvent):
                f.write(f"{indent}set_timer|{event.name}|{event.delay}\n")
            elif isinstance(event, CheckTimerEvent):
                f.write(
                    f"{indent}check_timer|({event.name}, {event.duration})|{event.delay} {{\n"
                )
                self._write_events(f, event.events, indent_level + 1)
                f.write(f"{indent}}}\n")
            elif isinstance(event, CheckPixelEvent):
                f.write(
                    f"{indent}check_pixel|({event.x}, {event.y}, {event.r}, "
                    f"{event.g}, {event.b}, {event.tolerance})|{event.delay} {{\n"
                )
                self._write_events(f, event.events, indent_level + 1)
                f.write(f"{indent}}}\n")
            elif isinstance(event, MoveEvent):
                f.write(f"{indent}move|({event.x}, {event.y})|{event.delay}\n")
            elif isinstance(event, SmoothMoveEvent):
                f.write(
                    f"{indent}smooth_move|({event.x}, {event.y});"
                    + (f"button={event.button};" if event.button else "")
                    + f"move_time={event.move_time};bezier_ratio={event.bezier_ratio}|{event.delay}\n"
                )
            elif isinstance(event, ClickEvent):
                btn_repr = f"<Button.{event.button.name}: ((0,0,0),0)>"
                f.write(f"click|({event.x}, {event.y}, {btn_repr}, {event.pressed})|{event.delay}\n")
            elif isinstance(event, ScrollEvent):
                f.write(f"scroll|({event.x}, {event.y}, {event.dx}, {event.dy})|{event.delay}\n")
            elif isinstance(event, SpacebarEvent):
                f.write(f"spacebar|{event.pressed}|{event.delay}\n")
            elif isinstance(event, WaitPixelEvent):
                f.write(
                    f"{indent}wait_pixel|({event.x}, {event.y}, {event.r}, {event.g}, {event.b}, "
                    f"{event.tolerance}, {event.timeout})|{event.delay}\n"
                )
            elif isinstance(event, WaitPixelsEvent):
                px_str = ",".join(f"({p[0]},{p[1]},{p[2]},{p[3]},{p[4]},{p[5]})" for p in event.pixels)
                f.write(f"{indent}wait_pixels|({px_str});mode={event.mode};timeout={event.timeout}|{event.delay}\n")
            elif isinstance(event, LogEvent):
                f.write(f"log|{event.message}|{event.delay}\n")
            elif isinstance(event, FindFishingSpotEvent):
                rg, co = event.region, event.color
                f.write(
                    f"find_fishing_spot|"
                    f"region=({rg[0]},{rg[1]},{rg[2]},{rg[3]});"
                    f"color=({co[0]},{co[1]},{co[2]});"
                    f"tol={event.tol};timeout={event.timeout};"
                    + (f"char=({event.char[0]},{event.char[1]});" if event.char else "")
                    + (f"button={event.button};" if event.button else "")
                    + f"move_time={event.move_time};bezier_ratio={event.bezier_ratio}|{event.delay}\n"
                )
            elif isinstance(event, RandomWaitEvent):
                f.write(f"random_wait|({event.min_t}, {event.max_t})|{event.delay}\n")

class Editor:
    def scale_timing(self, events, scale_factor):
        """Scale the timing of all events by a factor (mutates in-place)"""
        for event in events:
            if isinstance(event, LoopEvent):
                self.scale_timing(event.events, scale_factor)
            elif isinstance(event, CheckTimerEvent):
                event.delay *= scale_factor
                self.scale_timing(event.events, scale_factor)
            elif isinstance(event, SetTimerEvent):
                event.delay *= scale_factor
            elif isinstance(event, CheckPixelEvent):
                event.delay *= scale_factor
                self.scale_timing(event.events, scale_factor)
            elif isinstance(event, FindFishingSpotEvent):
                event.delay *= scale_factor
                event.move_time *= scale_factor
            elif isinstance(event, SmoothMoveEvent):
                event.delay *= scale_factor
                event.move_time *= scale_factor
            elif isinstance(event, RandomWaitEvent):
                event.delay *= scale_factor
                event.min_t *= scale_factor
                event.max_t *= scale_factor
            else:
                event.delay *= scale_factor
        return events

    def get_total_time(self, events):
        total_time_seconds = self._calculate_total_seconds(events)
        return self._get_readable_time(total_time_seconds)

    def _calculate_total_seconds(self, events):
        total = 0
        for event in events:
            if isinstance(event, LoopEvent):
                total += self._calculate_total_seconds(event.events) * event.count
            elif isinstance(event, CheckTimerEvent):
                total += event.delay + self._calculate_total_seconds(event.events)
            elif isinstance(event, SetTimerEvent):
                total += event.delay
            elif isinstance(event, CheckPixelEvent):
                # inner events may or may not run; count delay + inner as best-case
                total += event.delay + self._calculate_total_seconds(event.events)
            elif isinstance(event, FindFishingSpotEvent):
                total += event.delay + event.move_time
            elif isinstance(event, SmoothMoveEvent):
                total += event.delay + event.move_time
            elif isinstance(event, RandomWaitEvent):
                total += event.delay + (event.min_t + event.max_t) / 2
            else:
                total += event.delay
        return total

    def _get_readable_time(self, seconds):
        time_string = ""
        hours = int(seconds // 3600)
        if hours > 0:
            time_string += f"{hours}h "
        minutes = int((seconds % 3600) // 60)
        if minutes > 0:
            time_string += f"{minutes}m "
        time_string += f"{seconds:.2f}s"
        return time_string

class Recorder:
    def __init__(self, granularity=0.01):
        self.granularity = granularity
        self.mouse_events = []
        self.recording = False
        self.start_time = 0
        self.last_event_time = 0
        self.lock = threading.Lock()
        self.mouse_controller = mouse.Controller()
        self.thread = None
        self.listener_mouse = None

    def start(self):
        if self.recording: return
        print("Starting Recording!")
        self.recording = True
        self.mouse_events = []
        self.start_time = time.time()
        self.last_event_time = self.start_time
        
        pos = self.mouse_controller.position
        with self.lock:
            self.mouse_events.append(MoveEvent(x=pos[0], y=pos[1], delay=0))

        self.thread = threading.Thread(target=self._record_loop, daemon=True)
        self.thread.start()
        
        # Start listener (if not globally managed, but here we can attach it)
        # Note: In the original, listeners were global. Ideally, we attach/detach them here.
        # For now, we will rely on global hooks calling callbacks on this instance, 
        # or we start a temporary listener. Let's make this class handle its own listeners for cleaner OOP.
        self.listener_mouse = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        self.listener_mouse.start()

    def stop(self):
        if not self.recording: return
        self.recording = False
        if self.thread:
            self.thread.join()
        if self.listener_mouse:
            self.listener_mouse.stop()
        print("Recording stopped!")

    def _record_loop(self):
        while self.recording:
            position = self.mouse_controller.position
            with self.lock:
                last = self.mouse_events[-1] if self.mouse_events else None
            last_pos = (last.x, last.y) if isinstance(last, MoveEvent) else None

            if position != last_pos:
                current_time = time.time()
                delay = current_time - self.last_event_time
                print(f"{current_time - self.start_time:.2f} [{delay:.2f}]: Recording mouse position: {position}")
                with self.lock:
                    self.mouse_events.append(MoveEvent(x=position[0], y=position[1], delay=delay))
                self.last_event_time = current_time
            time.sleep(self.granularity)

    def _on_click(self, x, y, button, pressed):
        if self.recording:
            current_time = time.time()
            delay = current_time - self.last_event_time
            print(f"Recording click at {x}, {y} {button} {pressed}")
            with self.lock:
                self.mouse_events.append(ClickEvent(x=x, y=y, button=button, pressed=pressed, delay=delay))
            self.last_event_time = current_time

    def _on_scroll(self, x, y, dx, dy):
        if self.recording:
            current_time = time.time()
            delay = current_time - self.last_event_time
            print(f"Recording scroll {dx}, {dy}")
            with self.lock:
                self.mouse_events.append(ScrollEvent(x=x, y=y, dx=dx, dy=dy, delay=delay))
            self.last_event_time = current_time

    def on_spacebar(self, pressed):
        if self.recording:
            current_time = time.time()
            delay = current_time - self.last_event_time
            print(f"Recording spacebar {'press' if pressed else 'release'}")
            with self.lock:
                self.mouse_events.append(SpacebarEvent(pressed=pressed, delay=delay))
            self.last_event_time = current_time

    def get_events(self):
        with self.lock:
            return self.mouse_events  # no copy needed after stop()

class Player:
    def __init__(self, controller_mouse, controller_keyboard, position_threshold=5.0, delay_threshold=0.05, verbose=False):
        self.mouse = controller_mouse
        self.keyboard = controller_keyboard
        self.position_threshold = position_threshold
        self.delay_threshold = delay_threshold
        self.verbose = verbose
        self.playing = False
        self.thread = None
        self.max_duration = None
        self.timers = {}

    def play(self, events, loop_count=1, dry_run=False, max_duration=None):
        if self.playing: return
        print("Starting Playback!")
        self.playing = True
        self.max_duration = max_duration
        self.timers = {}
        
        if dry_run:
            logger.info("--- DRY RUN START ---")
            self._dry_run_recursive(events)
            logger.info("--- DRY RUN END ---")
            self.playing = False
            return

        self._start_time = time.time()
        self._iteration = 0
        self._event_count = 0
        self.thread = threading.Thread(target=self._play_loop, args=(events, loop_count), daemon=True)
        self.thread.start()

    def stop(self):
        if not self.playing: return
        self.playing = False
        if self.thread:
            self.thread.join()
        print()  # clear metrics line
        print("Playback stopped!")

    def _play_loop(self, events, loop_count):
        current_loop = loop_count
        total = loop_count if loop_count != -1 else None

        while self.playing:
            if self.max_duration and (time.time() - self._start_time) >= self.max_duration:
                if self.verbose:
                    sys.stdout.write(f"\n[{_ts()}][INFO] Max duration {self.max_duration}s reached — stopping playback\n")
                    sys.stdout.flush()
                break

            if current_loop != -1:
                if current_loop == 0:
                    break
                current_loop -= 1

            iter_start = time.time()
            try:
                self._execute_recursive(events)
            except _PixelTimeout as e:
                if self.verbose:
                    sys.stdout.write(f"\n[{_ts()}][TIMEOUT] {e} — restarting iteration\n")
                    sys.stdout.flush()
                self.timers.clear()  # Fix #6: purge stale timers on pixel timeout iteration restart
                try:
                    self._execute_recursive(events)
                except _PixelTimeout as e2:
                    if self.verbose:
                        sys.stdout.write(f"\n[{_ts()}][TIMEOUT] {e2} on restart — stopping playback\n")
                        sys.stdout.flush()
                    self.playing = False
                    break
            self._iteration += 1

            elapsed = time.time() - self._start_time
            iter_time = time.time() - iter_start
            rate = self._iteration / elapsed if elapsed > 0 else 0

            now = time.time()
            timer_str = ""
            if self.timers:
                t_parts = [f"{k}:{now - v:.1f}s" for k, v in self.timers.items()]
                timer_str = f"  timers={{{', '.join(t_parts)}}}"

            elapsed_str = self._format_time(elapsed)
            if total is not None:
                done = self._iteration
                eta = ((total - done) / rate) if rate > 0 else float('inf')
                eta_str = self._format_time(eta)
                line = (
                    f"iter={done}/{total}  elapsed={elapsed_str}  "
                    f"iter_time={iter_time:.2f}s  rate={rate:.2f}/s  "
                    f"eta={eta_str}  events={self._event_count}{timer_str}"
                )
            else:
                line = (
                    f"iter={self._iteration}  elapsed={elapsed_str}  "
                    f"iter_time={iter_time:.2f}s  rate={rate:.2f}/s  "
                    f"events={self._event_count}{timer_str}"
                )

            sys.stdout.write(f"\r\033[K{line}")
            sys.stdout.flush()

        self.playing = False

    def _format_time(self, seconds):
        if seconds == float('inf') or seconds is None or math.isnan(seconds):
            return "?"
        seconds = int(round(seconds))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if secs > 0 or not parts:
            parts.append(f"{secs}s")
        return " ".join(parts)

    def _execute_recursive(self, events, level=0):
        if not self.playing: return
        if self.max_duration and (time.time() - self._start_time) >= self.max_duration:
            self.playing = False
            return
        indent = '  ' * level

        for event in events:
            if not self.playing: break

            if isinstance(event, LoopEvent):
                if self.verbose:
                    print(f"{indent}Looping {event.count} times...")
                for _ in range(event.count):
                    if not self.playing: break
                    self._execute_recursive(event.events, level + 1)
                continue

            if isinstance(event, SetTimerEvent):
                self.timers[event.name] = time.time()
                if self.verbose:
                    sys.stdout.write(f"\r\033[K[{_ts()}][TIMER] set '{event.name}'\n")
                    sys.stdout.flush()
                rand_delay = random.uniform(-self.delay_threshold, self.delay_threshold)
                time.sleep(max(0, event.delay * (1 + rand_delay)))
                continue

            if isinstance(event, CheckTimerEvent):
                now = time.time()
                start_t = self.timers.get(event.name)
                if start_t is None:
                    matched = True
                    sys.stdout.write(f"\r\033[K[{_ts()}][TIMER] '{event.name}' not set yet — entering conditional\n")
                    sys.stdout.flush()
                else:
                    elapsed = now - start_t
                    matched = elapsed >= event.duration
                    if self.verbose:
                        status = "PASS" if matched else "SKIP"
                        sys.stdout.write(f"\r\033[K[{_ts()}][TIMER] check '{event.name}' ({elapsed:.1f}s / {event.duration}s) — {status}\n")
                        sys.stdout.flush()

                if matched:
                    self._execute_recursive(event.events, level + 1)

                rand_delay = random.uniform(-self.delay_threshold, self.delay_threshold)
                time.sleep(max(0, event.delay * (1 + rand_delay)))
                continue

            if isinstance(event, CheckPixelEvent):
                pr, pg, pb = 0, 0, 0  # Fix #1: default fallback prevents UnboundLocalError if grab fails
                try:
                    import mss as _mss
                    with _mss.MSS() as sct:
                        shot = sct.grab({"left": event.x, "top": event.y, "width": 1, "height": 1})
                        pr, pg, pb = shot.raw[2], shot.raw[1], shot.raw[0]  # BGRA
                    matched = (
                        abs(pr - event.r) <= event.tolerance and
                        abs(pg - event.g) <= event.tolerance and
                        abs(pb - event.b) <= event.tolerance
                    )
                except Exception as e:
                    logger.warning(f"check_pixel grab failed: {e}")
                    matched = False
                if self.verbose:
                    status = "PASS" if matched else "SKIP"
                    sys.stdout.write(f"\r\033[K[{_ts()}][CHECK] ({event.x},{event.y}) rgb=({pr},{pg},{pb}) — {status}\n")
                    sys.stdout.flush()
                if matched:
                    self._execute_recursive(event.events, level + 1)
                rand_delay = random.uniform(-self.delay_threshold, self.delay_threshold)
                time.sleep(max(0, event.delay * (1 + rand_delay)))
                continue

            if self.verbose:
                print(f"{indent}Playing: {type(event).__name__}, delay: {event.delay}")

            if isinstance(event, MoveEvent):
                dx = random.uniform(-self.position_threshold, self.position_threshold)
                dy = random.uniform(-self.position_threshold, self.position_threshold)
                self.mouse.position = (event.x + dx, event.y + dy)
            elif isinstance(event, ClickEvent):
                if event.pressed: self.mouse.press(event.button)
                else: self.mouse.release(event.button)
            elif isinstance(event, ScrollEvent):
                self.mouse.scroll(event.dx, event.dy)
            elif isinstance(event, SpacebarEvent):
                if event.pressed: self.keyboard.press(keyboard.Key.space)
                else: self.keyboard.release(keyboard.Key.space)
            elif isinstance(event, WaitPixelEvent):
                self._wait_pixel(event)
            elif isinstance(event, WaitPixelsEvent):
                self._wait_pixels(event)
            elif isinstance(event, FindFishingSpotEvent):
                self._find_fishing_spot(event)
            elif isinstance(event, SmoothMoveEvent):
                self._execute_smooth_move_and_click(
                    event.x, event.y,
                    button=event.button,
                    move_time=event.move_time,
                    bezier_ratio=event.bezier_ratio,
                    delay=event.delay,
                    log_tag="MOVE",
                )
            elif isinstance(event, RandomWaitEvent):
                wait = random.uniform(event.min_t, event.max_t)
                if self.verbose:
                    sys.stdout.write(f"\r\033[K[{_ts()}][WAIT] sleeping {wait:.2f}s ({event.min_t}-{event.max_t}s)\n")
                    sys.stdout.flush()
                time.sleep(wait)
            elif isinstance(event, LogEvent):
                if self.verbose:
                    sys.stdout.write(f"\r\033[K[{_ts()}][LOG] {event.message}\n")
                    sys.stdout.flush()

            self._event_count += 1
            # FindFishingSpotEvent and SmoothMoveEvent consume delay internally (pre-click settle)
            if not isinstance(event, (FindFishingSpotEvent, SmoothMoveEvent)):
                rand_delay = random.uniform(-self.delay_threshold, self.delay_threshold)
                actual_delay = max(0, event.delay * (1 + rand_delay))
                time.sleep(actual_delay)

    def _wait_pixel(self, event):
        try:
            import mss
        except ImportError:
            logger.error("wait_pixel requires mss: pip install mss")
            return

        deadline = time.time() + event.timeout
        with mss.MSS() as sct:
            monitor = {"top": event.y, "left": event.x, "width": 1, "height": 1}
            while time.time() < deadline and self.playing:
                try:
                    shot = sct.grab(monitor)
                    b, g, r = shot.raw[0], shot.raw[1], shot.raw[2]  # BGRA
                    if (abs(r - event.r) <= event.tolerance and
                            abs(g - event.g) <= event.tolerance and
                            abs(b - event.b) <= event.tolerance):
                        return  # matched
                except Exception as e:
                    logger.warning(f"wait_pixel grab error: {e}")
                    return
                time.sleep(0.05)
        raise _PixelTimeout(
            f"wait_pixel timed out after {event.timeout}s "
            f"at ({event.x}, {event.y}) target=({event.r},{event.g},{event.b})"
        )

    def _wait_pixels(self, event):
        """Wait until multiple pixel conditions (AND/OR) match within tolerance or timeout."""
        try:
            import mss
        except ImportError:
            logger.error("wait_pixels requires mss: pip install mss")
            self.playing = False
            return

        deadline = time.time() + event.timeout
        with mss.MSS() as sct:
            while time.time() < deadline and self.playing:
                results = []
                for (px, py, pr, pg, pb, tol) in event.pixels:
                    monitor = {"top": py, "left": px, "width": 1, "height": 1}
                    try:
                        shot = sct.grab(monitor)
                        b_v, g_v, r_v = shot.raw[0], shot.raw[1], shot.raw[2]
                        matched = (
                            abs(r_v - pr) <= tol and
                            abs(g_v - pg) <= tol and
                            abs(b_v - pb) <= tol
                        )
                        results.append(matched)
                    except Exception as e:
                        logger.warning(f"wait_pixels grab error: {e}")
                        results.append(False)

                if event.mode == 'and' and all(results):
                    return
                elif event.mode == 'or' and any(results):
                    return
                time.sleep(0.05)

        if not self.playing:
            return
        raise _PixelTimeout(f"wait_pixels: condition {event.mode!r} not met after {event.timeout}s")

    def _smooth_move(self, x0, y0, x1, y1, move_time, bezier_ratio=0.7):
        """Move mouse from (x0,y0) to (x1,y1) over move_time seconds.
        Randomly chooses between a cubic Bézier arc and an overshoot path.
        bezier_ratio=1.0 always Bézier, 0.0 always overshoot. move_time=0 → instant."""
        if move_time <= 0:
            self.mouse.position = (x1, y1)
            return

        steps = max(10, int(move_time * 60))  # ~60fps
        dt = move_time / steps

        if random.random() < bezier_ratio:
            # ── Cubic Bézier arc ──────────────────────────────────────────
            # Two control points offset perpendicular to the straight path,
            # creating a natural arc. Arc amount is randomised per move.
            dx, dy = x1 - x0, y1 - y0
            dist = math.sqrt(dx*dx + dy*dy) or 1.0
            px, py = -dy / dist, dx / dist          # perpendicular unit
            arc = random.uniform(0.1, 0.4) * dist * random.choice([-1, 1])
            cp1 = (x0 + dx*0.25 + px*arc, y0 + dy*0.25 + py*arc)
            cp2 = (x0 + dx*0.75 + px*arc, y0 + dy*0.75 + py*arc)
            for i in range(1, steps + 1):
                t = i / steps
                s = t * t * (3 - 2 * t)             # smoothstep speed profile
                u = 1 - s
                bx = u**3*x0 + 3*u**2*s*cp1[0] + 3*u*s**2*cp2[0] + s**3*x1
                by = u**3*y0 + 3*u**2*s*cp1[1] + 3*u*s**2*cp2[1] + s**3*y1
                self.mouse.position = (bx, by)
                time.sleep(dt)
        else:
            # ── Overshoot + ease-back ─────────────────────────────────────
            # Move ~5-15% past the target, then ease back to the real target.
            overshoot = random.uniform(0.05, 0.15)
            ox = x1 + (x1 - x0) * overshoot
            oy = y1 + (y1 - y0) * overshoot
            phase1 = max(1, int(steps * 0.75))      # 75% of time to reach overshoot
            phase2 = max(1, steps - phase1)          # 25% to ease back
            for i in range(1, phase1 + 1):
                t = i / phase1
                s = t * t * (3 - 2 * t)
                self.mouse.position = (x0 + (ox - x0)*s, y0 + (oy - y0)*s)
                time.sleep(dt)
            for i in range(1, phase2 + 1):
                t = i / phase2
                s = t * t * (3 - 2 * t)
                self.mouse.position = (ox + (x1 - ox)*s, oy + (y1 - oy)*s)
                time.sleep(dt)

    def _execute_smooth_move_and_click(self, target_x, target_y, button=None, move_time=0.0, bezier_ratio=0.7, delay=0.0, log_tag="MOVE", info_extra=""):
        """Performs non-linear cursor glide to target coordinate, followed by pre-click settle delay and optional click."""
        tx = target_x + random.uniform(-self.position_threshold, self.position_threshold)
        ty = target_y + random.uniform(-self.position_threshold, self.position_threshold)
        _BTN_MAP = {'left': mouse.Button.left, 'right': mouse.Button.right}
        btn = _BTN_MAP.get(button) if button else None
        if button and btn is None:
            logger.warning(f"{log_tag}: unknown button={button!r}, skipping click")

        x0, y0 = self.mouse.position
        rand_move = random.uniform(-self.delay_threshold, self.delay_threshold)
        actual_move_time = max(0, move_time * (1 + rand_move))
        self._smooth_move(x0, y0, tx, ty, actual_move_time, bezier_ratio)

        # front-load delay: let cursor settle before clicking
        rand_delay = random.uniform(-self.delay_threshold, self.delay_threshold)
        time.sleep(max(0, delay * (1 + rand_delay)))

        if btn is not None:
            self.mouse.press(btn)
            time.sleep(0.05)
            self.mouse.release(btn)

        if self.verbose:
            action = f"clicked({button})" if btn is not None else "moved"
            sys.stdout.write(f"\n[{_ts()}][{log_tag}] {action} to ({int(tx)},{int(ty)}){info_extra}\n")
            sys.stdout.flush()

    def _find_fishing_spot(self, event):
        try:
            import mss as _mss
        except ImportError:
            logger.error("find_fishing_spot requires mss: pip install mss")
            self.playing = False  # #3: halt rather than silently skip
            return

        r_t, g_t, b_t = event.color
        rx1, ry1, rx2, ry2 = event.region
        reg_mon = {"left": rx1, "top": ry1, "width": rx2 - rx1, "height": ry2 - ry1}
        deadline = time.time() + event.timeout

        with _mss.MSS() as sct:
            while time.time() < deadline and self.playing:
                spots = self._find_color_clusters(sct, reg_mon, rx1, ry1, r_t, g_t, b_t, event.tol)
                if spots:
                    if event.char:
                        tx, ty = min(spots, key=lambda p: (p[0]-event.char[0])**2 + (p[1]-event.char[1])**2)
                    else:
                        tx, ty = random.choice(spots)
                    self._execute_smooth_move_and_click(
                        tx, ty,
                        button=event.button,
                        move_time=event.move_time,
                        bezier_ratio=event.bezier_ratio,
                        delay=event.delay,
                        log_tag="FISH",
                        info_extra=f", {len(spots)} spot(s) available",
                    )
                    return
                time.sleep(0.2)

        # #2: abort (self.playing=False) must not be treated as a timeout
        if not self.playing:
            return
        raise _PixelTimeout(f"find_fishing_spot: no spot found after {event.timeout}s in {event.region}")

    def _color_in_region(self, sct, monitor, r_t, g_t, b_t, tol, stride=4):
        """True if any pixel in region matches color within tolerance."""
        shot = sct.grab(monitor)
        raw, w, h = shot.raw, shot.width, shot.height
        for py in range(0, h, stride):
            row = py * w
            for px in range(0, w, stride):
                idx = (row + px) * 4
                if (abs(raw[idx+2] - r_t) <= tol and
                        abs(raw[idx+1] - g_t) <= tol and
                        abs(raw[idx]   - b_t) <= tol):
                    return True
        return False

    def _find_color_clusters(self, sct, monitor, off_x, off_y, r_t, g_t, b_t, tol, stride=2):
        """Returns list of (x, y) bounding-box centers per cluster in screen coords.

        Each cluster: [sum_x, sum_y, count, min_x, max_x, min_y, max_y].
        Centroid = sum/count  →  O(1) lookup.
        Bbox update           →  O(1) per point.
        Overall: O(N·M), N=matching pixels, M=cluster count (typically 1-5).
        """
        shot = sct.grab(monitor)
        raw, w, h = shot.raw, shot.width, shot.height
        CLUSTER_DIST = 40
        clusters = []  # [sum_x, sum_y, count, min_x, max_x, min_y, max_y]

        for py in range(0, h, stride):
            row = py * w
            for px in range(0, w, stride):
                idx = (row + px) * 4
                if not (abs(raw[idx+2] - r_t) <= tol and
                        abs(raw[idx+1] - g_t) <= tol and
                        abs(raw[idx]   - b_t) <= tol):
                    continue

                gx, gy = px + off_x, py + off_y
                placed = False
                for c in clusters:
                    # O(1) centroid — no recompute over all cluster points
                    if (abs(gx - c[0] / c[2]) <= CLUSTER_DIST and
                            abs(gy - c[1] / c[2]) <= CLUSTER_DIST):
                        c[0] += gx; c[1] += gy; c[2] += 1
                        if gx < c[3]: c[3] = gx  # min_x
                        if gx > c[4]: c[4] = gx  # max_x
                        if gy < c[5]: c[5] = gy  # min_y
                        if gy > c[6]: c[6] = gy  # max_y
                        placed = True
                        break
                if not placed:
                    clusters.append([gx, gy, 1, gx, gx, gy, gy])

        # Bounding-box center: stable even with uneven perimeter sampling
        return [((c[3] + c[4]) // 2, (c[5] + c[6]) // 2) for c in clusters]


    def _dry_run_recursive(self, events, level=0):
        indent = '  ' * level
        for event in events:
            if isinstance(event, LoopEvent):
                logger.info(f"{indent}Loop {event.count} times {{")
                self._dry_run_recursive(event.events, level + 1)
                logger.info(f"{indent}}}")

# --- Duration helpers ---

def _parse_duration(s):
    """Parse hh:mm:ss, mm:ss, or raw seconds into a float of seconds."""
    parts = s.strip().split(':')
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        raise ValueError(f"Invalid duration format {s!r} — use hh:mm:ss, mm:ss, or seconds")

def _duration_stop(player, secs):
    """Sleep for secs then stop the player (runs in a daemon thread)."""
    time.sleep(secs)
    if player.playing:
        player.stop()

# --- Main Application ---

class AutoClickerApp:
    def __init__(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument('-g', '--granularity', type=float, default=0.01, help='Granularity for recording (s)')
        self.parser.add_argument('-c', '--count', type=int, default=1, help='Loop count (-1 = infinite)')
        self.parser.add_argument('-t', '--time', type=str, default=None, help='Run duration hh:mm:ss (loops infinitely until time is up)')
        self.parser.add_argument('-d', '--delay_threshold', type=float, default=0, help='Delay Jitter (s)')
        self.parser.add_argument('-p', '--position_threshold', type=float, default=0, help='Position Jitter (px)')
        self.parser.add_argument('-f', '--file', type=str, default='mouse_events.txt', help='File to load/save')
        self.parser.add_argument('-s', '--scale', type=float, default=1.0, help='Timing scale factor')
        self.parser.add_argument('-o', '--output', type=str, help='Output file (default: overwrites input file)')
        self.parser.add_argument('--dry-run', action='store_true', help='Dry run')
        self.parser.add_argument('--verbose', action='store_true', help='Print each event during playback')
        self.args = self.parser.parse_args()

        if self.args.verbose:
            logging.getLogger().setLevel(logging.INFO)

        self.reader = Reader()
        self.writer = Writer()
        self.editor = Editor()
        self.recorder = Recorder(granularity=self.args.granularity)
        self.player = Player(
            mouse.Controller(),
            keyboard.Controller(),
            position_threshold=self.args.position_threshold,
            delay_threshold=self.args.delay_threshold,
            verbose=self.args.verbose,
        )
        
        self.running = True
        self.loaded_events = []

    def run(self):
        # Auto-load or dry-run
        self.loaded_events = self.reader.load(self.args.file)
        if self.args.dry_run:
            self.player.play(self.loaded_events, loop_count=self.args.count, dry_run=True)
            return

        print("\n=== Controls ===")
        print("R: Start Recording")
        print("S: Stop Recording")
        print("P: Playback")
        print("E: Stop Playback")
        print("W: Save to file")
        print("L: Load from file")
        print("ESC: Quit")
        
        # We need a Persistent Keyboard Listener for global hotkeys
        with keyboard.Listener(on_press=self._on_key_press, on_release=self._on_key_release) as listener:
            try:
                listener.join()
            except KeyboardInterrupt:
                self.running = False
                self.recorder.stop()
                self.player.stop()
                print("\nExiting...")

    def _on_key_press(self, key):
        try:
            # Spacebar special handling for recorder
            if key == keyboard.Key.space and self.recorder.recording:
                self.recorder.on_spacebar(True)
                return

            if not hasattr(key, 'char'): return

            if key.char in ['r', 'R'] and not self.recorder.recording and not self.player.playing:
                self.recorder.start()
            
            elif key.char in ['s', 'S'] and self.recorder.recording:
                self.recorder.stop()
                self.loaded_events = self.recorder.get_events() # Update working memory
            
            elif key.char in ['t', 'T'] and not self.recorder.recording and not self.player.playing:
                logger.info(f"Scaling timing by factor: {self.args.scale}")
                self.editor.scale_timing(self.loaded_events, self.args.scale)
                logger.info(f"Scaled timing complete.")

            elif key.char in ['i', 'I'] and not self.recorder.recording and not self.player.playing:
                try:
                    scale_factor = float(input("Enter timing scale factor (e.g., 0.5 for 2x faster): \n"))
                    logger.info(f"Scaling timing by factor: {scale_factor}")
                    self.editor.scale_timing(self.loaded_events, scale_factor)
                    logger.info(f"Scaled timing complete.")
                except ValueError:
                    logger.warning("Invalid scale factor.")
                except Exception as e:
                    logger.warning(f"Error scaling: {e}")
            
            elif key.char in ['w', 'W']:
                out_file = self.args.output if self.args.output else self.args.file
                self.writer.save(out_file, self.loaded_events)
            
            elif key.char in ['l', 'L']:
                self.loaded_events = self.reader.load(self.args.file)
                logger.info(f"Loaded {len(self.loaded_events)} items from {self.args.file}")
                logger.info(f"total time: {self.editor.get_total_time(self.loaded_events)}")

            elif key.char in ['p', 'P'] and not self.player.playing and not self.recorder.recording:
                loop_count = self.args.count
                if self.args.time:
                    loop_count = -1  # run until timer / max_duration
                max_duration = _parse_duration(self.args.time) if self.args.time else None
                if max_duration:
                    print(f"Running for {self.args.time} ({max_duration:.0f}s)")
                self.player.play(self.loaded_events, loop_count=loop_count, max_duration=max_duration)
            
            elif key.char in ['e', 'E'] and self.player.playing:
                self.player.stop()

            elif key.char in ['h', 'H']:
                print("\n=== Controls ===")
                print("R: Start Recording")
                print("S: Stop Recording")
                print("T: Scale Timing")
                print("I: Interactive Scale")
                print("P: Playback")
                print("E: Stop Playback")
                print("W: Save to file")
                print("L: Load from file")
                print("H: Show Help")
                print("ESC: Quit")

        except AttributeError:
            pass

    def _on_key_release(self, key):
        if key == keyboard.Key.space and self.recorder.recording:
            self.recorder.on_spacebar(False)
        
        elif key == keyboard.Key.esc:
            self.running = False
            self.recorder.stop()
            self.player.stop()
            return False

if __name__ == '__main__':
    app = AutoClickerApp()
    try:
        app.run()
    except KeyboardInterrupt:
        pass

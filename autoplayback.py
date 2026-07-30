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
from pynput import keyboard, mouse

# --- Event types ---

@dataclass(slots=True)
class MoveEvent:
    x: float
    y: float
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

# --- Classes ---

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Reader:
    def load(self, filename, loaded_files=None):
        """Load mouse events from a text file, supporting nested blocks"""
        if loaded_files is None:
            loaded_files = set()
        
        if filename in loaded_files:
            logger.warning(f"Circular reference detected for file: {filename}")
            return []
        
        loaded_files.add(filename)
        
        try:
            with open(filename, 'r') as f:
                lines = f.readlines()
            events, _ = self._parse_lines(lines, indent_level=0, loaded_files=loaded_files)
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

            else:
                try:
                    event_type, event_data_str, delay_str = line.split('|')
                    delay = float(delay_str)

                    if event_type == 'move':
                        nums = re.findall(r'([-+]?\d*\.?\d+)', event_data_str)
                        events.append(MoveEvent(x=float(nums[0]), y=float(nums[1]), delay=delay))
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
                        nums = list(map(float, re.findall(r'([-+]?\d*\.?\d+)', event_data_str)))
                        # format: (x, y, r, g, b, tolerance, timeout)
                        events.append(WaitPixelEvent(
                            x=int(nums[0]), y=int(nums[1]),
                            r=int(nums[2]), g=int(nums[3]), b=int(nums[4]),
                            tolerance=int(nums[5]), timeout=float(nums[6]),
                            delay=delay,
                        ))
                    elif event_type == 'log':
                        events.append(LogEvent(message=event_data_str.strip(), delay=delay))
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
        indent = ""

        for event in events:
            if isinstance(event, LoopEvent):
                f.write(f"{indent}loop {event.count} {{\n")
                self._write_events(f, event.events, indent_level + 1)
                f.write(f"{indent}}}\n")
            elif isinstance(event, MoveEvent):
                f.write(f"move|({event.x}, {event.y})|{event.delay}\n")
            elif isinstance(event, ClickEvent):
                btn_repr = f"<Button.{event.button.name}: ((0,0,0),0)>"
                f.write(f"click|({event.x}, {event.y}, {btn_repr}, {event.pressed})|{event.delay}\n")
            elif isinstance(event, ScrollEvent):
                f.write(f"scroll|({event.x}, {event.y}, {event.dx}, {event.dy})|{event.delay}\n")
            elif isinstance(event, SpacebarEvent):
                f.write(f"spacebar|{event.pressed}|{event.delay}\n")
            elif isinstance(event, WaitPixelEvent):
                f.write(
                    f"wait_pixel|({event.x}, {event.y}, {event.r}, {event.g}, {event.b}, "
                    f"{event.tolerance}, {event.timeout})|{event.delay}\n"
                )
            elif isinstance(event, LogEvent):
                f.write(f"log|{event.message}|{event.delay}\n")

class Editor:
    def scale_timing(self, events, scale_factor):
        """Scale the timing of all events by a factor (mutates in-place)"""
        for event in events:
            if isinstance(event, LoopEvent):
                self.scale_timing(event.events, scale_factor)
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

    def play(self, events, loop_count=1, dry_run=False):
        if self.playing: return
        print("Starting Playback!")
        self.playing = True
        
        if dry_run:
            logger.info("--- DRY RUN START ---")
            self._dry_run_recursive(events)
            logger.info("--- DRY RUN END ---")
            self.playing = False
            return

        self.thread = threading.Thread(target=self._play_loop, args=(events, loop_count), daemon=True)
        self.thread.start()
        self._start_time = time.time()
        self._iteration = 0
        self._event_count = 0

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
            if current_loop != -1:
                if current_loop == 0:
                    break
                current_loop -= 1

            iter_start = time.time()
            self._execute_recursive(events)
            self._iteration += 1

            elapsed = time.time() - self._start_time
            iter_time = time.time() - iter_start
            rate = self._iteration / elapsed if elapsed > 0 else 0

            if total is not None:
                done = self._iteration
                eta = ((total - done) / rate) if rate > 0 else float('inf')
                eta_str = f"{eta:.0f}s" if eta != float('inf') else "?"
                line = (
                    f"iter={done}/{total}  elapsed={elapsed:.1f}s  "
                    f"iter_time={iter_time:.2f}s  rate={rate:.2f}/s  "
                    f"eta={eta_str}  events={self._event_count}"
                )
            else:
                line = (
                    f"iter={self._iteration}  elapsed={elapsed:.1f}s  "
                    f"iter_time={iter_time:.2f}s  rate={rate:.2f}/s  "
                    f"events={self._event_count}"
                )

            sys.stdout.write(f"\r\033[K{line}")
            sys.stdout.flush()

        self.playing = False

    def _execute_recursive(self, events, level=0):
        if not self.playing: return
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
            elif isinstance(event, LogEvent):
                sys.stdout.write(f"\r\033[K[LOG] {event.message}\n")
                sys.stdout.flush()

            self._event_count += 1
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
        logger.warning(
            f"wait_pixel timed out after {event.timeout}s "
            f"at ({event.x}, {event.y}) target=({event.r},{event.g},{event.b})"
        )

    def _dry_run_recursive(self, events, level=0):
        indent = '  ' * level
        for event in events:
            if isinstance(event, LoopEvent):
                logger.info(f"{indent}Loop {event.count} times {{")
                self._dry_run_recursive(event.events, level + 1)
                logger.info(f"{indent}}}")

# --- Main Application ---

class AutoClickerApp:
    def __init__(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument('-g', '--granularity', type=float, default=0.01, help='Granularity for recording (s)')
        self.parser.add_argument('-c', '--count', type=int, default=1, help='Loop count')
        self.parser.add_argument('-d', '--delay_threshold', type=float, default=0, help='Delay Jitter (s)')
        self.parser.add_argument('-p', '--position_threshold', type=float, default=0, help='Position Jitter (px)')
        self.parser.add_argument('-f', '--file', type=str, default='mouse_events.txt', help='File to load/save')
        self.parser.add_argument('-s', '--scale', type=float, default=1.0, help='Timing scale factor')
        self.parser.add_argument('-o', '--output', type=str, help='Output file (default: overwrites input file)')
        self.parser.add_argument('--dry-run', action='store_true', help='Dry run')
        self.parser.add_argument('--verbose', action='store_true', help='Print each event during playback')
        self.args = self.parser.parse_args()

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
                self.player.play(self.loaded_events, loop_count=self.args.count)
            
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

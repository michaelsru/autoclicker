#!/usr/bin/env python

import time
import threading
import random
import argparse
import re
from pynput import keyboard, mouse

# --- Classes ---

class Reader:
    def load(self, filename, loaded_files=None):
        """Load mouse events from a text file, supporting nested blocks"""
        if loaded_files is None:
            loaded_files = set()
        
        if filename in loaded_files:
            print(f"Warning: Circular reference detected for file: {filename}")
            return []
        
        loaded_files.add(filename)
        
        try:
            with open(filename, 'r') as f:
                lines = f.readlines()
            events, _ = self._parse_lines(lines, indent_level=0, loaded_files=loaded_files)
            return events
        except FileNotFoundError:
            print(f"File not found: {filename}")
            return []
        except Exception as e:
            print(f"Error loading file {filename}: {e}")
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
                print(f"{'  '*indent_level}Loading events from file: {target_file}")
                # Recursively load using the same loaded_files set
                imported_events = self.load(target_file, loaded_files) 
                events.extend(imported_events)
                
            elif line.startswith('loop '):
                parts = line[5:].strip().split(' ', 1)
                if len(parts) >= 1:
                    loop_count = int(parts[0])
                    rest = parts[1].strip() if len(parts) > 1 else ""
                    
                    if rest == '{':
                        print(f"{'  '*indent_level}Parsing loop block: {loop_count} times")
                        block_events, new_i = self._parse_lines(lines, i, indent_level + 1, loaded_files)
                        i = new_i
                        events.append({'type': 'loop', 'count': loop_count, 'events': block_events})
                    elif rest: 
                        # Legacy: loop <count> <filename>
                        target_file = rest
                        print(f"{'  '*indent_level}Looping explicitly from file: {loop_count} times: {target_file}")
                        file_events = self.load(target_file, loaded_files)
                        events.append({'type': 'loop', 'count': loop_count, 'events': file_events})
                    else: 
                         print(f"Syntax error on loop command: {line}")

            else:
                try:
                    event_type, event_data_str, delay = line.split('|')
                    event = {'type': event_type, 'delay': float(delay)}
                    
                    if event_type == 'move':
                        position = tuple(map(float, re.findall(r'([-+]?\d*\.?\d+)', event_data_str)))
                        event['data'] = position
                    elif event_type == 'click':
                        match = re.match(r"\(([-+]?\d*\.\d+|\d+), ([-+]?\d*\.\d+|\d+), <Button\.(\w+): .+>, (True|False)\)", event_data_str)
                        if match:
                            x, y, button_name, pressed_str = match.groups()
                            x, y = float(x), float(y)
                            button = getattr(mouse.Button, button_name)
                            pressed = pressed_str == 'True'
                            event['data'] = (x, y, button, pressed)
                    elif event_type == 'scroll':
                        x, y, dx, dy = map(float, re.findall(r'([-+]?\d*\.?\d+)', event_data_str))
                        event['data'] = (x, y, dx, dy)
                    elif event_type == 'spacebar':
                        event['data'] = event_data_str == 'True'
                    
                    events.append(event)
                except Exception as e:
                    print(f"Error parsing line: {line} - {e}")
                    continue
                    
        return events, i

class Writer:
    def save(self, filename, events):
        """Save mouse events to a text file"""
        try:
            with open(filename, 'w') as f:
                self._write_events(f, events)
            print(f"Events saved to {filename}")
        except Exception as e:
            print(f"Error saving file {filename}: {e}")

    def _write_events(self, f, events, indent_level=0):
        indent = "" # We don't indent standard events to keep compatibility, but we could
        
        for event in events:
            event_type = event['type']
            
            if event_type == 'loop':
                count = event['count']
                sub_events = event['events']
                f.write(f"{indent}loop {count} {{\n")
                self._write_events(f, sub_events, indent_level + 1)
                f.write(f"{indent}}}\n")
                continue

            # Standard events
            delay = event['delay']
            event_data = event.get('data')

            if event_type == 'move':
                f.write(f"{event_type}|{event_data}|{delay}\n")
            elif event_type == 'click':
                x, y, button, pressed = event_data
                # Reconstruct string format: <Button.left: ((1, 2, 6), 0)>
                # This is a bit hacky to match the exact string format Pynput produces/parser expects
                button_str = str(button) # e.g. Button.left
                # Logic to approximate the internal representation output if needed, 
                # or just rely on the parser being flexible. 
                # The regex expects: <Button.(\w+): .+>
                # Let's reconstruct a valid string for the regex:
                btn_name = button.name
                dummy_internal = "((0,0,0),0)" # Placeholder
                btn_repr = f"<Button.{btn_name}: {dummy_internal}>"
                f.write(f"{event_type}|({x}, {y}, {btn_repr}, {pressed})|{delay}\n")
            elif event_type == 'scroll':
                x, y, dx, dy = event_data
                f.write(f"{event_type}|({x}, {y}, {dx}, {dy})|{delay}\n")
            elif event_type == 'spacebar':
                f.write(f"{event_type}|{event_data}|{delay}\n")

class Editor:
    def scale_timing(self, events, scale_factor):
        """Scale the timing of all events by a factor"""
        scaled_events = []
        for event in events:
            new_event = event.copy() # Shallow copy
            if event['type'] == 'loop':
                # Recursive scale
                new_event['events'] = self.scale_timing(event['events'], scale_factor)
            else:
                new_event['delay'] = float(event['delay']) * scale_factor
            scaled_events.append(new_event)
        return scaled_events

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
        
        # Initial position
        with self.lock:
            self.mouse_events.append({'type': 'move', 'data': self.mouse_controller.position, 'delay': 0})
        
        # Start mouse polling thread for movement
        self.thread = threading.Thread(target=self._record_loop)
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
            # Verify we have at least one event
            with self.lock:
                last_pos = self.mouse_events[-1]['data'] if self.mouse_events and self.mouse_events[-1]['type'] == 'move' else None
            
            if position != last_pos:
                current_time = time.time()
                delay = current_time - self.last_event_time
                print(f"{current_time - self.start_time:.2f} [{delay:.2f}]: Recording mouse position: {position}")
                with self.lock:
                    self.mouse_events.append({'type': 'move', 'data': position, 'delay': delay})
                self.last_event_time = current_time
            time.sleep(self.granularity)

    def _on_click(self, x, y, button, pressed):
        if self.recording:
            current_time = time.time()
            delay = current_time - self.last_event_time
            print(f"Recording click at {x}, {y} {button} {pressed}")
            with self.lock:
                self.mouse_events.append({'type': 'click', 'data': (x, y, button, pressed), 'delay': delay})
            self.last_event_time = current_time

    def _on_scroll(self, x, y, dx, dy):
        if self.recording:
            current_time = time.time()
            delay = current_time - self.last_event_time
            print(f"Recording scroll {dx}, {dy}")
            with self.lock:
                self.mouse_events.append({'type': 'scroll', 'data': (x, y, dx, dy), 'delay': delay})
            self.last_event_time = current_time

    def on_spacebar(self, pressed):
        if self.recording:
            current_time = time.time()
            delay = current_time - self.last_event_time
            print(f"Recording spacebar {'press' if pressed else 'release'}")
            with self.lock:
                self.mouse_events.append({'type': 'spacebar', 'data': pressed, 'delay': delay})
            self.last_event_time = current_time

    def get_events(self):
        with self.lock:
            return list(self.mouse_events)

class Player:
    def __init__(self, controller_mouse, controller_keyboard, position_threshold=5.0, delay_threshold=0.05):
        self.mouse = controller_mouse
        self.keyboard = controller_keyboard
        self.position_threshold = position_threshold
        self.delay_threshold = delay_threshold
        self.playing = False
        self.thread = None

    def play(self, events, loop_count=1, dry_run=False):
        if self.playing: return
        print("Starting Playback!")
        self.playing = True
        
        if dry_run:
            print("--- DRY RUN START ---")
            self._dry_run_recursive(events)
            print("--- DRY RUN END ---")
            self.playing = False
            return

        self.thread = threading.Thread(target=self._play_loop, args=(events, loop_count))
        self.thread.start()

    def stop(self):
        if not self.playing: return
        self.playing = False
        if self.thread:
            self.thread.join()
        print("Playback stopped!")

    def _play_loop(self, events, loop_count):
        current_loop = loop_count
        while self.playing:
            if current_loop != -1:
                if current_loop == 0:
                    break
                current_loop -= 1
            
            self._execute_recursive(events)
        self.playing = False

    def _execute_recursive(self, events, level=0):
        if not self.playing: return

        for event in events:
            if not self.playing: break
            
            event_type = event['type']
            
            if event_type == 'loop':
                count = event['count']
                sub_events = event['events']
                print(f"{'  '*level}Looping {count} times...")
                for _ in range(count):
                    if not self.playing: break
                    self._execute_recursive(sub_events, level + 1)
                continue

            event_data = event.get('data')
            delay = event['delay']
            
            print(f"{'  '*level}Playing: {event_type}, delay: {delay}")
            
            if event_type == 'move':
                position = event_data
                dx = random.uniform(-self.position_threshold, self.position_threshold)
                dy = random.uniform(-self.position_threshold, self.position_threshold)
                self.mouse.position = (position[0] + dx, position[1] + dy)
            
            elif event_type == 'click':
                x, y, button, pressed = event_data
                if pressed: self.mouse.press(button)
                else: self.mouse.release(button)
                
            elif event_type == 'scroll':
                x, y, dx, dy = event_data
                self.mouse.scroll(dx, dy)
                
            elif event_type == 'spacebar':
                if event_data: self.keyboard.press(keyboard.Key.space)
                else: self.keyboard.release(keyboard.Key.space)
            
            # Delay handling
            rand_delay = random.uniform(-self.delay_threshold, self.delay_threshold)
            actual_delay = delay * (1 + rand_delay)
            if actual_delay < 0: actual_delay = 0
            time.sleep(actual_delay)

    def _dry_run_recursive(self, events, level=0):
        indent = '  ' * level
        for event in events:
            if event['type'] == 'loop':
                print(f"{indent}Loop {event['count']} times {{")
                self._dry_run_recursive(event['events'], level + 1)
                print(f"{indent}}}")
            else:
                print(f"{indent}{event['type']} | delay={event['delay']}")

# --- Main Application ---

class AutoClickerApp:
    def __init__(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument('-g', '--granularity', type=float, default=0.01, help='Granularity for recording (s)')
        self.parser.add_argument('-c', '--count', type=int, default=1, help='Loop count')
        self.parser.add_argument('-d', '--delay_threshold', type=float, default=0.05, help='Delay Jitter (s)')
        self.parser.add_argument('-p', '--position_threshold', type=float, default=5.0, help='Position Jitter (px)')
        self.parser.add_argument('-f', '--file', type=str, default='mouse_events.txt', help='File to load/save')
        self.parser.add_argument('--dry-run', action='store_true', help='Dry run')
        self.args = self.parser.parse_args()

        self.reader = Reader()
        self.writer = Writer()
        self.editor = Editor()
        self.recorder = Recorder(granularity=self.args.granularity)
        self.player = Player(
            mouse.Controller(), 
            keyboard.Controller(), 
            position_threshold=self.args.position_threshold,
            delay_threshold=self.args.delay_threshold
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
            listener.join()

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
            
            elif key.char in ['w', 'W']:
                self.writer.save(self.args.file, self.loaded_events)
            
            elif key.char in ['l', 'L']:
                self.loaded_events = self.reader.load(self.args.file)
                print(f"Loaded {len(self.loaded_events)} items from {self.args.file}")

            elif key.char in ['p', 'P'] and not self.player.playing and not self.recorder.recording:
                self.player.play(self.loaded_events, loop_count=self.args.count)
            
            elif key.char in ['e', 'E'] and self.player.playing:
                self.player.stop()

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
    app.run()

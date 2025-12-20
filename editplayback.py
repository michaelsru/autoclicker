#!/usr/bin/env python

import time
import threading
import random
import argparse
import re
from pynput import keyboard, mouse

parser = argparse.ArgumentParser(description='Edit mouse event playback files')
parser.add_argument('-f', '--file', type=str, default='mouse_events.txt', help='Mouse events file to edit')
parser.add_argument('-s', '--scale', type=float, default=1.0, help='Timing scale factor (1.0 = normal, 0.5 = 2x faster, 2.0 = 2x slower)')
parser.add_argument('-o', '--output', type=str, help='Output file (default: overwrites input file)')
args = parser.parse_args()

# Create instances of the mouse and keyboard controllers
mouseController = mouse.Controller()
keyboardController = keyboard.Controller()

# Initialize variables
mouse_events = []
mouse_events_lock = threading.Lock()
running = True
playing_back = False
playback_thread = None
delay_threshold = 0.05
position_threshold = 0.0015

# Function to parse and load events from a file
def load_events_from_file(filename, loaded_files=None):
    """Load mouse events from a text file, supporting both direct events and command syntax"""
    if loaded_files is None:
        loaded_files = set()
    
    # Prevent infinite recursion
    if filename in loaded_files:
        print(f"Warning: Circular reference detected for file: {filename}")
        return []
    
    loaded_files.add(filename)
    events = []
    try:
        with open(filename, 'r') as f:
            for line in f.readlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Check if it's a command line
                if line.startswith('run '):
                    # Load events from another file
                    target_file = line[4:].strip()
                    print(f"Loading events from file: {target_file}")
                    events.extend(load_events_from_file(target_file, loaded_files.copy()))
                elif line.startswith('loop '):
                    # Parse loop command: loop <count> <filename>
                    parts = line[5:].strip().split(' ', 1)
                    if len(parts) == 2:
                        loop_count = int(parts[0])
                        target_file = parts[1].strip()
                        print(f"Looping {loop_count} times: {target_file}")
                        loop_events = load_events_from_file(target_file, loaded_files.copy())
                        for _ in range(loop_count):
                            events.extend(loop_events)
                else:
                    # Parse regular event line
                    try:
                        event_type, event_data, delay = line.split('|')
                        if event_type == 'move':
                            position = tuple(map(float, re.findall(r'([-+]?\d*\.?\d+)', event_data)))
                            event_data = position
                        elif event_type == 'click':
                            match = re.match(r"\(([-+]?\d*\.\d+|\d+), ([-+]?\d*\.\d+|\d+), <Button\.(\w+): .+>, (True|False)\)", event_data)
                            if match:
                                x, y, button, pressed = match.groups()
                                x, y = float(x), float(y)
                                button = getattr(mouse.Button, button)
                                pressed = pressed == 'True'
                                event_data = (x, y, button, pressed)
                        elif event_type == 'scroll':
                            x, y, dx, dy = map(float, re.findall(r'([-+]?\d*\.?\d+)', event_data))
                            event_data = (x, y, dx, dy)
                        elif event_type == 'spacebar':
                            event_data = event_data == 'True'
                        
                        events.append((event_type, event_data, float(delay)))
                    except Exception as e:
                        print(f"Error parsing line: {line} - {e}")
                        continue
    except FileNotFoundError:
        print(f"File not found: {filename}")
    except Exception as e:
        print(f"Error loading file {filename}: {e}")
    
    return events

# Function to save events to file
def save_events_to_file(filename, events):
    """Save mouse events to a text file"""
    try:
        with open(filename, 'w') as f:
            for event in events:
                event_type, event_data, delay = event
                if event_type == 'move':
                    f.write(f"{event_type}|{event_data}|{delay}\n")
                elif event_type == 'click':
                    x, y, button, pressed = event_data
                    # <Button.left: ((1, 2, 6), 0)>, True)
                    # click|(1113.30078125, 896.0390625, Button.left, False)|0.09698886871337892

                    f.write(f"{event_type}|({x}, {y}, <{button}: ((1, 2, 6), 0)>, {pressed})|{delay}\n")
                elif event_type == 'scroll':
                    x, y, dx, dy = event_data
                    f.write(f"{event_type}|({x}, {y}, {dx}, {dy})|{delay}\n")
                elif event_type == 'spacebar':
                    f.write(f"{event_type}|{event_data}|{delay}\n")
        print(f"Events saved to {filename}")
    except Exception as e:
        print(f"Error saving file {filename}: {e}")

# Function to scale timing of events
def scale_timing(events, scale_factor):
    """Scale the timing of all events by a factor"""
    scaled_events = []
    for event_type, event_data, delay in events:
        new_delay = delay * scale_factor
        scaled_events.append((event_type, event_data, new_delay))
    return scaled_events

# Function to play back mouse activity
def play_back_mouse_activity():
    global playing_back, mouse_events, delay_threshold, position_threshold
    print(f'Playing back {len(mouse_events)} mouse events...')
    for event in mouse_events:
        if not playing_back:
            break
        event_type, event_data, delay = event
        print(f"Playing back: {event_type}, delay: {delay:.3f}s")
        if event_type == 'move':
            position = event_data
            rand_position_scaler = random.uniform(-position_threshold, position_threshold)
            modified_position = (position[0] * (1 + rand_position_scaler), position[1] * (1 + rand_position_scaler))
            mouseController.position = modified_position
        elif event_type == 'click':
            x, y, button, pressed = event_data
            if pressed:
                mouseController.press(button)
            else:
                mouseController.release(button)
        elif event_type == 'scroll':
            x, y, dx, dy = event_data
            mouseController.scroll(dx, dy)
        elif event_type == 'spacebar':
            if event_data:  # True for press, False for release
                keyboardController.press(keyboard.Key.space)
            else:
                keyboardController.release(keyboard.Key.space)
        
        rand_delay_scaler = random.uniform(-delay_threshold, delay_threshold)
        delay *= (1 + rand_delay_scaler)
        time.sleep(delay)
    
    playing_back = False
    print("Playback completed!")

# Function to handle key presses
def on_press(key):
    global playing_back, playback_thread, mouse_events, running
    
    try:
        if hasattr(key, 'char'):
            if key.char in ['l', 'L']:
                print(f"Loading events from {args.file}...")
                with mouse_events_lock:
                    mouse_events = load_events_from_file(args.file)
                total_time = sum(event[2] for event in mouse_events)
                print(f"Loaded {len(mouse_events)} events")
                print(f"Total time: {total_time:.2f} seconds")
                
            elif key.char in ['s', 'S']:
                # Scale timing
                scale_factor = args.scale
                print(f"Scaling timing by factor: {scale_factor}")
                with mouse_events_lock:
                    mouse_events = scale_timing(mouse_events, scale_factor)
                total_time = sum(event[2] for event in mouse_events)
                print(f"Scaled timing complete. New total time: {total_time:.2f} seconds")
                
            elif key.char in ['w', 'W']:
                # Save events
                output_file = args.output if args.output else args.file
                print(f"Saving events to {output_file}...")
                with mouse_events_lock:
                    save_events_to_file(output_file, mouse_events)
                    
            elif key.char in ['p', 'P'] and not playing_back:
                print("Starting Playback!")
                playing_back = True
                playback_thread = threading.Thread(target=play_back_mouse_activity)
                playback_thread.start()
                
            elif key.char in ['e', 'E'] and playing_back:
                playing_back = False
                if playback_thread:
                    playback_thread.join()
                print("Playback stopped!")
                
            elif key.char in ['i', 'I']:
                # Interactive scale input
                try:
                    scale_input = input("Enter timing scale factor (e.g., 0.5 for 2x faster, 2.0 for 2x slower): ")
                    scale_factor = float(scale_input)
                    print(f"Scaling timing by factor: {scale_factor}")
                    with mouse_events_lock:
                        mouse_events = scale_timing(mouse_events, scale_factor)
                    total_time = sum(event[2] for event in mouse_events)
                    print(f"Scaled timing complete. New total time: {total_time:.2f} seconds")
                except ValueError:
                    print("Invalid scale factor. Please enter a number.")
                except KeyboardInterrupt:
                    print("Scale input cancelled.")
                    
            elif key.char in ['h', 'H']:
                # Show help
                print("\n=== Edit Playback Controls ===")
                print("L - Load events from file")
                print("S - Scale timing by command line factor")
                print("I - Interactive timing scale")
                print("W - Save events to file")
                print("P - Play back events")
                print("E - Stop playback")
                print("H - Show this help")
                print("ESC - Exit program")
                print(f"\nCurrent settings:")
                print(f"  Input file: {args.file}")
                print(f"  Scale factor: {args.scale}")
                print(f"  Output file: {args.output if args.output else args.file}")
                
    except AttributeError:
        pass

# Function to handle key releases (to exit the program)
def on_release(key):
    global running, playing_back, playback_thread
    
    if key == keyboard.Key.esc:
        # Stop listener and cleanup
        running = False
        playing_back = False
        
        # Clean up threads
        if playback_thread and playback_thread.is_alive():
            playback_thread.join()
        
        # Stop listeners
        keyboardListener.stop()
        
        return False

# Print startup information
print(f"Edit Playback started!")
print(f"  Input file: {args.file}")
print(f"  Scale factor: {args.scale}")
print(f"  Output file: {args.output if args.output else args.file}")
print(f"\nPress 'H' for help")

# Create a keyboard listener
keyboardListener = keyboard.Listener(on_press=on_press, on_release=on_release)
keyboardListener.start()

while running:
    time.sleep(0.1)


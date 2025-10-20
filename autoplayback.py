#!/usr/bin/env python

import time
import threading
import random
import argparse
import re
from pynput import keyboard, mouse

parser = argparse.ArgumentParser()
parser.add_argument('-g', '--granularity', type=float, default=0.01, help='Granularity between mouse events in seconds')
parser.add_argument('-c', '--count', type=int, default=-1, help='Number of loops to playback')
# add delay threshold
parser.add_argument('-d', '--delay_threshold', type=float, default=0.05, help='Delay threshold in seconds')
# add position threshold
parser.add_argument('-p', '--position_threshold', type=float, default=0.0015, help='Position threshold in percentage')
args = parser.parse_args()

granularity = args.granularity
count = args.count
delay_threshold = args.delay_threshold
position_threshold = args.position_threshold

# Create instances of the mouse and keyboard controllers
mouseController = mouse.Controller()
keyboardController = keyboard.Controller()

# Initialize variables
recording = False
playing_back = False
running = True
mouse_events = []
playback_thread = None
mouse_events_lock = threading.Lock()
start_time = 0

# Function to record mouse activity
def record_mouse_activity():
    global recording, mouse_events, start_time, last_event_time
    start_time = time.time()
    mouse_events = []
    last_event_time = start_time
    with mouse_events_lock:
        mouse_events.append(('move', mouseController.position, 0))
    while recording:
        position = mouseController.position
        if position != mouse_events[-1][1]:
            current_time = time.time()
            differential_seconds = current_time - last_event_time
            print(f"{current_time - start_time:.2f} [{differential_seconds:.2f}]: Recording mouse position: {position}")
            with mouse_events_lock:
                mouse_events.append(('move', position, differential_seconds))
            last_event_time = current_time
        time.sleep(granularity)

# Function to handle mouse clicks
def on_click(x, y, button, pressed):
    global last_event_time
    if recording:
        event_type = 'click'
        current_time = time.time()
        differential_seconds = current_time - last_event_time
        print(f"Recording mouse click at {x}, {y} with button {button} and pressed state {pressed}")
        with mouse_events_lock:
            mouse_events.append((event_type, (x, y, button, pressed), differential_seconds))
        last_event_time = current_time

def on_scroll(x, y, dx, dy):
    global last_event_time
    if recording:
        current_time = time.time()
        differential_seconds = current_time - last_event_time
        print(f"Recording mouse scroll at {x}, {y} with dx={dx}, dy={dy}")
        with mouse_events_lock:
            mouse_events.append(('scroll', (x, y, dx, dy), differential_seconds))
        last_event_time = current_time

# Function to parse and load events from a file
def load_events_from_file(filename):
    """Load mouse events from a text file, supporting both direct events and command syntax"""
    events = []
    try:
        with open(filename, 'r') as f:
            for line in f.readlines():
                line = line.strip()
                if not line:
                    continue
                
                # Check if it's a command line
                if line.startswith('run '):
                    # Load events from another file
                    target_file = line[4:].strip()
                    print(f"Loading events from file: {target_file}")
                    events.extend(load_events_from_file(target_file))
                elif line.startswith('loop '):
                    # Parse loop command: loop <count> <filename>
                    parts = line[5:].strip().split(' ', 1)
                    if len(parts) == 2:
                        loop_count = int(parts[0])
                        target_file = parts[1].strip()
                        print(f"Looping {loop_count} times: {target_file}")
                        loop_events = load_events_from_file(target_file)
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

# Function to play back mouse activity
def play_back_mouse_activity():
    global playing_back, count, mouse_events, delay_threshold, position_threshold
    print(f'mouse events: {mouse_events}')
    while playing_back:
        if count != -1:
            if count == 0:
                playing_back = False
                break
            count -= 1
        for event in mouse_events:
            if not playing_back:
                break
            event_type, event_data, delay = event
            print(f"Playing back mouse activity: {event_type}, {event_data}, delay: {delay}")
            if event_type == 'move':
                position = event_data
                rand_position_scaler = random.uniform(-position_threshold, position_threshold)
                modified_position = (position[0] * (1 + rand_position_scaler), position[1] * (1 + rand_position_scaler))
                mouseController.position = modified_position
                print(f"Moving mouse to {position}")
            elif event_type == 'click':
                x, y, button, pressed = event_data
                print(f"Clicking mouse at {x}, {y} with button {button} and pressed state {pressed}")
                if pressed:
                    mouseController.press(button)
                else:
                    mouseController.release(button)
            elif event_type == 'scroll':
                x, y, dx, dy = event_data
                print(f"Scrolling mouse at {x}, {y} with dx={dx}, dy={dy}")
                mouseController.scroll(dx, dy)
            elif event_type == 'spacebar':
                if event_data:  # True for press, False for release
                    print("Pressing spacebar")
                    keyboardController.press(keyboard.Key.space)
                else:
                    print("Releasing spacebar")
                    keyboardController.release(keyboard.Key.space)
            rand_delay_scaler = random.uniform(-delay_threshold, delay_threshold)
            print(f"Random delay scaler: {rand_delay_scaler}")
            delay *= (1 + rand_delay_scaler)
            time.sleep(delay)

# Function to handle key presses
def on_press(key):
    global recording, playing_back, playback_thread, count, mouse_events, last_event_time

    try:
        if key == keyboard.Key.space and recording:
            current_time = time.time()
            differential_seconds = current_time - last_event_time
            print(f"Recording spacebar press")
            with mouse_events_lock:
                mouse_events.append(('spacebar', True, differential_seconds))
            last_event_time = current_time
        elif hasattr(key, 'char'):
            if key.char in ['r', 'R'] and not recording and not playing_back:
                print("Starting Recording!")
                recording = True
                threading.Thread(target=record_mouse_activity).start()
            elif key.char in ['s', 'S'] and recording:
                recording = False
                print("Recording stopped!")
            # save recording to file
            elif key.char in ['w', 'W']:
                print("Saving recording to mouse_events.txt...")
                with open('mouse_events.txt', 'w') as f:
                    for event in mouse_events:
                        f.write(f"{event[0]}|{event[1]}|{event[2]}\n")
                print("Recording saved to mouse_events.txt!")
            # load recording from file
            elif key.char in ['l', 'L']:
                print("Loading recording from mouse_events.txt...")
                with mouse_events_lock:
                    mouse_events = load_events_from_file('mouse_events.txt')
                total_time = sum(event[2] for event in mouse_events)
                print("Recording loaded from mouse_events.txt!")
                print(f"Total recording time: {total_time} seconds")
            elif key.char in ['p', 'P'] and not playing_back and not recording:
                print("Starting Playback!")
                playing_back = True
                count = args.count
                playback_thread = threading.Thread(target=play_back_mouse_activity)
                playback_thread.start()
            elif key.char in ['e', 'E'] and playing_back:
                playing_back = False
                if playback_thread:
                    playback_thread.join()
                print("Playback stopped!")
    except AttributeError:
        pass

# Function to handle key releases (to exit the program)
def on_release(key):
    global running, recording, playing_back, last_event_time

    if key == keyboard.Key.space and recording:
        current_time = time.time()
        differential_seconds = current_time - last_event_time
        print(f"Recording spacebar release")
        with mouse_events_lock:
            mouse_events.append(('spacebar', False, differential_seconds))
        last_event_time = current_time
    elif key == keyboard.Key.esc:
        # Stop listener
        running = False
        recording = False
        playing_back = False
        return False

# Create a mouse listener
mouseListener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
mouseListener.start()

# Create a keyboard listener
keyboardListener = keyboard.Listener(on_press=on_press, on_release=on_release)
keyboardListener.start()

while running:
    time.sleep(0.1)

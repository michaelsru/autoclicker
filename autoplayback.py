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
args = parser.parse_args()

granularity = args.granularity
count = args.count

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

# Function to play back mouse activity
def play_back_mouse_activity():
    global playing_back, count, mouse_events
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
                mouseController.position = position
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
                with open('mouse_events.txt', 'r') as f:
                    mouse_events = []
                    total_time = 0
                    for line in f.readlines():
                        print(f'line: {line}')
                        event_type, event_data, delay = line.strip().split('|')
                        if event_type == 'move':
                            position = tuple(map(float, re.findall(r'([-+]?\d*\.?\d+)', event_data)))
                            event_data = position
                        elif event_type == 'click':
                            x, y, button, pressed = re.match(r"\(([-+]?\d*\.\d+|\d+), ([-+]?\d*\.\d+|\d+), <Button\.(\w+): .+>, (True|False)\)", event_data).groups()
                            x, y = float(x), float(y)
                            button = getattr(mouse.Button, button)
                            pressed = pressed == 'True'
                            event_data = (x, y, button, pressed)
                        elif event_type == 'scroll':
                            x, y, dx, dy = map(float, re.findall(r'([-+]?\d*\.?\d+)', event_data))
                            event_data = (x, y, dx, dy)
                        elif event_type == 'spacebar':
                            event_data = event_data == 'True'
                        with mouse_events_lock:
                            mouse_events.append((event_type, event_data, float(delay)))
                        print(f"Loading mouse event: {event_type}, {event_data}, delay: {delay}")
                        total_time += float(delay)
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

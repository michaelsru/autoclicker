#!/usr/bin/env python

import time
import threading
import random
import argparse
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
    global recording, mouse_events, start_time
    start_time = time.time()
    mouse_events = []
    with mouse_events_lock:
        mouse_events.append(('move', mouseController.position, 0))
    while recording:
        position = mouseController.position
        # record only if mouse position changes
        if position != mouse_events[-1][1]:
        # if position != mouse_events[-1][1]:
            offset_time = time.time() - start_time
            last_event_time = mouse_events[-1][2]
            print(f'last event time: {last_event_time}')
            differential_seconds = offset_time - last_event_time
            print(f"{offset_time:.2f} [{differential_seconds:.2f}]: Recording mouse position: {position}")
            with mouse_events_lock:
                mouse_events.append(('move', position, offset_time))
        time.sleep(granularity)  # Record mouse position every 0.01 seconds

# Function to handle mouse clicks
def on_click(x, y, button, pressed):
    if recording:
        event_type = 'click'
        print(f"Recording mouse click at {x}, {y} with button {button} and pressed state {pressed}")
        offset_time = time.time() - start_time
        with mouse_events_lock:
            mouse_events.append((event_type, (x, y, button, pressed), offset_time))

# Function to play back mouse activity
def play_back_mouse_activity():
    global playing_back, count
    start_time = time.time()
    print(mouse_events)
    while playing_back:
        if count != -1:
            if count == 0:
                playing_back = False
                break
            count -= 1
        # for event in mouse_events:
        i = 0
        while i < len(mouse_events)-1 and playing_back:
            event_type, event_data, offset_time = mouse_events[i]
            print(f"Playing back mouse activity: {event_type}, {event_data}, {offset_time}")
            differential_seconds = mouse_events[i+1][2] - offset_time
            print(f"offset time: {offset_time}, delay: {differential_seconds}")
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
            time.sleep(differential_seconds)
            i += 1

# Function to handle key presses
def on_press(key):
    global recording, playing_back, playback_thread, count

    try:
        if hasattr(key, 'char'):
            if key.char in ['r', 'R'] and not recording and not playing_back:
                print("Starting Recording!")
                recording = True
                threading.Thread(target=record_mouse_activity).start()
            elif key.char in ['s', 'S'] and recording:
                recording = False
                print("Recording stopped!")
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
    global running, recording, playing_back
    if key == keyboard.Key.esc:
        # Stop listener
        running = False
        recording = False
        playing_back = False
        return False

# Create a mouse listener
mouseListener = mouse.Listener(on_click=on_click)
mouseListener.start()

# Create a keyboard listener
keyboardListener = keyboard.Listener(on_press=on_press, on_release=on_release)
keyboardListener.start()

while running:
    time.sleep(0.1)

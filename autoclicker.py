#!/usr/bin/env python

import time
import threading
import random
import argparse
from pynput import keyboard, mouse

# Parse command line arguments
parser = argparse.ArgumentParser(description='Auto-clicker with configurable duration')
parser.add_argument('-d', '--duration', type=float, default=-1, help='Duration to click in seconds (-1 for infinite)')
parser.add_argument('-i', '--interval', type=float, default=10.07, help='Click interval in seconds')
parser.add_argument('-v', '--variation', type=float, default=0.001, help='Click interval variation')
args = parser.parse_args()

# Set the interval (in seconds) between clicks
click_interval = args.interval
click_interval_variation = args.variation
duration = args.duration

# Create an instance of the mouse Controller
mouseController = mouse.Controller()

# Flag to control the clicking
clicking = False
running = True
click_start_time = 0

# Function to start clicking
def start_clicking():
    global clicking, click_start_time
    click_start_time = time.time()
    while clicking:
        # Check if duration limit has been reached
        if duration > 0 and (time.time() - click_start_time) >= duration:
            print(f"Duration limit reached ({duration} seconds). Stopping clicking.")
            clicking = False
            break
        
        # Press and release the left mouse button
        mouseController.press(mouse.Button.left)
        mouseController.release(mouse.Button.left)
        print("Click!")
        actual_interval = click_interval + (click_interval_variation * (2 * random.random() - 1))
        print(actual_interval)
        time.sleep(actual_interval)


# Function to start dragging
def start_dragging():
    global clicking, click_start_time
    click_start_time = time.time()
    while clicking:
        # Check if duration limit has been reached
        if duration > 0 and (time.time() - click_start_time) >= duration:
            print(f"Duration limit reached ({duration} seconds). Stopping dragging.")
            clicking = False
            break
        
        # Press and release the left mouse button
        mouseController.press(mouse.Button.left)
        time.sleep(0.07)
        mouseController.release(mouse.Button.left)
        print("Click!")
        actual_interval = click_interval + (click_interval_variation * (2 * random.random() - 1))
        print(actual_interval)
        time.sleep(actual_interval)

# Function to handle key presses
def on_press(key):
    global clicking
    try:
        if hasattr(key, 'char'):
            if key.char in ['['] and not clicking:
                print("Starting Clicking!")
                clicking = True
                threading.Thread(target=start_clicking).start()
            elif key.char in ['\\'] and not clicking:
                print("Starting Dragging!")
                clicking = True
                threading.Thread(target=start_dragging).start()
            elif key.char in [']'] and clicking:
                clicking = False
                print("Clicking stopped!")
    except AttributeError:
        pass

# Function to handle key releases (to exit the program)
def on_release(key):
    global running
    global clicking
    if key == keyboard.Key.esc:
        # Stop listener
        running = False
        clicking = False
        return False


# Print current settings
print(f"Auto-clicker started with settings:")
print(f"  Click interval: {click_interval} seconds")
print(f"  Interval variation: {click_interval_variation} seconds")
print(f"  Duration: {'Infinite' if duration < 0 else f'{duration} seconds'}")
print(f"\nControls:")
print(f"  [ - Start clicking")
print(f"  \\ - Start dragging")
print(f"  ] - Stop clicking/dragging")
print(f"  ESC - Exit program")

# Create a keyboard listener
keyboardListener = keyboard.Listener(on_press=on_press, on_release=on_release)
keyboardListener.start()

while running:
    time.sleep(0.1)
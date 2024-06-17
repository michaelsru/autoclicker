#!/usr/bin/env python

import time
import threading
import random
from pynput import keyboard, mouse

# Create an instance of the mouse Controller
mouseController = mouse.Controller()

# Set the interval (in seconds) between clicks
click_interval = 0.8
click_interval_variation = 0.1

# Flag to control the clicking
clicking = False
running = True

# Function to start clicking
def start_clicking():
    while clicking:
            # Press and release the left mouse button
            mouseController.press(mouse.Button.left)
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
            if key.char in ['s', 'S']:
                clicking = True
                print("Starting Clicking!")
                threading.Thread(target=start_clicking).start()
            elif key.char in ['e', 'E']:
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


# Create a keyboard listener
keyboardListener = keyboard.Listener(on_press=on_press, on_release=on_release)
keyboardListener.start()

while running:
    time.sleep(0.1)
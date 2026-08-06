
from PIL import ImageGrab
full = ImageGrab.grab().getpixel((100, 100))
bbox = ImageGrab.grab(bbox=(100, 100, 101, 101)).getpixel((0, 0))
print('full:', full[:3])
print('bbox:', bbox[:3])
print('match:', full[:3] == bbox[:3])
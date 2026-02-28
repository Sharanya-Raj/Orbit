import pyautogui
import time

#For debugging cursor position

print("Tracking cursor position... Press Ctrl+C to stop.\n")

try:
    while True:
        x, y = pyautogui.position()
        print(f"\rX: {x:<6} Y: {y:<6}", end="", flush=True)
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nStopped.")
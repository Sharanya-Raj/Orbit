import pyautogui
import io
import base64
import subprocess
import time
import pygetwindow as gw

# Disable pyautogui fail-safe (moving mouse to corner stops script)
pyautogui.FAILSAFE = False

def open_app(name: str):
    """Opens an application by navigating to it if already open, else uses the start menu."""
    try:
        windows = gw.getAllWindows()
        for w in windows:
            if w.title and name.lower() in w.title.lower():
                try:
                    w.activate()
                    time.sleep(0.5)
                    return
                except Exception:
                    pass
    except Exception as e:
        print(f"Error checking open windows: {e}")

    # Original method of pressing windows key, typing in the name and continuing
    press_win_key()
    time.sleep(0.5)
    type_text(name)
    time.sleep(0.5)
    press_single_key('enter')

def press_shortcut(*keys):
    """Presses a combination of keys."""
    pyautogui.hotkey(*keys)

def move_and_click(x: int, y: int):
    """
    Moves the mouse to specific coordinates and clicks.
    Agent expects x and y to be normalized coordinates from 0 to 1000 based on Gemini vision boxes.
    """
    screen_width, screen_height = pyautogui.size()
    real_x = int((x / 1000) * screen_width)
    real_y = int((y / 1000) * screen_height)
    
    # Smoothly move the cursor to the target coordinates
    pyautogui.moveTo(real_x, real_y, duration=0.5)
    # Give the application a brief moment to register the hover state
    time.sleep(0.1)
    pyautogui.click()

def take_screenshot() -> str:
    """Captures the screen and returns a base64 encoded PNG string."""
    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

def press_win_key():
    """Presses the Windows key (opens Start menu)."""
    pyautogui.press('win')

def type_text(text: str):
    """Types the specified text on the keyboard."""
    pyautogui.typewrite(text, interval=0.01)

def press_single_key(key: str):
    """Presses a single key (e.g., 'enter', 'tab', 'down')."""
    pyautogui.press(key)

import pyautogui
import io
import base64
import subprocess
import time

# Disable pyautogui fail-safe (moving mouse to corner stops script)
pyautogui.FAILSAFE = False

def open_app(name: str):
    """Opens an application through the shell."""
    subprocess.Popen(["start", name], shell=True)

def press_shortcut(*keys):
    """Presses a combination of keys."""
    pyautogui.hotkey(*keys)

def move_and_click(x: int, y: int):
    """Moves the mouse to specific coordinates and clicks."""
    pyautogui.click(x, y)

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

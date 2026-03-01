import pyautogui
import pyperclip
import io
import base64
import subprocess
import time
import pygetwindow as gw

# Disable pyautogui fail-safe (moving mouse to corner stops script)
pyautogui.FAILSAFE = False

import ctypes
import os

def _get_process_name(hwnd):
    try:
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h_process = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid.value)
        if not h_process: return ""
        exe_name = ctypes.create_unicode_buffer(260)
        size = ctypes.c_ulong(260)
        psapi = ctypes.WinDLL('psapi')
        if psapi.GetModuleFileNameExW(h_process, None, exe_name, size.value):
            ctypes.windll.kernel32.CloseHandle(h_process)
            return os.path.basename(exe_name.value).lower()
        ctypes.windll.kernel32.CloseHandle(h_process)
    except:
        pass
    return ""

def open_app(name: str):
    """Opens an application by navigating to it if already open, else uses the start menu."""
    try:
        search_name = name.lower()
        windows = gw.getAllWindows()
        
        # 1. Try to find by exact visual title match
        for w in windows:
            if w.title and search_name in w.title.lower():
                try:
                    w.activate()
                    return
                except Exception:
                    pass
                    
        # 2. Try to find by process executable name
        # Handles cases like Spotify where window title changes to the song name
        for w in windows:
            if w.title and w.visible:
                proc_name = _get_process_name(w._hWnd)
                if proc_name and (search_name in proc_name or proc_name.startswith(search_name)):
                    try:
                        w.activate()
                        return
                    except Exception:
                        pass
    except Exception as e:
        print(f"Error checking open windows: {e}")

    # Special handling for Chrome to ensure Playwright debugging ports are open
    if search_name in ["chrome", "google chrome"]:
        print("[OS] Launching Chrome with remote debugging ports...")
        press_shortcut('win', 'r')
        time.sleep(0.3)
        type_text('chrome --remote-debugging-port=9222')
        press_single_key('enter')
        time.sleep(1.0)
        maximize_window()
        return

    # Original method of pressing windows key, typing in the name and continuing
    press_win_key()
    time.sleep(0.3)
    type_text(name)
    time.sleep(0.2)
    press_single_key('enter')

def press_shortcut(*keys):
    """Presses a combination of keys."""
    pyautogui.hotkey(*keys)

def maximize_window():
    """Natively maximizes the currently active window using pygetwindow."""
    try:
        active_window = gw.getActiveWindow()
        if active_window and not active_window.isMaximized:
            active_window.maximize()
    except Exception as e:
        print(f"Failed to natively maximize window: {e}")

def move_and_click(x: int, y: int):
    """
    Moves the mouse to specific coordinates and clicks.
    Agent expects x and y to be normalized coordinates from 0 to 1000 based on Gemini vision boxes.
    """
    screen_width, screen_height = pyautogui.size()
    real_x = int((x / 1000) * screen_width)
    
    # Gemini vision model bounding boxes also use top-left as (0,0), 
    # matching pyautogui, so we don't need to invert the Y axis.
    real_y = int((y / 1000) * screen_height)
    
    # Move the mouse first so hover states can register, then click
    pyautogui.moveTo(real_x, real_y, duration=0.0)
    pyautogui.click(real_x, real_y)

def take_screenshot(step: int = None) -> str:
    """Captures the screen and returns a base64 encoded PNG string."""
    img = pyautogui.screenshot()
    
    # Save original screenshot for debugging
    import os
    os.makedirs("debug", exist_ok=True)
    filename = f"debug/step_{step}_original.png" if step is not None else "debug/debug_original_screenshot.png"
    img.save(filename)
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

def press_win_key():
    """Presses the Windows key (opens Start menu)."""
    pyautogui.press('win')

def type_text(text: str):
    """Types the specified text using clipboard paste for full special character support.
    Falls back to pyautogui.typewrite if clipboard is unavailable.
    """
    try:
        # Use clipboard paste — handles @, !, spaces, Unicode, etc.
        pyperclip.copy(text)
        time.sleep(0.05)
        pyautogui.hotkey('ctrl', 'v')
    except Exception:
        # Fallback: character-by-character (slower, limited char set)
        pyautogui.typewrite(text, interval=0.04)

def press_single_key(key: str):
    """Presses a single key (e.g., 'enter', 'tab', 'down')."""
    pyautogui.press(key)

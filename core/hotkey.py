import threading
from pynput import keyboard

def listen(on_toggle: callable):
    """
    Fires Ctrl+Shift+Space across the whole OS, even when widget isn't focused.
    Calls `on_toggle` when the hotkey is pressed.
    """
    # Define our hotkey combination

    # Ctrl + Shift + Space
    COMBINATION = {
        keyboard.Key.ctrl_l, 
        keyboard.Key.shift, 
        keyboard.Key.space
    }
    
    # Or fallback alternative mapping
    # Ctrl + Shift + Space
    COMBINATION_2 = {
        keyboard.Key.ctrl_r, 
        keyboard.Key.shift_r, 
        keyboard.Key.space
    }

    # F + J
    COMBINATION_3 = {
        keyboard.Key.f, 
        keyboard.Key.j
    }

    current = set()

    def on_press(key):
        if key in COMBINATION or key in COMBINATION_2 or key in COMBINATION_3:
            current.add(key)
            if all(k in current for k in COMBINATION) or all(k in current for k in COMBINATION_2) or all(k in current for k in COMBINATION_3):
                on_toggle()

    def on_release(key):
        try:
            current.remove(key)
        except KeyError:
            pass

    def start_listener():
        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()
            
    # Run in a background thread
    t = threading.Thread(target=start_listener, daemon=True)
    t.start()

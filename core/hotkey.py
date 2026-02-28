import threading
from pynput import keyboard

def listen(on_toggle: callable):
    """
    Fires Ctrl+Shift+Space across the whole OS, even when widget isn't focused.
    Calls `on_toggle` when the hotkey is pressed.
    """
    # Define our hotkey combination
    COMBINATION = {
        keyboard.Key.ctrl_l, 
        keyboard.Key.shift, 
        keyboard.Key.space
    }
    
    # Or fallback alternative mapping
    COMBINATION_2 = {
        keyboard.Key.ctrl_r, 
        keyboard.Key.shift_r, 
        keyboard.Key.space
    }

    current = set()

    def on_press(key):
        if key in COMBINATION or key in COMBINATION_2:
            current.add(key)
            if all(k in current for k in COMBINATION) or all(k in current for k in COMBINATION_2):
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

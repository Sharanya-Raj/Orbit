import threading
from pynput import keyboard

def listen(on_start: callable, on_stop: callable):
    """
    Fires Ctrl+Shift+Space across the whole OS, even when widget isn't focused.
    Calls `on_start` when the hotkey is pressed.
    Calls `on_stop` when the hotkey is released.
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
        keyboard.KeyCode.from_char('f'),
        keyboard.KeyCode.from_char('j')
    }

    current = set()
    is_active = False

    def on_press(key):
        nonlocal is_active
        if key in COMBINATION or key in COMBINATION_2 or key in COMBINATION_3:
            current.add(key)
            if not is_active:
                if all(k in current for k in COMBINATION) or all(k in current for k in COMBINATION_2) or all(k in current for k in COMBINATION_3):
                    is_active = True
                    on_start()

    def on_release(key):
        nonlocal is_active
        try:
            current.remove(key)
        except KeyError:
            pass
            
        if is_active:
            # If the combination is broken by a release, stop
            if not (all(k in current for k in COMBINATION) or all(k in current for k in COMBINATION_2) or all(k in current for k in COMBINATION_3)):
                is_active = False
                on_stop()

    def start_listener():
        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()

    def killswitch_on_press(key):
        if hasattr(key, 'char') and key.char == '\\':
            import os
            import signal
            print("\n[System] Global killswitch activated ('\\'). Exiting immediately.")
            os.kill(os.getpid(), signal.SIGTERM)

    def start_killswitch():
        with keyboard.Listener(on_press=killswitch_on_press) as listener:
            listener.join()
            
    # Run hotkeys in background threads
    t1 = threading.Thread(target=start_listener, daemon=True)
    t1.start()
    
    t2 = threading.Thread(target=start_killswitch, daemon=True)
    t2.start()

import time
from . import browser, os_control
from core import tts

def execute_action(action: dict, page=None, browser_context=None):
    """
    The action router. Receives a single action dict from Gemini and calls the right function.
    """
    match action["type"]:
        # Browser actions
        case "open_url":      
            if page: browser.open_url(page, action["url"])
        case "click_element": 
            if page: browser.click_element(page, action["selector"])
        case "type_text":     
            # Depending on context, might be browser or OS
            if page: 
                browser.type_text(page, action["text"])
            else:
                os_control.type_text(action["text"])
        case "press_key":     
            if page:
                browser.press_key(page, action["key"])
            else:
                os_control.press_single_key(action["key"])
                
        # OS Actions
        case "open_app":      os_control.open_app(action["app"])
        case "win_key":       os_control.press_win_key()
        case "click_xy":      os_control.move_and_click(action.get("x", 0), action.get("y", 0))
        
        # General Actions
        case "speak":         tts.speak(action["text"])
        case "wait":          time.sleep(action.get("ms", 1000) / 1000.0)
        case "screenshot":    pass  # agent loop handles this automatically
        case "done":          pass  # handled in agent loop return
        case _:               
            print(f"Unknown action type: {action.get('type')}")

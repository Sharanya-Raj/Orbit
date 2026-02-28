import time
from . import os_control, browser
from core import tts

def execute_action(action: dict, page=None, browser_context=None):
    """
    The action router. Uses the 'context' field from the action to route correctly.
    Since Playwright was removed, most actions are OS-level now, except click_element.
    """
    match action["type"]:
        case "click_element":
            if page: browser.click_element(page, action["selector"])
        case "press_shortcut": os_control.press_shortcut(*action["keys"])
        case "type_text":  os_control.type_text(action["text"])
        case "press_key":  os_control.press_single_key(action["key"])
        case "open_app":   os_control.open_app(action["app"])
        case "win_key":    os_control.press_win_key()
        case "click_xy":   os_control.move_and_click(action.get("x", 0), action.get("y", 0))
        case "speak":      tts.speak(action["text"])
        case "wait":       time.sleep(action.get("ms", 1000) / 1000.0)
        case "screenshot": pass
        case "done":       pass
        case "request_user_input": pass
        case _:
            print(f"[Router] Unknown action type: {action.get('type')}")

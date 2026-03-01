import time
import logging
from . import os_control, browser
from core import tts, logger


def validate_action_safe(action: dict):
    """Block any action that targets the taskbar region (bbox ymin > 950).

    Returns (action_to_run, was_replaced) where was_replaced=True means
    the original action was intercepted and a safe replacement was returned.
    """
    if action.get("type") == "click_box":
        bbox = action.get("bbox", [])
        if len(bbox) == 4 and bbox[0] > 950:  # ymin > 950 → bottom of screen
            logging.warning(f"BLOCKED taskbar click attempt: {action}")
            return {
                "type": "speak",
                "thought": "Attempted to click taskbar area which is hidden. Using open_app instead.",
                "text": "The taskbar is hidden. Please use the app launcher.",
                "context": "os",
            }, True
    return action, False


def execute_action(action: dict, page=None, browser_context=None):
    """
    The action router. Uses the 'context' field from the action to route correctly.
    Since Playwright was removed, most actions are OS-level now, except click_element.
    """
    action, replaced = validate_action_safe(action)
    if replaced:
        tts.speak(action["text"])
        return

    use_browser = action.get("context") == "browser" and page is not None
    
    match action["type"]:
        case "click_element":
            if page: browser.click_element(page, action["selector"])

        # Dual-context actions — routed by context field
        case "type_text":
            if use_browser:
                browser.type_text(page, action["text"])
            else:
                os_control.type_text(action["text"])

        case "press_key":
            if use_browser:
                browser.press_key(page, action["key"])
            else:
                os_control.press_single_key(action["key"])

        # OS-only actions
        case "open_app":  os_control.open_app(action["app"])
        case "win_key":   os_control.press_win_key()
        case "maximize_window": os_control.maximize_window()
        case "press_shortcut": os_control.press_shortcut(*action["keys"])
        case "click_box":
            bbox = action.get("bbox", [0, 0, 0, 0])
            cx = (bbox[1] + bbox[3]) / 2.0
            cy = (bbox[0] + bbox[2]) / 2.0
            os_control.move_and_click(cx, cy)

        # General actions
        case "speak":      tts.speak(action["text"])
        case "wait":       time.sleep(action.get("ms", 1000) / 1000.0)
        case "screenshot": pass
        case "done":       pass
        case "request_user_input": pass
        case _:
            logger.log_error(f"[Router] Unknown action type: {action.get('type')!r}")
            print(f"[Router] Unknown action type: {action.get('type')}")
    
import time
from . import browser, os_control
from core import tts

def execute_action(action: dict, page=None, browser_context=None):
    """
    The action router. Uses the 'context' field from the action to route correctly.
    'context': 'browser' → uses Playwright page
    'context': 'os'      → uses pyautogui / OS
    Falls back to page presence for backward compatibility.
    """
    ctx = action.get("context", "os")
    use_browser = (ctx == "browser") and (page is not None)

    match action["type"]:
        # Browser-only actions
        case "open_url":
            if page: browser.open_url(page, action["url"])

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
        case "click_xy":  os_control.move_and_click(action.get("x", 0), action.get("y", 0))

        # General actions
        case "speak":      tts.speak(action["text"])
        case "wait":       time.sleep(action.get("ms", 1000) / 1000.0)
        case "screenshot": pass   # handled automatically in agent loop
        case "done":       pass   # handled in agent loop return
        case "request_user_input": pass  # handled in agent loop
        case _:
            print(f"[Router] Unknown action type: {action.get('type')}")

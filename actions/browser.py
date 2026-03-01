def extract_affordances(page):
    """
    Evaluates JavaScript on the Playwright page to extract all potentially clickable elements
    (links, buttons, inputs) and returns them as a list of dictionaries containing
    their selector and visible text/value.
    """
    return page.evaluate("""
        () => {
            const clickable = [...document.querySelectorAll('a, button, input')];
            return clickable.map(el => ({
                selector: el.tagName.toLowerCase() + (el.id ? '#' + el.id : ''),
                text: el.innerText || el.value || null
            }));
        }
    """)

def click_element(page, text_to_match: str):
    """
    Attempts to click an element on the active Playwright page matching the exact inner text.
    First tries an exact text match, then falls back to a substring match.
    """
    # 1. Try exact text match using Playwright's text engine
    try:
        exact_locator = page.locator(f"text='{text_to_match}'").first
        if exact_locator.count() > 0:
            exact_locator.click(timeout=3000)
            return
    except Exception as e:
        print(f"[Browser] Exact match click failed, trying fallback: {e}")
            
    # 2. Try substring match (case-insensitive)
    try:
        substring_locator = page.locator(f"text={text_to_match}").first
        if substring_locator.count() > 0:
            substring_locator.click(timeout=3000)
            return
    except Exception as e:
        print(f"[Browser] Substring match click failed, trying JS fallback: {e}")

    # 3. JavaScript brute-force fallback for shadow DOMs / weird SPAs (like Discord)
    try:
        clicked = page.evaluate("""
            (searchText) => {
                const elements = [...document.querySelectorAll('a, button, input, div, span')].reverse();
                for (let el of elements) {
                    if (el.innerText && el.innerText.trim() === searchText && el.offsetParent !== null) {
                        el.click();
                        return {clicked: true, tag: el.tagName, className: el.className};
                    }
                }
                return {clicked: false};
            }
        """, text_to_match)
        if clicked and clicked.get("clicked"):
            print(f"[Browser] JS clicked '{text_to_match}' successfully on <{clicked.get('tag')} class='{clicked.get('className')}'>")
            return

        print(f"[Browser] Could not find any element matching '{text_to_match}'")
            
    except Exception as e:
        print(f"[Browser] JS fallback failed to click '{text_to_match}': {e}")

def type_text(page, text: str):
    """Types text directly into the currently focused element on the web page."""
    try:
        page.keyboard.type(text)
    except Exception as e:
        print(f"[Browser] Failed to type text: {e}")

def press_key(page, key: str):
    """Presses a specific key (e.g., 'Enter', 'Tab') on the web page."""
    try:
        page.keyboard.press(key)
    except Exception as e:
        print(f"[Browser] Failed to press key '{key}': {e}")

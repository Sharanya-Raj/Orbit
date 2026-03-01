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
    # 1. Try Playwright's robust get_by_text (handles nested and fuzzy matching well)
    try:
        locator = page.get_by_text(text_to_match, exact=False).first
        if locator.count() > 0:
            # force=True bypasses Playwright's strict actionability checks (visbility/overlapping)
            locator.click(timeout=3000, force=True)
            return
    except Exception as e:
        print(f"[Browser] 'get_by_text' click failed, trying JS fallback: {e}")

    # 2. JavaScript brute-force fallback for shadow DOMs / weird SPAs (like Discord)
    try:
        clicked = page.evaluate("""
            (searchText) => {
                const searchLower = searchText.toLowerCase().trim();
                // Exclude massive container elements that might accidentally match the text
                const excludeTags = ['BODY', 'HTML', 'MAIN', 'SECTION', 'HEADER', 'FOOTER', 'ARTICLE', 'NAV'];
                const elements = [...document.querySelectorAll('*')].reverse(); // Start from deepest children
                
                for (let el of elements) {
                    if (excludeTags.includes(el.tagName)) continue;
                    
                    const text = (el.textContent || el.innerText || "").toLowerCase();
                    if (text.includes(searchLower) && el.offsetParent !== null) {
                        el.click();
                        return {clicked: true, tag: el.tagName, className: el.className};
                    }
                }
                return {clicked: false};
            }
        """, text_to_match)
        
        if clicked and clicked.get("clicked"):
            print(f"[Browser] JS clicked element successfully: <{clicked.get('tag')} class='{clicked.get('className')}'>")
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

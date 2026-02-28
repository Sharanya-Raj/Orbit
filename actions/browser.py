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
    try:
        # Exact text matcher
        locator = page.get_by_text(text_to_match, exact=True).first
        if locator.count() > 0:
            locator.click(timeout=3000)
            return
            
        # Fallback to loose match
        locator = page.get_by_text(text_to_match, exact=False).first
        if locator.count() > 0:
            locator.click(timeout=3000)
            
    except Exception as e:
        print(f"[Browser] Failed to click '{text_to_match}': {e}")

def open_url(page, url: str):
    """Navigates to a URL and waits for page load."""
    page.goto(url, wait_until="networkidle")

def click_element(page, selector: str):
    """Clicks an element by text via a priority waterfall of Playwright strategies."""
    strategies = [
        lambda: page.get_by_role("button",  name=selector, exact=False).first.click(timeout=2000),
        lambda: page.get_by_role("link",    name=selector, exact=False).first.click(timeout=2000),
        lambda: page.get_by_role("textbox", name=selector, exact=False).first.click(timeout=2000),
        lambda: page.get_by_role("menuitem",name=selector, exact=False).first.click(timeout=2000),
        lambda: page.get_by_text(selector,  exact=False).first.click(timeout=2000),
        # Safe last resort: :text-is() handles dashes, spaces, colons without CSS parsing
        lambda: page.locator(f':text-is("{selector}")').first.click(timeout=2000),
    ]
    last_err = None
    for strategy in strategies:
        try:
            strategy()
            return
        except Exception as e:
            last_err = e
    raise Exception(f"click_element failed for '{selector}': {last_err}")

def type_text(page, text: str):
    """Types text at the current focused element."""
    page.keyboard.type(text)

def press_key(page, key: str):
    """Presses a key: "Enter", "Tab", "Escape" """
    page.keyboard.press(key)

def focus_url_bar(page):
    """Ctrl+L — jumps focus to browser address bar."""
    page.keyboard.press("Control+l")

def new_tab(browser):
    """Opens a new tab, returns the new page object."""
    context = browser.contexts[0]
    page = context.new_page()
    return page

def scroll(page, direction: str):
    """Scroll up or down."""
    if direction.lower() == "down":
        page.mouse.wheel(0, 800)
    elif direction.lower() == "up":
        page.mouse.wheel(0, -800)

def wait(page, ms: int):
    """Wait for a fixed time (for slow page loads)."""
    page.wait_for_timeout(ms)

def get_page_text(page) -> str:
    """Returns all visible text — lets Gemini "read" the page."""
    return page.evaluate("() => document.body.innerText")

def screenshot(page) -> bytes:
    """Takes a screenshot — fed back to Gemini each loop iteration."""
    return page.screenshot(type="png")

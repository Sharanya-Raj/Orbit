def open_url(page, url: str):
    """Navigates to a URL and waits for page load."""
    page.goto(url, wait_until="networkidle")

def click_element(page, selector: str):
    """Clicks an element matching the CSS selector or exact text."""
    try:
        # First try as a standard CSS selector
        page.click(selector, timeout=2000)
    except:
        # If it fails (which it often will since DeepSeek is passing plain text), click by text
        page.get_by_text(selector, exact=False).first.click(timeout=3000)

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

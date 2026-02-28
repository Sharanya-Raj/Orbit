import os
import json
import time
import re
import threading
import base64
from pathlib import Path
from dotenv import load_dotenv
import openai
from actions import os_control, execute_action
from core import tts, state

# Cross-thread mechanism for pausing the agent and receiving a user voice reply
user_reply_event = threading.Event()
user_reply_text = ""

# Load environment variables
load_dotenv()

# We try to load Playwright, but it might not be initialized if we're only doing OS actions
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    pass

# Path to store persistent browser profile (cookies, sessions, etc.)
PROFILE_DIR = Path(__file__).parent / ".browser_profile"
PROFILE_DIR.mkdir(exist_ok=True)

# Global Playwright state for lazy loading
browser_instance = None
browser_context = None
page_instance = None

def get_browser_page():
    """Lazily initializes a persistent Edge browser — cookies and sessions are saved between runs."""
    global browser_instance, browser_context, page_instance
    if page_instance is None:
        p = sync_playwright().start()
        # channel="msedge" opens Microsoft Edge instead of Chromium
        browser_context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            channel="msedge",
        )
        page_instance = browser_context.pages[0] if browser_context.pages else browser_context.new_page()
        # Clear any stale tab state from the previous session
        page_instance.goto("about:blank")

    return page_instance


VISION_PROMPT = """
You are acting as the eyes for an AI computer-control agent.
You will be shown a screenshot of the user's current screen.
Your job is to provide a highly detailed, accessibility-focused text description of the screen.
Describe the layout, all visible windows, and specifically list every interactable element (buttons, text fields, links, icons) and their general locations on the screen.
CRITICAL: For EVERY interactable element you list, you MUST provide its spatial bounding box in normalized [ymin, xmin, ymax, xmax] format out of 1000.
Example: `[150, 400, 200, 500] "Submit" button`
Do not guess what the user wants to do, just describe what you see accurately.
"""

SYSTEM_PROMPT = """
You are an AI computer-control agent helping blind users operate their computer by voice.

You will be provided with a text description of the current screen (from your vision module) and the user's goal.
Respond ONLY with a single JSON action object — no prose, no markdown, no fences.

Available actions:
  {"type": "open_url",            "url": "https://..."}
  {"type": "click_element",       "selector": "exact-text-of-button-or-link"}
  {"type": "click_xy",            "x": 100, "y": 200}
  {"type": "type_text",           "text": "..."}
  {"type": "press_key",           "key": "Enter"}
  {"type": "open_app",            "app": "notepad"}
  {"type": "win_key"}
  {"type": "wait",                "ms": 1500}
  {"type": "speak",               "text": "saying something to the user"}
  {"type": "request_user_input",  "prompt": "Please type your email, then say OK when done."}
  {"type": "done",                "message": "summary of what was done"}

## Decision Priority (follow this order strictly):

1. WEB APPS → Always use open_url directly. NEVER use win_key or open_app for web services.
   Common mappings:
   - "Google Docs / Google Document" → open_url https://docs.google.com/document/create
   - "Google Sheets"                 → open_url https://docs.google.com/spreadsheets/create
   - "Google Slides"                 → open_url https://docs.google.com/presentation/create
   - "Gmail"                         → open_url https://mail.google.com
   - "YouTube"                       → open_url https://youtube.com
   - "Google Drive"                  → open_url https://drive.google.com
   - Any website/web app             → open_url <full URL>

2. DESKTOP APPS (not browser-based) → Use win_key, then type_text the app name, then press_key Enter.
   Examples: Notepad, Calculator, Paint, File Explorer, VS Code, Spotify.

3. BROWSER CLICKS → If a browser page is open, use click_element with the EXACT visible button/link text.
   NEVER use click_xy inside a browser. NEVER use win_key while a browser task is in progress.

4. OS CLICKS → Use click_xy only for native desktop UI (outside a browser).
   Coordinates come from vision bounding boxes [ymin, xmin, ymax, xmax] normalized to 1000.
   Center = ((ymin+ymax)/2, (xmin+xmax)/2).

## General Rules:
- Return exactly ONE action per response.
- Verify each step succeeded before issuing the next.
- SENSITIVE DATA: On any login/password/form page, NEVER type credentials.
  Use request_user_input with a clear spoken prompt, then click Submit after user confirms.
- Always end with {"type": "done", "message": "spoken summary"}.
"""


# Initialize API Clients
vision_client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY", ""),
)

decision_client = openai.OpenAI(
    base_url="https://api.featherless.ai/v1",
    api_key=os.environ.get("FEATHERLESS_API_KEY", ""),
)

# #Check if the API keys are loaded
# print("OpenRouter API Key:", os.environ.get("OPENROUTER_API_KEY"))
# print("Featherless API Key:", os.environ.get("FEATHERLESS_API_KEY"))    

def run_agent(instruction: str, update_log_callback=None) -> str:
    """Runs the main perception-action loop to execute the user instruction."""
    if update_log_callback:
        update_log_callback(f"🎤 You: {instruction}")
        
    decision_messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {"role": "user",   "content": f"User Goal: {instruction}\nWhat's next?"}
    ]

    for iteration in range(15):  # max 15 iterations safety cap
        # 1. Take screenshot of current OS state
        screenshot_b64 = os_control.take_screenshot()

        # 2. Get Vision Description from Gemini
        try:
            if update_log_callback:
                update_log_callback("[System] Analyzing screen...")
                
            vision_response = vision_client.chat.completions.create(
                model="google/gemini-3-flash-preview",
                messages=[
                    {"role": "system", "content": VISION_PROMPT.strip()},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this screen."},
                            {"type": "image_url", "image_url": {
                                "url": f"data:image/png;base64,{screenshot_b64}"
                            }}
                        ]
                    }
                ],
                max_tokens=500,
            )
            screen_description = vision_response.choices[0].message.content.strip()
            
        except Exception as e:
            error_msg = f"Error in vision module: {str(e)}"
            if update_log_callback:
                update_log_callback(error_msg)
            print(error_msg)
            return "Task failed at vision step due to an error."

        # 3. Add vision context to decision messages
        decision_messages[-1]["content"] = f"Current Screen Description:\n{screen_description}\n\nUser Goal: {instruction}\nWhat should I do next? (Reply only with JSON)"

        # 4. Get Action Decision from DeepSeek
        try:
            if update_log_callback:
                update_log_callback("[System] Deciding action...")

            response = decision_client.chat.completions.create(
                model="deepseek-ai/DeepSeek-V3-0324",  # HuggingFace-style ID required by Featherless
                messages=decision_messages,
                max_tokens=256,
            )
            
            raw_content = response.choices[0].message.content.strip()

            # Robustly extract the first valid JSON object from the response,
            # ignoring any surrounding prose or markdown fences DeepSeek may include.
            import re
            json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
            if json_match:
                raw_content = json_match.group(0)
            else:
                raise ValueError(f"No JSON found in DeepSeek response: {raw_content[:200]}")

            action = json.loads(raw_content)
            
            if update_log_callback:
                update_log_callback(f"🤖 Agent: {json.dumps(action)}")

            if action["type"] == "done":
                msg = action.get("message", "Done.")
                tts.speak(msg)
                return msg

            if action["type"] == "speak":
                tts.speak(action["text"])

            if action["type"] == "request_user_input":
                # Speak the prompt so the user knows what to type
                prompt = action.get("prompt", "Please provide input, then press OK on the widget.")
                tts.speak(prompt)
                if update_log_callback:
                    update_log_callback(f"⌨️ Waiting for user input: {prompt}")

                # Signal the widget to switch to input-waiting mode
                state.state.set_state("waiting_for_input")

                # Block this thread until widget provides the user's voice reply
                user_reply_event.clear()
                user_reply_event.wait(timeout=120)  # 2-min timeout

                reply = user_reply_text.strip() or "done"
                if update_log_callback:
                    update_log_callback(f"🎤 User replied: {reply}")

                # Inject reply into conversation so agent knows user completed the step
                decision_messages.append({"role": "assistant", "content": json.dumps(action)})
                decision_messages.append({"role": "user", "content": f"User said: '{reply}'. Now continue."})
                continue  # Skip execute_action, go to next iteration

            # 5. Execute the action
            # IMPORTANT: only route browser-specific actions to Playwright.
            # OS actions (win_key, type_text, click_xy, press_key) must ALWAYS go to pyautogui,
            # even when the browser is already open.
            active_page = None
            if action["type"] in ["open_url", "click_element"]:
                active_page = get_browser_page()

            execute_action(action, page=active_page, browser_context=browser_context)

            
            time.sleep(0.8)  # Let UI settle before next frame

            # Append the cycle log
            decision_messages.append({"role": "assistant", "content": str(action)})
            decision_messages.append({"role": "user", "content": "Action complete. What's next?"})

        except Exception as e:
            error_msg = f"Error in agent loop: {str(e)}"
            if update_log_callback:
                update_log_callback(error_msg)
            print(error_msg)
            return "Task failed due to an error."

    return "Maximum steps reached."


if __name__ == "__main__":
    run_agent("Open Google Chrome")


import os
import json
import time
import base64
from dotenv import load_dotenv
import openai
from actions import os_control, execute_action
from core import tts, state

# Load environment variables
load_dotenv()

# We try to load Playwright, but it might not be initialized if we're only doing OS actions
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    pass

# Global Playwright state for lazy loading
browser_instance = None
browser_context = None
page_instance = None

def get_browser_page():
    """Lazily initializes the Playwright browser when needed."""
    global browser_instance, browser_context, page_instance
    if page_instance is None:
        p = sync_playwright().start()
        browser_instance = p.chromium.launch(headless=False)
        browser_context = browser_instance.new_context()
        page_instance = browser_context.new_page()
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
  {"type": "open_url",      "url": "https://..."}
  {"type": "click_element", "selector": "exact-text-of-button-or-link"}
  {"type": "click_xy",      "x": 100, "y": 200}
  {"type": "type_text",     "text": "..."}
  {"type": "press_key",     "key": "Enter"}
  {"type": "open_app",      "app": "chrome"}
  {"type": "win_key"}
  {"type": "wait",          "ms": 1500}
  {"type": "speak",         "text": "saying something to the user"}
  {"type": "done",          "message": "summary of what was done"}

Rules:
- OS Navigation (coordinates): The vision module provides bounding boxes in [ymin, xmin, ymax, xmax] normalized to 1000.
  To click a desktop icon or native app, calculate the center coordinate (min+max)/2 and output {"type": "click_xy", "x": 100, "y": 200}.
- Web Browser Navigation (Text Matching): If you are currently in a browser (or opened one via open_url), 
  DO NOT use click_xy. Instead, use click_element and pass the EXACT text of the button or link as the 'selector'. 
  Example: {"type": "click_element", "selector": "Sign In"}
- Return exactly ONE action per JSON response
- Verify the previous action succeeded before moving to the next step
- Use win_key + type_text + press_key("Enter") to open desktop apps
- Always end with the "done" type and a spoken summary for the user
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
                model="deepseek/deepseek-chat-v3-0324",  # Featherless uses HuggingFace-style IDs
                messages=decision_messages,
                max_tokens=256,
            )
            
            raw_content = response.choices[0].message.content.strip()
            
            # Clean up potential markdown formatting from the response
            if raw_content.startswith("```json"):
                raw_content = raw_content[7:]
            if raw_content.startswith("```"):
                raw_content = raw_content[3:]
            if raw_content.endswith("```"):
                raw_content = raw_content[:-3]
                
            raw_content = raw_content.strip()
            action = json.loads(raw_content)
            
            if update_log_callback:
                update_log_callback(f"🤖 Agent: {json.dumps(action)}")

            if action["type"] == "done":
                msg = action.get("message", "Done.")
                tts.speak(msg)
                return msg

            if action["type"] == "speak":
                tts.speak(action["text"])

            # 5. Execute the action
            active_page = None
            if action["type"] in ["open_url", "click_element"] or page_instance is not None:
                # If they ask to navigate the web, or if browser is already open, pass the page
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
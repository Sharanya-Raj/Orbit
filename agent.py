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

SYSTEM_PROMPT = """
You are an AI computer-control agent helping blind users operate their computer by voice.

You will be shown a screenshot of the current screen and the user's goal.
Respond ONLY with a single JSON action object — no prose, no markdown, no fences.

Available actions:
  {"type": "open_url",      "url": "https://..."}
  {"type": "click_element", "selector": "css-or-aria"}
  {"type": "click_xy",      "x": 100, "y": 200}
  {"type": "type_text",     "text": "..."}
  {"type": "press_key",     "key": "Enter"}
  {"type": "open_app",      "app": "chrome"}
  {"type": "win_key"}
  {"type": "wait",          "ms": 1500}
  {"type": "speak",         "text": "saying something to the user"}
  {"type": "done",          "message": "summary of what was done"}

Rules:
- Return exactly ONE action per JSON response
- NO MARKDOWN FORMATTING OR BACKTICKS (Don't wrap the JSON in ```json )
- After each action the screen will be re-captured and sent back to you
- Verify the previous action succeeded before moving to the next step
- Prefer open_url for browser navigation over clicking through menus
- Use win_key + type_text + press_key("Enter") to open desktop apps
- Always end with the "done" type and a spoken summary for the user
"""

# Initialize API Client (OpenRouter)
client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY", ""),
)

def run_agent(instruction: str, update_log_callback=None) -> str:
    """Runs the main perception-action loop to execute the user instruction."""
    if update_log_callback:
        update_log_callback(f"🎤 You: {instruction}")
        
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {"role": "user",   "content": instruction}
    ]

    for iteration in range(15):  # max 15 iterations safety cap
        # Take screenshot of current OS state
        screenshot_b64 = os_control.take_screenshot()

        # Attach screenshot to latest user message
        text_content = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else "What should I do next?"
        
        messages[-1]["content"] = [
            {"type": "text", "text": text_content},
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{screenshot_b64}"
            }}
        ]

        try:
            response = client.chat.completions.create(
                model="google/gemini-flash-1.5",
                messages=messages,
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

            # Execute the action (assuming OS context for now, browser to be added dynamically)
             # Ideally we'd manage playwright browser/page state if needed here
            execute_action(action, page=None, browser_context=None)
            
            time.sleep(0.8)  # Let UI settle before next frame

            # Append the cycle log
            messages.append({"role": "assistant", "content": str(action)})
            messages.append({"role": "user", "content": "Action complete. What's next? (Return just the next JSON action)"})

        except Exception as e:
            error_msg = f"Error in agent loop: {str(e)}"
            if update_log_callback:
                update_log_callback(error_msg)
            print(error_msg)
            return "Task failed due to an error."

    return "Maximum steps reached."

import os
import json
import time
import re
import threading
import base64
from pathlib import Path
from dotenv import load_dotenv
import openai
from actions import os_control, execute_action, browser
from core import tts, state

# Cross-thread mechanism for pausing the agent and receiving a user voice reply
user_reply_event = threading.Event()
user_reply_text = ""

# Load environment variables
load_dotenv()

import traceback

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    pass

# Global Playwright state
browser_instance = None
browser_context = None
page_instance = None

def get_browser_page():
    """Lazily attaches to the running Chrome/Edge instance over CDP to extract DOM."""
    global browser_instance, browser_context
    try:
        if browser_instance is None or not browser_instance.is_connected():
            p = sync_playwright().start()
            browser_instance = p.chromium.connect_over_cdp("http://localhost:9222")
            
        # Always fetch the most recent active page, as users might close/open tabs
        contexts = browser_instance.contexts
        if not contexts: return None
        pages = contexts[0].pages
        if not pages: return None
        
        # Return the last active page
        return pages[-1]
    except Exception as e:
        # Expected if browser isn't open yet or wasn't launched with debugging port
        browser_instance = None
        return None

VISION_PROMPT = """
You are acting as the eyes for an AI computer-control agent.
You will be shown a screenshot of the user's current screen.
Your job is to provide a highly detailed, accessibility-focused text description of the screen.
Describe the layout, all visible windows, and specifically list every interactable element (buttons, text fields, links, icons) and their general locations on the screen.
CRITICAL: For EVERY interactable element you list, you MUST provide its spatial bounding box in normalized [ymin, xmin, ymax, xmax] format out of 1000.
Example: `[150, 400, 200, 500] "Submit" button`
Do not guess what the user wants to do, just describe what you see accurately.
CRITICAL: Explicitly state the name of the currently active/focused application or window at the beginning of your description.
"""

SYSTEM_PROMPT = """
You are an AI computer-control agent helping blind users operate their computer by voice.
You will be provided with a text description of the current screen (from your vision module) and the user's goal.
Respond ONLY with a single JSON action object — no prose, no markdown, no fences.

Available actions (must include a "thought" explaining your plan):
  {"thought": "I need to click the search bar...", "type": "click_xy", "x": 100, "y": 200, "context": "os"}
  {"thought": "Typing the URL into the bar...", "type": "type_text", "text": "...", "context": "os"}
  {"thought": "Pressing Enter to search...", "type": "press_key", "key": "Enter", "context": "os"}
  {"thought": "Opening Run dialog...", "type": "press_shortcut", "keys": ["win", "r"], "context": "os"}
  {"thought": "Waiting for app to load...", "type": "wait", "ms": 1500, "context": "os"}
  {"thought": "Saying hello...", "type": "speak", "text": "saying something to the user", "context": "os"}
  {"thought": "Need user to log in...", "type": "request_user_input", "prompt": "spoken instruction for the user", "context": "os"}
  {"thought": "Goal achieved.", "type": "done", "message": "summary of what was done", "context": "os"}

Rules — follow this decision tree EVERY time:

1. BROWSING THE WEB:
   - To open the browser, you MUST use the Windows Run dialog to enable debugging ports. DO NOT use the Start Menu search for Chrome.
   - First, `press_shortcut` with keys `["win", "r"]`.
   - Wait 1000ms, then `type_text` exactly: `chrome --remote-debugging-port=9222` and press Enter. This is CRITICAL so we can attach to the DOM.
   - Wait for it to open, then click the address bar and do your search.
   - NEVER guess coordinates. EVERY action requires the Vision module to give you the exact coordinate bounding box.

2. OS & DESKTOP TASKS:
   - To click ANY native element, web button, or UI element: use `click_xy`.
   - The Vision model provides bounding boxes in the format `[ymin, xmin, ymax, xmax]`.
   - You MUST calculate the exact center coordinates using this formula:
     `x = (xmin + xmax) / 2`
     `y = (ymin + ymax) / 2`
   - CRITICAL: Calculate the final integer values yourself BEFORE generating the JSON. Do NOT put mathematical expressions in the JSON. `x` and `y` must be numbers.
   - Windows Search results: ALWAYS use `click_xy` to click them based on the vision module.
   - To maximize a window, use: `{"thought": "Maximizing...", "type": "press_shortcut", "keys": ["win", "up"], "context": "os"}`
   
3. BROWSER DOM NAVIGATION (If inside a browser):
   - You MUST maximize the browser as soon as you open it.
   - You can use Playwright's `click_element` Action to click exact text on a webpage instead of `click_xy` coordinates if you want to ensure accuracy.
   - {"thought": "Clicking the Sign In button...", "type": "click_element", "selector": "Sign In", "context": "browser"}
   
4. BATCHING ACTIONS:
   - You can output a single action JSON object OR an array of action objects `[{}, {}]` to execute multiple steps quickly without waiting for a new screenshot (e.g., win_key -> type_text "chrome" -> press_key "Enter").

AUTH RULE (HIGHEST PRIORITY):
If the screen shows ANY of these: sign-in form, login page, account picker, "Create account", "Sign in", "Log in", "Choose an account", "Continue with Google", "Forgot password", or any page where credentials are required — output `request_user_input` IMMEDIATELY to hand off to the user.
Example: {"thought": "Log in needed", "type": "request_user_input", "prompt": "I see a sign-in page. Please sign in, then press the hotkey and say OK when you're done.", "context": "os"}

General:
- Return ONLY valid JSON (either a single action object or a list of action objects).
- Only click coordinates explicitly listed in the vision description. NEVER guess or hallucinate buttons like "Open" if they are not listed. If "Open" is not listed, click the app name text itself.
- The MOMENT the goal is achieved, output done immediately.
- Always end with {"type": "done", "message": "spoken summary for the user"}.
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
    decision_messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {"role": "user",   "content": f"User Goal: {instruction}\nWhat's next?"}
    ]
    just_resumed_from_input = False  # tracks when we need to append instead of overwrite

    for iteration in range(15):  # max 15 iterations safety cap
        if update_log_callback:
            update_log_callback(f"[Step {iteration + 1}/15]")
        # 1. Take screenshot of current OS state
        screenshot_b64 = os_control.take_screenshot()

        # 2. Get Vision Description from Gemini
        try:
            if update_log_callback:
                update_log_callback("[System] Analyzing screen...")
            
            print("\n[Vision] Prompting Vision Model with screen snapshot...")
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
                max_tokens=800,
            )
            screen_description = vision_response.choices[0].message.content.strip()
            # --- DOM Extraction Injection ---
            dom_context = ""
            if "chrome" in screen_description.lower() or "edge" in screen_description.lower():
                try:
                    page = get_browser_page()
                    if page:
                        affordances = browser.extract_affordances(page)
                        # Truncate affordances if there are too many to save tokens
                        if len(affordances) > 50:
                            affordances = affordances[:50]
                        dom_context = f"\n[DOM Context - {len(affordances)} Clickable Elements Available]:\n{json.dumps(affordances)}"
                except Exception as e:
                    print(f"Failed to extract DOM: {e}")
                    pass
                    
            print(f"\n[Vision Model Screen Description]:\n{screen_description}\n")
            
        except Exception as e:
            error_msg = f"Error in vision module: {str(e)}"
            if update_log_callback:
                update_log_callback(error_msg)
            print(error_msg + "\n" + traceback.format_exc())
            return "Task failed at vision step due to an error."

        # 3. Add vision context to decision messages
        screen_update = f"Current Screen Description:\n{screen_description}{dom_context}\n\nUser Goal: {instruction}\nWhat should I do next? (Reply only with JSON)"
        if just_resumed_from_input:
            # After user input confirmation, APPEND the screen update (never overwrite the confirmation)
            decision_messages.append({"role": "user", "content": screen_update})
            just_resumed_from_input = False
        else:
            decision_messages[-1]["content"] = screen_update

        # 4. Get Action Decision from DeepSeek
        try:
            if update_log_callback:
                update_log_callback("[System] Deciding action...")

            print(f"\n[Decision] Prompting Decision Model with Goal: '{instruction}'")
            print(f"       and Messages Context: {json.dumps(decision_messages[-1]['content'])[:100]}...\n")
            
            response = decision_client.chat.completions.create(
                model="deepseek-ai/DeepSeek-V3-0324",  # HuggingFace-style ID required by Featherless
                messages=decision_messages,
                max_tokens=1500,
            )
            
            raw_content = response.choices[0].message.content.strip()
            print(f"\n[Decision Model Raw Response]:\n{raw_content}\n")
            
            # Clean up potential markdown formatting and extra conversational text
            start_dict = raw_content.find('{')
            start_list = raw_content.find('[')
            end_dict = raw_content.rfind('}')
            end_list = raw_content.rfind(']')
            
            starts = [i for i in (start_dict, start_list) if i != -1]
            ends = [i for i in (end_dict, end_list) if i != -1]
            
            if starts and ends:
                start_idx = min(starts)
                end_idx = max(ends)
                raw_content = raw_content[start_idx:end_idx+1]
                
            raw_content = raw_content.strip()
            
            try:
                action_data = json.loads(raw_content)
            except json.JSONDecodeError:
                # Fallback: LLMs often leave trailing commas `},]` which breaks json.loads
                # but works perfectly in Python's ast.literal_eval
                import ast
                action_data = ast.literal_eval(raw_content)
            
            # Convert single action to a list for uniform processing
            actions = action_data if isinstance(action_data, list) else [action_data]
            
            for action in actions:
                print(f"[Parsed Action]: {json.dumps(action)}")
                
                # Log the thought process first so the user can read it
                thought = action.get("thought", "")
                if thought and update_log_callback:
                    update_log_callback(f"🧠 Thinking: {thought}")
                    
                if update_log_callback:
                    update_log_callback(f"🤖 Action: {action.get('type')}")

                if action["type"] == "done":
                    # Play the completion sound
                    try:
                        import pygame
                        pygame.mixer.init()
                        pygame.mixer.music.load("sounds/Note_block_chime_scale.ogg")
                        pygame.mixer.music.play()
                    except Exception as e:
                        print(f"Failed to play finish sound: {e}")
                        
                    msg = action.get("message", "Done.")
                    tts.speak(msg)
                    return msg

                if action["type"] == "speak":
                    tts.speak(action["text"])

                if action["type"] == "request_user_input":
                    prompt = action.get("prompt", "Please provide input, then press OK on the widget.")
                    tts.speak(prompt)
                    if update_log_callback:
                        update_log_callback(f"⌨️ Waiting for user input: {prompt}")

                    state.state.set_state("waiting_for_input")
                    user_reply_event.clear()
                    user_reply_event.wait(timeout=120)

                    reply = user_reply_text.strip() or "done"
                    if update_log_callback:
                        update_log_callback(f"🎤 User replied: {reply}")

                    decision_messages.append({"role": "assistant", "content": json.dumps(action)})
                    decision_messages.append({"role": "user", "content": f"User confirmed (said: '{reply}'). ASSUME THIS STEP IS COMPLETE. Proceed to the NEXT action toward the goal. Do NOT use request_user_input again."})
                    just_resumed_from_input = True
                    break  # Stop processing the rest of the batch and get a new vision screenshot

                # 5. Execute OS action
                active_page = None
                if action["type"] == "click_element":
                    active_page = get_browser_page()
                    
                execute_action(action, page=active_page)
                time.sleep(1.0)  # Sleep between batched actions so OS UI can catch up

            # Append the cycle log (the whole batch)
            decision_messages.append({"role": "assistant", "content": json.dumps(action_data)})
            decision_messages.append({"role": "user", "content": "Action complete. What's next?"})

        except Exception as e:
            error_msg = f"Error in agent loop: {str(e)}"
            if update_log_callback:
                update_log_callback(error_msg)
            print(error_msg)
            return "Task failed due to an error."

    return "Maximum steps reached."

#for testing purposes
def console_logger(msg):
    print(msg)

if __name__ == "__main__":
    print("==================================================")
    print("Testing Agent Logic without Audio...")
    print("==================================================")
    
    # Fake transcript that bypasses the broken Audio module
    # fake_transcript = "Open Google Docs in the browser and create a new blank document."
    
    fake_transcript = "Open Discord and send a message to Sharanya saying Hi"
    print(f"Submitting Fake Transcript: '{fake_transcript}'\n")
    
    try:
        # Pass it straight into the refactored reasoning loop!
        final_message = run_agent(fake_transcript, update_log_callback=console_logger)
        print("\nAgent finished! Final Message:", final_message)
        
    except Exception as e:
        print("\nAgent crashed during testing:", str(e))
    
    print("\nTest complete.")
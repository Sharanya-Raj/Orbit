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
CRITICAL: Explicitly state the name of the currently active/focused application or window at the beginning of your description.
"""

SYSTEM_PROMPT = """
You are an AI computer-control agent helping blind users operate their computer by voice.

You will be provided with a text description of the current screen (from your vision module) and the user's goal.
Respond ONLY with a single JSON action object — no prose, no markdown, no fences.

Available actions:
  {"type": "open_url",      "url": "https://...", "context": "browser"}
  {"type": "click_element", "selector": "exact-text-of-button-or-link", "context": "browser"}
  {"type": "click_xy",      "x": 100, "y": 200, "context": "os"}
  {"type": "type_text",     "text": "...", "context": "os"}
  {"type": "press_key",     "key": "Enter", "context": "os"}
  {"type": "open_app",      "app": "chrome", "context": "os"}
  {"type": "win_key",       "context": "os"}
  {"type": "wait",          "ms": 1500, "context": "os"}
  {"type": "speak",         "text": "saying something to the user", "context": "os"}
  {"type": "done",          "message": "summary of what was done", "context": "os"}

Rules:
- Provide a "context" field: use "browser" ONLY if interacting with a web page via open_url or click_element. Use "os" for EVERYTHING else (Desktop apps, Discord, Settings, etc).
- OS Navigation (coordinates): The vision module provides bounding boxes in [ymin, xmin, ymax, xmax] normalized to 1000.
  To click a desktop icon or native app (like Discord), calculate the center coordinate (min+max)/2 and output {"type": "click_xy", "x": 100, "y": 200, "context": "os"}. DO NOT use click_element for desktop apps.
- Web Browser Navigation (Text Matching): If acting inside a web page (or just opened one via open_url), 
  DO NOT use click_xy. Instead, use click_element and pass the EXACT text of the button or link as the 'selector', with "context": "browser".
- Return exactly ONE action per JSON response.
- Verify the previous action succeeded before moving to the next step.
- Use win_key + type_text + press_key("Enter") to open desktop apps. Ensure you type the full exact name if searching.
- Always end with the "done" type and a spoken summary for the user.

SAFEGUARDS (CRITICAL):
1. NEVER guess coordinates. Only click on coordinates EXPLICITLY provided by the vision module. If the target is not found, DO NOT click randomly.
2. Verify the Active Window: If the vision module reports that an unintended application is open (e.g. Copilot, Claude) when you intended to open a different app (e.g. Discord), you MUST recognize this. Stop and declare failure, or press Window key again to restart the search. DO NOT interact with unintended applications.
3. Stay strictly on task. Do NOT click or open anything irrelevant to the user's goal.
4. If you are lost, stuck, or unsure what to do, use {"type": "speak", "text": "I need help finding the right element."} or {"type": "done", "message": "Failed to complete task."} to cleanly exit.
5. STOP ON COMPLETION: The MOMENT you verify that the User Goal has been achieved, you MUST immediately output the "done" action to stop. Do not take any extra unnecessary actions.
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
                max_tokens=500,
            )
            screen_description = vision_response.choices[0].message.content.strip()
            print(f"\n[Vision Model Screen Description]:\n{screen_description}\n")
            
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

            print(f"\n[Decision] Prompting Decision Model with Goal: '{instruction}'")
            print(f"       and Messages Context: {json.dumps(decision_messages[-1]['content'])[:100]}...\n")
            
            response = decision_client.chat.completions.create(
                model="deepseek-ai/DeepSeek-V3-0324",  # HuggingFace-style ID required by Featherless
                messages=decision_messages,
                max_tokens=256,
            )
            
            raw_content = response.choices[0].message.content.strip()
            print(f"\n[Decision Model Raw Response]:\n{raw_content}\n")
            
            # Clean up potential markdown formatting and extra conversational text
            start_idx = raw_content.find('{')
            end_idx = raw_content.rfind('}')
            if start_idx != -1 and end_idx != -1:
                raw_content = raw_content[start_idx:end_idx+1]
                
            raw_content = raw_content.strip()
            action = json.loads(raw_content)
            print(f"[Parsed Action]: {json.dumps(action)}")
            
            if update_log_callback:
                update_log_callback(f"🤖 Agent: {json.dumps(action)}")

            if action["type"] == "done":
                # Play the completion sound
                try:
                    # We might need to initialize pygame mixer here if not already done,
                    # but it's safe to call init() multiple times
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
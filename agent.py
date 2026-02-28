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
from core import tts, state, logger
import pygame
from PIL import Image, ImageDraw

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
You will also be told the user's current goal.

Your job is to provide a highly detailed, accessibility-focused text description of the screen.
Describe the layout, all visible windows, and specifically list every interactable element (buttons, text fields, links, icons) and their general locations on the screen.

CRITICAL INSTRUCTIONS:
1. Pay special attention to elements that could help achieve the user's explicit goal.
2. If you don't immediately see the exact button needed for the goal, THINK PROACTIVELY. Identify and list other tools or paths that could help (e.g., if you don't see a song, look for and label the "Search" bar or "Home" button).
3. For EVERY interactable element you list, you MUST provide its spatial bounding box in normalized [ymin, xmin, ymax, xmax] format out of 1000.
Example: `[150, 400, 200, 500] "Submit" button`
4. Be extremely precise and tight with your bounding boxes. Do not create wide or large boxes that group multiple items together (like entire navigation bars). Output individual, tight boxes for the exact icons, buttons, or inputs.

Do not guess what the user wants to do, just describe what you see accurately based on the prompt.
CRITICAL: Explicitly state the name of the currently active/focused application or window at the beginning of your description.
CRITICAL: You MUST explicitly classify the active window as either a "BROWSER" or a "DESKTOP_APP". To classify as a "BROWSER", the window MUST have typical browser UI elements like a URL address bar at the top, browser tabs, and navigation buttons. If it lacks a URL address bar and tabs, explicitly classify it as a "DESKTOP_APP" (even if it's an app like Spotify or Discord).
"""

SYSTEM_PROMPT = """
You are an AI computer-control agent helping blind users operate their computer by voice.

You will be provided with a text description of the current screen (from your vision module) and the user's goal.
Respond ONLY with a single JSON action object — no prose, no markdown, no fences.

Available actions:
  {"type": "open_url",            "url": "https://...", "context": "browser"}
  {"type": "click_element",       "selector": "exact-text-of-button-or-link", "context": "browser"}
  {"type": "click_box",           "bbox": [150, 400, 200, 500], "context": "os"}
  {"type": "type_text",           "text": "...", "context": "os"}
  {"type": "press_key",           "key": "Enter", "context": "os"}
  {"type": "open_app",            "app": "chrome", "context": "os"}
  {"type": "win_key",             "context": "os"}
  {"type": "wait",                "ms": 1500, "context": "os"}
  {"type": "speak",               "text": "saying something to the user", "context": "os"}
  {"type": "request_user_input",  "prompt": "spoken instruction for the user", "context": "os"}
  {"type": "done",                "message": "summary of what was done", "context": "os"}

Rules:
- Provide a "context" field: use "browser" ONLY if interacting with a web page via open_url or click_element. Use "os" for EVERYTHING else (Desktop apps, Discord, Settings, etc).
  * CRITICAL CONTEXT CHECK: If the vision description classifies the active application as a "DESKTOP_APP" (like Spotify, Discord, VS Code), YOU MUST use "os" context and OS actions.
  * ONLY use "browser" and browser actions if the active application is explicitly classified as a "BROWSER" (has a URL bar) AND you are on a webpage.
- OS Navigation (coordinates): For ANY element in a DESKTOP_APP, you MUST use {"type": "click_box", "bbox": [ymin, xmin, ymax, xmax], "context": "os"}. Copy the EXACT bounding box array provided by the vision module. DO NOT calculate coordinates yourself. DO NOT use click_element for desktop apps.
- Web Browser Navigation (Text Matching): If acting inside a BROWSER web page, 
  DO NOT use click_xy. Instead, use click_element and pass the EXACT text of the button or link as the 'selector', with "context": "browser".
Rules — follow this decision tree EVERY time:

STEP 1: Is the goal to visit a website or use a web service (Google Docs, Gmail, YouTube, any .com)?
  → YES: Immediately use open_url with the correct URL. DO NOT use win_key, open_app, or search.
         Examples: Google Docs → https://docs.google.com, Gmail → https://mail.google.com
  → NO: Continue to Step 2.

STEP 2: Is a browser already open and is the current screen a web page?
  → YES: Use click_element with the EXACT visible text of the button or link.
         NEVER use click_xy inside a browser page.
  → NO: Continue to Step 3.

STEP 3: This is an OS/desktop task.
  - To open OR switch to a desktop app (Spotify, Discord, Notepad, etc.):
      * ALWAYS use {"type": "open_app", "app": "<name>", "context": "os"}.
      * This works whether the app is already running or not — it will bring it to focus or launch it.
      * NEVER click a taskbar icon. NEVER click a desktop shortcut. NEVER use win_key manually for this.
  - To click a native UI element inside an already-open app: use the "click_box" action with the exact bounding box array from the vision description.
  - SPECIFICITY RULE: If the user requests a specific item (e.g., a specific song, file, or contact), you MUST click the specific row or text for that item. DO NOT click a generic "Play" button for the whole album/playlist/page unless it is the only option.
  - To type text: type_text with context "os".

AUTH RULE (HIGHEST PRIORITY — overrides everything else):
If the screen shows ANY of these: sign-in form, login page, account picker, "Create account" button,
"Sign in", "Log in", "Email or phone", "Choose an account", "Continue with Google", "Forgot password",
or any page where credentials are required — output request_user_input IMMEDIATELY.
NEVER click "Create account". NEVER output done with a failure. ALWAYS hand off to the user.
Example: {"type": "request_user_input", "prompt": "I see a sign-in page. Please sign in in the browser window, then press the hotkey and say OK when you're done.", "context": "os"}

General:
- Return exactly ONE action per JSON response.
- Verify the previous action succeeded before moving to the next step.
- Use the "open_app" action to open desktop apps or switch to them if they're already running. Ensure you provide the app name.
- Always end with the "done" type and a spoken summary for the user.

SAFEGUARDS (CRITICAL):
1. NEVER guess coordinates. Only click on coordinates EXPLICITLY provided by the vision module. If the target is not found, DO NOT click randomly.
2. NEVER click the Windows taskbar (the bar at the bottom of the screen with app icons). To open or switch to any app, you MUST use the "open_app" action — not click_box on a taskbar icon.
3. Verify the Active Window: If the vision module reports that an unintended application is open (e.g. Copilot, Claude) when you intended to open a different app (e.g. Discord), you MUST recognize this. Stop and declare failure, or press Window key again to restart the search. DO NOT interact with unintended applications.
4. Stay strictly on task. Do NOT click or open anything irrelevant to the user's goal.
5. If you are lost, stuck, or unsure what to do, use {"type": "speak", "text": "I need help finding the right element."} or {"type": "done", "message": "Failed to complete task."} to cleanly exit.
6. STOP ON COMPLETION: The MOMENT you verify that the User Goal has been achieved, you MUST immediately output the "done" action to stop. Do not take any extra unnecessary actions.
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
    logger.log_session_start(instruction)
    if update_log_callback:
        update_log_callback(f"🎤 You: {instruction}")

    decision_messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {"role": "user",   "content": f"User Goal: {instruction}\nWhat's next?"}
    ]
    just_resumed_from_input = False  # tracks when we need to append instead of overwrite

    for iteration in range(15):  # max 15 iterations safety cap
        if update_log_callback:
            update_log_callback(f"[Step {iteration + 1}/15]")
        # 1. Take screenshot of current OS state
        screenshot_b64 = os_control.take_screenshot(step=iteration + 1)

        # 2. Get Vision Description from Gemini
        try:
            if update_log_callback:
                update_log_callback("[System] Analyzing screen...")
            
            print("\n[Vision] Prompting Vision Model with screen snapshot...")
            vision_messages = [
                {"role": "system", "content": VISION_PROMPT.strip()},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"User Goal: {instruction}\nDescribe this screen and list the bounding boxes for any relevant elements to help achieve this goal."},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{screenshot_b64}"
                        }}
                    ]
                }
            ]
            logger.log_llm_prompt("google/gemini-3-flash-preview", vision_messages)
            vision_response = vision_client.chat.completions.create(
                model="google/gemini-3-flash-preview",
                messages=vision_messages,
                max_tokens=800,
            )
            screen_description = vision_response.choices[0].message.content.strip()
            logger.log_llm_response("google/gemini-3-flash-preview", screen_description)
            print(f"\n[Vision Model Screen Description]:\n{screen_description}\n")
            
            # Debug: Draw bounded boxes on the screenshot
            try:
                import os
                os.makedirs("debug", exist_ok=True)
                step_num = iteration + 1
                debug_img = Image.open(f"debug/step_{step_num}_original.png")
                draw = ImageDraw.Draw(debug_img)
                w, h = debug_img.size
                
                boxes = re.finditer(r'\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]\s*(.*)', screen_description)
                for match in boxes:
                    ymin, xmin, ymax, xmax = map(int, match.groups()[:4])
                    label = match.group(5)[:40] # Truncate long labels
                    
                    ry1, rx1 = int((ymin/1000) * h), int((xmin/1000) * w)
                    ry2, rx2 = int((ymax/1000) * h), int((xmax/1000) * w)
                    
                    draw.rectangle([rx1, ry1, rx2, ry2], outline="red", width=2)
                    draw.text((rx1, max(0, ry1 - 12)), label, fill="red")
                    
                debug_img.save(f"debug/step_{step_num}_vision_boxes.png")
                print(f"\n[Debug] Saved debug/step_{step_num}_original.png and debug/step_{step_num}_vision_boxes.png\n")
            except Exception as e:
                print(f"\n[Debug] Failed to save vision boxes: {e}\n")
            
        except Exception as e:
            error_msg = f"Error in vision module: {str(e)}"
            logger.log_error(error_msg)
            if update_log_callback:
                update_log_callback(error_msg)
            print(error_msg)
            logger.log_session_end("Task failed at vision step due to an error.")
            return "Task failed at vision step due to an error."

        # 3. Add vision context to decision messages
        screen_update = f"Current Screen Description:\n{screen_description}\n\nUser Goal: {instruction}\nWhat should I do next? (Reply only with JSON)"
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
            logger.log_llm_prompt("deepseek-ai/DeepSeek-V3-0324", decision_messages)

            response = decision_client.chat.completions.create(
                model="deepseek-ai/DeepSeek-V3-0324",  # HuggingFace-style ID required by Featherless
                messages=decision_messages,
                max_tokens=256,
            )

            raw_content = response.choices[0].message.content.strip()
            logger.log_llm_response("deepseek-ai/DeepSeek-V3-0324", raw_content)
            print(f"\n[Decision Model Raw Response]:\n{raw_content}\n")
            
            # Clean up potential markdown formatting and extra conversational text
            start_idx = raw_content.find('{')
            end_idx = raw_content.rfind('}')
            if start_idx != -1 and end_idx != -1:
                raw_content = raw_content[start_idx:end_idx+1]
                
            raw_content = raw_content.strip()
            
            # DeepSeek sometimes hallucinates and outputs Python dicts (single quotes) instead of JSON
            try:
                action = json.loads(raw_content)
            except json.JSONDecodeError:
                import ast
                action = ast.literal_eval(raw_content) # Safely parse python dict with single quotes
                
            print(f"\n[Agent Thought]: {action.get('thought', 'No thought provided.')}")
            print(f"[Parsed Action]: {json.dumps(action)}")
            
            if update_log_callback:
                update_log_callback(f"� Agent Thought: {action.get('thought', 'Deciding...')}")
                update_log_callback(f"🤖 Agent Action: {action.get('type')}")

            logger.log_action(action)

            if action["type"] == "done":
                # Play the completion sound
                try:
                    # We might need to initialize pygame mixer here if not already done,
                    # but it's safe to call init() multiple times
                    pygame.mixer.init()
                    pygame.mixer.music.load("sounds/Note_block_chime_scale.ogg")
                    pygame.mixer.music.play()
                except Exception as e:
                    print(f"Failed to play finish sound: {e}")

                msg = action.get("message", "Done.")
                logger.log_session_end(msg)
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

                # Inject reply — strongly tell DeepSeek the step is complete and to move forward
                decision_messages.append({"role": "assistant", "content": json.dumps(action)})
                decision_messages.append({"role": "user", "content": f"User confirmed (said: '{reply}'). ASSUME THIS STEP IS COMPLETE. Proceed to the NEXT action toward the goal. Do NOT use request_user_input again."})
                just_resumed_from_input = True  # next iteration must APPEND screen, not overwrite
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
            logger.log_error(error_msg)
            if update_log_callback:
                update_log_callback(error_msg)
            print(error_msg)
            logger.log_session_end("Task failed due to an error.")
            return "Task failed due to an error."

    logger.log_session_end("Maximum steps reached.")
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
    
    # fake_transcript = "Open Discord and send a message to Sharanya saying Hi"
    fake_transcript = "Open Spotify and play WHAT IS LOVE by TWICE"
    print(f"Submitting Fake Transcript: '{fake_transcript}'\n")
    
    try:
        # Pass it straight into the refactored reasoning loop!
        final_message = run_agent(fake_transcript, update_log_callback=console_logger)
        print("\nAgent finished! Final Message:", final_message)
        
    except Exception as e:
        print("\nAgent crashed during testing:", str(e))
    
    print("\nTest complete.")
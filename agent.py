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

import traceback

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    pass

# Global Playwright state
browser_instance = None
browser_context = None

def get_browser_page():
    """Aggressively attaches to the running Chrome instance over CDP to extract DOM, clearing dead caches."""
    global browser_instance, browser_context
    
    # 1. Test existing connection or reset it
    try:
        if browser_instance is not None:
            if not browser_instance.is_connected() or len(browser_instance.contexts) == 0:
                raise Exception("Connection is dead")
    except Exception:
        browser_instance = None
        browser_context = None

    # 2. Attach if no valid connection exists
    if browser_instance is None:
        try:
            p = sync_playwright().start()
            browser_instance = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as e:
            return None # Expected if Chrome isn't open with debugging ports

    # 3. Always fetch the most recent active page
    try:
        contexts = browser_instance.contexts
        if not contexts: return None
        pages = contexts[0].pages
        if not pages: return None
        return pages[-1] # Return the last active page
    except Exception:
        browser_instance = None
        return None

VISION_PROMPT = """
You are acting as the eyes for an AI computer-control agent.
You will be shown a screenshot of the user's current screen.
You will also be told the user's current goal.

Your job is to provide a highly detailed, accessibility-focused text description of the screen.
Describe the layout, all visible windows, and specifically list every interactable element (buttons, text fields, links, icons) and their general locations on the screen.

CRITICAL INSTRUCTIONS:
1. Pay special attention to elements that could help achieve the user's explicit goal.
2. If you don't immediately see the exact button needed for the goal, THINK PROACTIVELY. Identify and list other tools or paths that could help (e.g., if you don't see a song, look for and label the "Search" bar or "Home" button).
3. EXHAUSTIVE ROW DETAILS: When looking at lists or rows (like song tracks, files, or messages), you MUST identify and bound EVERY single icon or action button inside that row. Do not just box the entire row and ignore the buttons inside it! Find and separately label the "Play" button, "Add to Playlist", "Heart", "Three dots", or any other interactable elements within that specific row.
4. For EVERY interactable element you list, you MUST provide its spatial bounding box in normalized [ymin, xmin, ymax, xmax] format out of 1000.
Example: `[150, 400, 200, 500] "Submit" button`
5. Be extremely precise and tight with your bounding boxes. Do not create wide or large boxes that group multiple items together (like entire navigation bars). Output individual, tight boxes for the exact icons, buttons, or inputs.

Do not guess what the user wants to do, just describe what you see accurately based on the prompt.
CRITICAL: Explicitly state the name of the currently active/focused application or window at the beginning of your description.
CRITICAL: You MUST explicitly classify the active window as either a "BROWSER" or a "DESKTOP_APP". To classify as a "BROWSER", the window MUST have typical browser UI elements like a URL address bar at the top, browser tabs, and navigation buttons. If it lacks a URL address bar and tabs, explicitly classify it as a "DESKTOP_APP" (even if it's an app like Spotify or Discord).
"""

SYSTEM_PROMPT = """
You are an AI computer-control agent helping blind users operate their computer by voice.
You will be provided with a text description of the current screen (from your vision module) and the user's goal.
Respond ONLY with a single JSON action object — no prose, no markdown, no fences.

Available actions (must include a "thought" explaining your plan):
  {"thought": "I need to click the search bar...", "type": "click_box", "bbox": [100, 200, 150, 250], "context": "os"}
  {"thought": "Typing the URL into the bar...", "type": "type_text", "text": "...", "context": "os"}
  {"thought": "Pressing Enter to search...", "type": "press_key", "key": "Enter", "context": "os"}
  {"thought": "Opening Run dialog...", "type": "press_shortcut", "keys": ["win", "r"], "context": "os"}
  {"thought": "Waiting for app to load...", "type": "wait", "ms": 1500, "context": "os"}
  {"thought": "Saying hello...", "type": "speak", "text": "saying something to the user", "context": "os"}
  {"thought": "Need user to log in...", "type": "request_user_input", "prompt": "spoken instruction for the user", "context": "os"}
  {"thought": "Goal achieved.", "type": "done", "message": "summary of what was done", "context": "os"}

Rules:
- Provide a "context" field: use "browser" ONLY if interacting with a web page via open_url or click_element. Use "os" for EVERYTHING else (Desktop apps, Discord, Settings, etc).
  * CRITICAL CONTEXT CHECK: If the vision description classifies the active application as a "DESKTOP_APP", YOU MUST use "os" context and OS actions.
  * ONLY use "browser" and browser actions if the active application is explicitly classified as a "BROWSER" (has a URL bar) AND you are on a webpage.
- OS Navigation (coordinates): For ANY element in a DESKTOP_APP, you MUST use {"type": "click_box", "bbox": [ymin, xmin, ymax, xmax], "context": "os"}. Copy the EXACT bounding box array provided by the vision module. DO NOT calculate coordinates yourself. DO NOT use click_element for desktop apps.
- Web Browser Navigation (Text Matching): If acting inside a BROWSER web page, 
  DO NOT use click_xy. Instead, use click_element and pass the EXACT text of the button or link as the 'selector', with "context": "browser".
Rules — follow this decision tree EVERY time:

1. BROWSING THE WEB:
   - To open the browser (if not already open), you MUST use the `{"type": "open_app", "app": "chrome", "context": "os"}` action. This will automatically launch Chrome with debugging ports enabled and maximize the window for you.
   - To navigate to a website: ALWAYS focus the address bar first by using `{"type": "press_shortcut", "keys": ["ctrl", "l"], "context": "os"}`.
   - After focusing the address bar, use `type_text` to type the URL (e.g., "example.com"). NOTE: `type_text` DOES NOT press Enter automatically! You MUST follow it up with a separate `press_key` action for "enter" to submit, or use a batch array: `[{"type": "type_text", "text": "example.com", "context": "os"}, {"type": "press_key", "key": "enter", "context": "os"}]`. You must navigate to the main site and click through the GUI.
   - NEVER guess coordinates. EVERY action requires the Vision module to give you the exact coordinate bounding box.

2. OS & DESKTOP TASKS:
   - To click ANY native element, web button, or UI element: use `click_box`.
   - The Vision model provides bounding boxes in the format `[ymin, xmin, ymax, xmax]`.
   - Just directly copy the exact array into the "bbox" field.
   - Windows Search results: ALWAYS use `click_box` to click them based on the vision module.
   - To maximize a window, use: `{"thought": "Maximizing...", "type": "maximize_window", "context": "os"}`
   
3. BROWSER DOM NAVIGATION (If inside a browser):
   - You can use Playwright's `click_element` Action to click exact text on a webpage instead of `click_box` coordinates if you want to ensure accuracy.
   - {"thought": "Clicking the Sign In button...", "type": "click_element", "selector": "Sign In", "context": "browser"}
   
4. BATCHING ACTIONS:
   - You can output a single action JSON object OR an array of action objects `[{}, {}]` to execute multiple steps quickly without waiting for a new screenshot (e.g., win_key -> type_text "chrome" -> press_key "Enter").

STEP 3: This is an OS/desktop task.
  - To open OR switch to a desktop app:
      * ALWAYS use {"type": "open_app", "app": "<app_name>", "context": "os"}.
      * This works whether the app is already running or not — it will bring it to focus or launch it.
      * NEVER click a taskbar icon. NEVER click a desktop shortcut. NEVER use win_key manually for this.
  - To click a native UI element inside an already-open app: use the "click_box" action with the exact bounding box array from the vision description.
  - SPECIFICITY RULE: If the user requests a specific item (e.g., a specific song, file, or contact), you MUST click the specific row or text for that item. DO NOT click a generic "Play" button for the whole album/playlist/page unless it is the only option.
  - To type text: type_text with context "os".

AUTH RULE:
If the screen is EXPLICITLY a login page actively requesting credentials (e.g., you see input text boxes specifically labeled for "Email", "Username", or "Password"), you must immediately output `request_user_input`.
DO NOT trigger this rule just because there is a "Login" or "Sign In" button tucked in the corner of a generic homepage. ONLY trigger it if the main content of the screen is an active form waiting for the user's password.
NEVER click "Create account" unless explicitly requested by the user. NEVER output done with a failure just because of a login wall. ALWAYS hand off to the user.
Example: {"type": "request_user_input", "prompt": "Please sign in to the active window, then press the hotkey and say Okay when you're done.", "context": "os"}

SAFEGUARDS (CRITICAL):
1. NEVER guess coordinates. Only click on coordinates EXPLICITLY provided by the vision module. If the target is not found, DO NOT click randomly.
2. NEVER click the Windows taskbar (the bar at the bottom of the screen with app icons). To open or switch to any app, you MUST use the "open_app" action — not click_box on a taskbar icon.
3. Verify the Active Window: If the vision module reports that an unintended application is open (e.g. Copilot, Claude) when you intended to open a different app (e.g. Discord), you MUST recognize this. Stop and declare failure, or press Window key again to restart the search. DO NOT interact with unintended applications.
4. Stay strictly on task. Do NOT click or open anything irrelevant to the user's goal.
5. If you are lost, stuck, or unsure what to do, use {"type": "speak", "text": "I need help finding the right element."} or {"type": "done", "message": "Failed to complete task."} to cleanly exit.
6. STOP ON COMPLETION: The MOMENT you verify that the User Goal has been achieved (e.g., you successfully clicked the specific requested song, the music player is active, the email sent, etc.), you MUST immediately output the `done` action to stop.
7. DO NOT DOUBLE-PRESS: If your last action was to click "Play" on a song, assume it is now playing! If you click it a second time, you will pause it. STOP immediately and output {"type": "done", "message": "Goal achieved", "context": "os"}.

General:
- Verify the previous action succeeded before moving to the next step.
- Use the "open_app" action to open desktop apps or switch to them if they're already running. Ensure you provide the app name.
- Typing into search bars: If you use `type_text`, it ONLY types the characters. If you need to submit the search or press Enter, you MUST output a second action: `{"type": "press_key", "key": "Enter", "context": "browser"}` (or "os"). Batch them together when possible: `[{"type": "type_text", "text": "foo", "context": "browser"}, {"type": "press_key", "key": "Enter", "context": "browser"}]`.
- NEVER click dropdown search suggestions: When typing into a search bar (like Google or Discord), DO NOT use `click_element` or `click_box` to try and click the suggested autocomplete text that drops down. Always just press "Enter".
- Always end with the "done" type and a spoken summary for the user.

SAFEGUARDS (CRITICAL):
1. NEVER guess coordinates. Only click on coordinates EXPLICITLY provided by the vision module. If the target is not found, DO NOT click randomly.
2. FATAL ERROR: NEVER click the Windows taskbar (the bar at the bottom of the screen with app icons). The icons are too small and you will misclick and launch the wrong app. To open or switch to any app, you MUST use the `{"type": "open_app", "app": "name", "context": "os"}` action. Any attempt to `click_box` a taskbar icon is a catastrophic failure.
3. Verify the Active Window: If the vision module reports that an unintended application is open when you intended to open a different app, you MUST recognize this. Stop and declare failure, or use `open_app` again. DO NOT interact with unintended applications.
4. Stay strictly on task. Do NOT click or open anything irrelevant to the user's goal.
5. If you are lost, stuck, or unsure what to do, use {"type": "speak", "text": "I need help finding the right element."} or {"type": "done", "message": "Failed to complete task."} to cleanly exit.
6. STOP ON COMPLETION: The MOMENT you verify that the FULL User Goal has been achieved, you MUST immediately output the "done" action to stop. 
   - However, DO NOT output "done" prematurely. If the user asked to "open" a web app (like Discord or Spotify), simply navigating to their homepage is NOT the final step. You must click the button to actually launch the web app/client before outputting done.
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
                    
                    y0, y1 = sorted([ry1, ry2])
                    x0, x1 = sorted([rx1, rx2])
                    
                    draw.rectangle([x0, y0, x1, y1], outline="red", width=2)
                    draw.text((x0, max(0, y0 - 12)), label, fill="red")
                    
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
            logger.log_llm_prompt("deepseek-ai/DeepSeek-V3-0324", decision_messages)

            response = decision_client.chat.completions.create(
                model="deepseek-ai/DeepSeek-V3-0324",  # HuggingFace-style ID required by Featherless
                messages=decision_messages,
                max_tokens=1500,
            )

            raw_content = response.choices[0].message.content.strip()
            logger.log_llm_response("deepseek-ai/DeepSeek-V3-0324", raw_content)
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
            
            # DeepSeek sometimes hallucinates and outputs Python dicts (single quotes) instead of JSON
            try:
                action_data = json.loads(raw_content)
            except json.JSONDecodeError:
                import ast
                action_data = ast.literal_eval(raw_content) # Safely parse python dict with single quotes
                
            # Convert single action to a list for uniform processing
            actions = action_data if isinstance(action_data, list) else [action_data]
            
            for action in actions:
                print(f"\n[Agent Thought]: {action.get('thought', 'No thought provided.')}")
                print(f"[Parsed Action]: {json.dumps(action)}")
                
                if update_log_callback:
                    update_log_callback(f"🧠 Agent Thought: {action.get('thought', 'Deciding...')}")
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
                        update_log_callback(f"🤖 Action: {action.get('type')}")
            # Support batching (an array of actions) or a single action
            actions = action_data if isinstance(action_data, list) else [action_data]
            
            for action in actions:
                print(f"\n[Agent Thought]: {action.get('thought', 'No thought provided.')}")
                print(f"[Parsed Action]: {json.dumps(action)}")
                
                if update_log_callback:
                    update_log_callback(f"🧠 Agent Thought: {action.get('thought', 'Deciding...')}")
                    update_log_callback(f"🤖 Agent Action: {action.get('type')}")
    
                logger.log_action(action)
    
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
                    break  # Break out of the action batch to give control back to the perception loop
    
                # 5. Execute OS/Browser action
                active_page = None
                if action["type"] == "click_element" or action.get("context") == "browser":
                    active_page = get_browser_page()
                    
                execute_action(action, page=active_page)
                time.sleep(0.2)  # Short sleep between batched actions so OS UI can catch up
            
            if just_resumed_from_input:
                continue

            # Append the cycle log (the whole batch)
            decision_messages.append({"role": "assistant", "content": json.dumps(action_data)})
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
    fake_transcript = "Open Google Docs in the browser and create a new blank document."
    
    # fake_transcript = "Open Discord and send a message to Sharanya saying Hi"
    # fake_transcript = "Open Spotify and play WHAT IS LOVE by TWICE"
    print(f"Submitting Fake Transcript: '{fake_transcript}'\n")
    
    try:
        # Pass it straight into the refactored reasoning loop!
        final_message = run_agent(fake_transcript, update_log_callback=console_logger)
        print("\nAgent finished! Final Message:", final_message)
        
    except Exception as e:
        print("\nAgent crashed during testing:", str(e))
    
    print("\nTest complete.")
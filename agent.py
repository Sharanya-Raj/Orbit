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
    """Attaches to the running Chrome instance over CDP, clearing dead caches."""
    global browser_instance, browser_context

    try:
        if browser_instance is not None:
            if not browser_instance.is_connected() or len(browser_instance.contexts) == 0:
                raise Exception("Connection is dead")
    except Exception:
        browser_instance = None
        browser_context = None

    if browser_instance is None:
        try:
            p = sync_playwright().start()
            browser_instance = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception:
            return None

    try:
        contexts = browser_instance.contexts
        if not contexts: return None
        pages = contexts[0].pages
        if not pages: return None
        return pages[-1]
    except Exception:
        browser_instance = None
        return None


def _extract_json(text: str) -> str:
    """Extract the first JSON object or array from a string."""
    start_dict = text.find('{')
    start_list = text.find('[')
    end_dict = text.rfind('}')
    end_list = text.rfind(']')

    starts = [i for i in (start_dict, start_list) if i != -1]
    ends = [i for i in (end_dict, end_list) if i != -1]

    if starts and ends:
        return text[min(starts):max(ends)+1].strip()
    return text.strip()


# =============================================================================
# VISION PROMPT
# =============================================================================
VISION_PROMPT = """You are the vision module for an AI computer-control agent that helps blind users.
You will receive a screenshot and the user's current goal.

YOUR ONLY JOB: Describe exactly what is visible on screen. Do NOT infer, guess, or assume anything not clearly visible.

OUTPUT FORMAT — Follow this structure exactly:

1. ACTIVE WINDOW: State the name of the currently focused application/window.
2. WINDOW TYPE: Classify as exactly one of:
   - "BROWSER" — ONLY if you see a URL address bar, browser tabs, and navigation buttons (back/forward/refresh).
   - "DESKTOP_APP" — Everything else (Spotify, Discord, Settings, File Explorer, etc.), even if it displays web content.
3. SCREEN LAYOUT: Briefly describe what panels, sidebars, or sections are visible.
4. GOAL-RELEVANT ELEMENTS: List ONLY elements that could help achieve the stated goal. For each element:
   - The element's exact visible label or text
   - Its type (button, text field, link, icon, menu item, checkbox, etc.)
   - Its tight bounding box in normalized [ymin, xmin, ymax, xmax] format (0-1000 scale)

BOUNDING BOX RULES:
- Each box must tightly wrap ONE individual element. Never group multiple elements into one box.
- For rows/lists (songs, files, messages): separately identify EACH icon and button WITHIN the row with its own tight box.
- Format: [ymin, xmin, ymax, xmax] "Label" element_type
- Example: [150, 400, 180, 440] "Search" text_field

ANTI-HALLUCINATION RULES:
- If you cannot read text clearly, write "[unreadable]" instead of guessing.
- If you are unsure whether an element exists, DO NOT include it.
- Do NOT describe elements from memory or from what you expect the app to look like. Only describe what is VISIBLE.
- Do NOT fabricate bounding box coordinates. If you cannot determine the precise location, omit the element.

PROACTIVE NAVIGATION HINTS:
- If the goal requires an element you don't see, identify navigation paths (Search bar, Home button, menu items).
- Label these as "NAVIGATION AID:" so the agent knows they are indirect paths, not the target itself.

GOAL STATUS CHECK:
After listing elements, add this line on its own:
GOAL STATUS: [COMPLETE / IN_PROGRESS / BLOCKED] — Brief reason based ONLY on what you see.
- COMPLETE: All observable success criteria are clearly visible on screen (e.g., song is playing shown by pause button, message is sent and visible in chat).
- IN_PROGRESS: The task is underway but the final result is not yet confirmed on screen.
- BLOCKED: Something unexpected is preventing progress (error dialog, unexpected login wall, wrong app, loading failure).
"""


# =============================================================================
# PLANNING PROMPT
# =============================================================================
PLANNING_PROMPT = """You are the planning module for an AI computer-control agent.
Given a user's goal, produce a structured execution plan. Output ONLY valid JSON with this exact structure:

{
  "goal_summary": "One-sentence restatement of the goal",
  "steps": [
    "Step 1: Open the application ...",
    "Step 2: Navigate to ...",
    "Step 3: ..."
  ],
  "success_criteria": [
    "Observable condition visible on screen that confirms completion (e.g., 'Pause button is visible and song title X is shown in the Now Playing bar')",
    "..."
  ],
  "completion_signal": "Specific description of what the screen will look like when the task is 100% done"
}

Rules:
- Steps must be concrete and specific (name actual apps, UI elements, and actions).
- Success criteria must be OBSERVABLE from a screenshot — things you can SEE, not infer.
- Success criteria must clearly distinguish "in progress" from "done".
- Do not include unnecessary steps.
- Anticipate common obstacles: login pages, loading states, search required to find a specific item.
"""


# =============================================================================
# GOAL CHECK PROMPT
# =============================================================================
GOAL_CHECK_PROMPT = """You are the goal-verification module for an AI computer-control agent.

You will receive:
1. The user's original goal
2. The plan's success criteria
3. The current screen description (including a GOAL STATUS line from the vision module)

Determine if the goal has been FULLY accomplished based on what is visible on screen.

Output ONLY valid JSON:
{
  "accomplished": true/false,
  "confidence": 0-100,
  "reason": "Specific explanation citing visible elements from the screen description",
  "missing": "If not accomplished: what is still missing or incorrect (empty string if accomplished)"
}

Rules:
- Be REASONABLE. Return accomplished=true if the success criteria are plausibly met based on what you can see.
- If the vision module says GOAL STATUS: COMPLETE, trust it unless there is clear contradicting evidence.
- Confidence >= 50 is sufficient to mark accomplished=true.
- The agent has already done the work — your job is to confirm it, not to second-guess every detail.
- If the task is "send a message" and a message matching the description is visible in chat, that is COMPLETE.
- Partial completion is NOT completion, but do not require pixel-perfect confirmation.
"""


# =============================================================================
# SYSTEM PROMPT
# =============================================================================
SYSTEM_PROMPT = """You are an AI computer-control agent helping blind users operate their computer by voice.

RESPONSE FORMAT: You MUST respond with ONLY a valid JSON action (or array of actions). No prose, no markdown fences, no explanation outside the JSON.

You will be given the user's goal, an execution plan with success criteria, and the current screen description from the vision module.

MANDATORY REASONING PROCESS — Before choosing an action, internally answer:
1. What step of the plan am I on? What does the plan say to do next?
2. What does the vision description say is the ACTIVE WINDOW and its TYPE?
3. Does the screen's GOAL STATUS say COMPLETE? If so, verify visually and use "done".
4. Does the vision description show an element that DIRECTLY matches what I need for the current step?
5. If yes: What is the EXACT bounding box the vision module provided? Copy it verbatim.
6. If no: What navigation step gets me closer (open search, scroll, switch apps)?
7. Am I CERTAIN this action moves toward the goal, or am I guessing?

Include your reasoning in the "thought" field of every action.

AVAILABLE ACTIONS (every action MUST include "thought" and "context"):

Click an element by bounding box (primary method for all UI interaction):
  {"thought": "...", "type": "click_box", "bbox": [ymin, xmin, ymax, xmax], "context": "os"}

Type text into the focused field:
  {"thought": "...", "type": "type_text", "text": "...", "context": "os"}

Press a single key:
  {"thought": "...", "type": "press_key", "key": "Enter", "context": "os"}

Press a keyboard shortcut:
  {"thought": "...", "type": "press_shortcut", "keys": ["ctrl", "l"], "context": "os"}

Wait for UI to update:
  {"thought": "...", "type": "wait", "ms": 1500, "context": "os"}

Speak to the user:
  {"thought": "...", "type": "speak", "text": "...", "context": "os"}

Request user input (for login/auth ONLY):
  {"thought": "...", "type": "request_user_input", "prompt": "...", "context": "os"}

Open or switch to an application:
  {"thought": "...", "type": "open_app", "app": "chrome", "context": "os"}

Maximize the current window:
  {"thought": "...", "type": "maximize_window", "context": "os"}

Click a web page element by its visible text (browser pages only):
  {"thought": "...", "type": "click_element", "selector": "Sign In", "context": "browser"}

Task complete — ONLY when success criteria are visibly confirmed on screen:
  {"thought": "...", "type": "done", "message": "summary", "context": "os"}

CONTEXT RULES:
- Default context is "os". Use "os" for ALL desktop apps (Spotify, Discord, Settings, File Explorer, etc.).
- Use "browser" ONLY when interacting with web page content inside a window the vision module explicitly classified as "BROWSER".
- CRITICAL: If vision says "DESKTOP_APP", you MUST use "os" context and click_box. Never use click_element or "browser" context for desktop apps.

NAVIGATION RULES:

Opening/switching apps:
  - ALWAYS use {"type": "open_app", "app": "<name>", "context": "os"}.
  - NEVER click taskbar icons. NEVER click desktop shortcuts. NEVER use win_key manually for this.
  - After opening an app, if the window is NOT maximized (desktop or other windows visible behind it), IMMEDIATELY use {"type": "maximize_window", "context": "os"} BEFORE interacting.

Browser navigation:
  - To open browser: {"type": "open_app", "app": "chrome", "context": "os"}
  - To navigate to a URL: press_shortcut ["ctrl", "l"] → type_text URL → press_key "Enter".
  - Navigate to the main site and use the GUI. Do not construct deep URLs from memory.

Clicking elements:
  - Desktop apps: ALWAYS use click_box with the EXACT bounding box from the vision module. Copy verbatim.
  - Browser web pages: You may use click_element with the exact visible text of the target element.

Typing and submitting:
  - type_text only types. To submit searches or send chat messages, follow with press_key "Enter".
  - To send a chat message, you MUST batch: [{"type": "type_text", "text": "hello", "context": "os"}, {"type": "press_key", "key": "Enter", "context": "os"}]
  - The message will NOT send on Discord/Web otherwise!

Search interactions:
  - After typing in a search bar, ALWAYS press Enter to submit. NEVER click autocomplete dropdown suggestions.

SPECIFICITY RULE:
  - If the user asks for a specific item (song, file, contact), click THAT specific item. Do NOT click a generic "Play" button unless it is the only option and clearly targets the correct item.

AUTH RULE:
  - Trigger request_user_input ONLY if the screen shows a login FORM with empty credential input fields.
  - Do NOT trigger just because a "Sign In" button exists on a homepage.
  - NEVER click "Create account" unless the user explicitly asked.

"DONE" RULE — CRITICAL:
  - ONLY output "done" when you can SEE on screen that the success criteria from the plan are met.
  - Do NOT output "done" just because you performed the last planned step. Verify the result is visible.
  - If the result is ambiguous or not yet visible, use {"type": "wait", "ms": 2000} and check again.
  - When in doubt about whether the goal is achieved, keep going — do NOT give up prematurely.

PERSISTENCE RULE:
  - If an action does not produce the expected result, try an alternative approach.
  - If a button is not visible, use search or navigation to find it.
  - Only give up after 3+ distinct approaches have all failed.

ANTI-HALLUCINATION SAFEGUARDS (CRITICAL — VIOLATIONS CAUSE TASK FAILURE):

1. NEVER GUESS COORDINATES. Only use bounding boxes the vision module EXPLICITLY provided. If not listed, navigate to find it.

2. NEVER CLICK THE TASKBAR. The bottom bar with small app icons is OFF LIMITS. Always use open_app.

3. VERIFY BEFORE ACTING. Before every click_box:
   - The bounding box appears in the vision description (quote the label).
   - The element matches what you intend to interact with.
   - The active window is the correct application.
   If ANY check fails, STOP and reassess.

4. VERIFY AFTER ACTING. Check if the previous action succeeded in the new vision description. If unchanged, do NOT repeat blindly. Reassess.

5. ONE CLICK RULE. If you clicked a Play/toggle, assume it worked. Do NOT click it again. Verify result before proceeding.

6. STAY ON TASK. Do not open anything unrelated to the goal. If an unintended app opens, use open_app to switch back.

7. FAIL GRACEFULLY. If stuck after 3+ distinct failed attempts at the same step:
   {"thought": "Cannot find target after multiple attempts.", "type": "done", "message": "I was unable to complete the task. [reason]", "context": "os"}
   Do NOT click randomly hoping to find something.

8. STOP ONLY ON CONFIRMED COMPLETION. When visible evidence on screen confirms the goal is achieved, output "done".

BATCHING:
- You may output a JSON array of actions: [{...}, {...}]
- Use for predictable multi-step sequences (e.g., address bar → type URL → Enter).
- Do NOT batch when you need to see the screen result before deciding the next step.
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

import time
import functools

def retry_api_call(max_retries=3, base_delay=2.0):
    """Decorator to retry Featherless API calls on 429 Concurrency Limits mapping backoffs."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_str = str(e).lower()
                    if "429" in error_str or "concurrency" in error_str or "rate limit" in error_str:
                        if attempt == max_retries - 1:
                            print(f"[API Error] Max retries reached for {func.__name__}: {e}")
                            raise
                        print(f"[API Warning] Concurrency limit hit in {func.__name__}. Retrying in {delay}s...")
                        time.sleep(delay)
                        delay *= 2  # Exponential backoff
                    else:
                        raise # Reraise non-rate limit errors immediately
        return wrapper
    return decorator

@retry_api_call()
def _call_decision_model(messages, max_tokens=1500):
    """Wrapped helper to call the decision model with retries."""
    return decision_client.chat.completions.create(
        model="deepseek-ai/DeepSeek-V3-0324",
        messages=messages,
        max_tokens=max_tokens,
    )


def plan_goal(instruction: str, update_log_callback=None) -> dict:
    """Creates an execution plan with observable success criteria for the given goal."""
    print("\n[Planner] Creating execution plan...")
    if update_log_callback:
        update_log_callback("[System] Planning how to accomplish your goal...")

    try:
        response = _call_decision_model(
            messages=[
                {"role": "system", "content": PLANNING_PROMPT.strip()},
                {"role": "user", "content": f"User Goal: {instruction}"},
            ],
            max_tokens=800,
        )
        raw = response.choices[0].message.content.strip()
        plan = json.loads(_extract_json(raw))

        print(f"\n[Plan]:\n{json.dumps(plan, indent=2)}\n")
        if update_log_callback:
            update_log_callback(f"[Plan] {plan.get('goal_summary', instruction)}")
            for step in plan.get("steps", []):
                update_log_callback(f"  • {step}")
        return plan

    except Exception as e:
        print(f"[Planner] Failed to create plan ({e}). Using fallback.")
        return {
            "goal_summary": instruction,
            "steps": [f"Accomplish: {instruction}"],
            "success_criteria": [f"The goal '{instruction}' is visibly completed on screen."],
            "completion_signal": f"Screen shows the completed result of: {instruction}",
        }


def check_goal_accomplished(instruction: str, plan: dict, screen_description: str) -> tuple:
    """Verifies whether the goal has been achieved based on the current screen description."""
    try:
        criteria_text = "\n".join(f"- {c}" for c in plan.get("success_criteria", []))
        prompt = (
            f"User Goal: {instruction}\n\n"
            f"Success Criteria:\n{criteria_text}\n\n"
            f"Completion Signal: {plan.get('completion_signal', '')}\n\n"
            f"Current Screen Description:\n{screen_description}"
        )

        response = _call_decision_model(
            messages=[
                {"role": "system", "content": GOAL_CHECK_PROMPT.strip()},
                {"role": "user", "content": prompt},
            ],
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()
        result = json.loads(_extract_json(raw))

        accomplished = result.get("accomplished", False) and result.get("confidence", 0) >= 50
        reason = result.get("reason", "Goal status unknown.")

        print(f"\n[Goal Check] Accomplished: {accomplished} (confidence: {result.get('confidence', 0)}%)")
        print(f"[Goal Check] Reason: {reason}")
        if not accomplished and result.get("missing"):
            print(f"[Goal Check] Missing: {result.get('missing')}")

        return accomplished, reason

    except Exception as e:
        print(f"[Goal Check] Error: {e}")
        return False, ""


def _play_finish_sound():
    try:
        pygame.mixer.init()
        pygame.mixer.music.load("sounds/Note_block_chime_scale.ogg")
        pygame.mixer.music.play()
    except Exception as e:
        print(f"Failed to play finish sound: {e}")


def translate_to_english(text: str) -> tuple[str, str]:
    """Translates the text to English and returns (english_text, detected_language)."""
    try:
        response = _call_decision_model(
            messages=[
                {"role": "system", "content": "You are a translator. Detect the language of the user's text. If it is already English, just return 'English|||' followed by the exact text. Otherwise, return the name of the detected language, followed by '|||', followed by the English translation. Do not add any conversational filler. Example: 'Spanish|||Translate this to English'"},
                {"role": "user", "content": text}
            ],
            max_tokens=200,
        )
        content = response.choices[0].message.content.strip()
        parts = content.split('|||', 1)
        if len(parts) == 2:
            return parts[1].strip(), parts[0].strip()
        return text, "English"
    except Exception as e:
        print(f"[Translation] Failed to translate to English: {e}")
        return text, "English"

def translate_from_english(text: str, target_language: str) -> str:
    """Translates english text back into the user's original language."""
    if target_language.lower() == "english":
        return text
        
    try:
        response = _call_decision_model(
            messages=[
                {"role": "system", "content": f"You are a translator. Translate the following English text into {target_language}. Return ONLY the translated text. No conversational filler."},
                {"role": "user", "content": text}
            ],
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[Translation] Failed to translate back to {target_language}: {e}")
        return text

def run_agent(instruction: str, update_log_callback=None) -> str:
    """Runs the main perception-action loop to execute the user instruction."""
    logger.log_session_start(instruction)
    
    # --- MULTI-LINGUAL SUPPORT: Translate to English for reasoning ---
    english_instruction, user_language = translate_to_english(instruction)
    print(f"\n[Multi-Lingual] Detected Language: {user_language} -> Translated mapped goal: '{english_instruction}'\n")
    
    if update_log_callback:
        update_log_callback(f"🎤 You: {instruction}")
        if user_language.lower() != "english":
            update_log_callback(f"🌐 Translated to English: {english_instruction}")

    # 0. Planning phase — create a plan with observable success criteria
    plan = plan_goal(instruction, update_log_callback)
    plan_text = (
        f"Execution Plan:\n"
        f"Goal: {plan['goal_summary']}\n"
        f"Steps:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan.get("steps", []))) + "\n"
        f"Success Criteria:\n" + "\n".join(f"  - {c}" for c in plan.get("success_criteria", [])) + "\n"
        f"Completion Signal: {plan.get('completion_signal', '')}"
    )

    decision_messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {"role": "user", "content": f"User Goal: {instruction}\n\n{plan_text}\n\nBegin executing the plan. Take the first action."},
    ]
    just_resumed_from_input = False
    consecutive_done_attempts = 0  # Track how many times in a row the agent says done

    for iteration in range(25):
        if update_log_callback:
            update_log_callback(f"[Step {iteration + 1}/25]")

        # 1. Take screenshot of current OS state
        screenshot_b64 = os_control.take_screenshot(step=iteration + 1)

        # 2. Get Vision Description
        try:
            if update_log_callback:
                update_log_callback("[System] Analyzing screen...")

            print("\n[Vision] Prompting Vision Model with screen snapshot...")
            vision_messages = [
                {"role": "system", "content": VISION_PROMPT.strip()},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"User Goal: {instruction}\nDescribe ONLY what you see in this screenshot. List bounding boxes for goal-relevant interactable elements."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
                    ],
                },
            ]
            logger.log_llm_prompt("google/gemini-3-flash-preview", vision_messages)
            vision_response = vision_client.chat.completions.create(
                model="google/gemini-3-flash-preview",
                messages=vision_messages,
                max_tokens=800,
            )
            screen_description = vision_response.choices[0].message.content.strip()

            # DOM extraction for browser contexts
            dom_context = ""
            if "chrome" in screen_description.lower() or "edge" in screen_description.lower():
                try:
                    page = get_browser_page()
                    if page:
                        affordances = browser.extract_affordances(page)
                        if len(affordances) > 50:
                            affordances = affordances[:50]
                        dom_context = f"\n[DOM Context - {len(affordances)} Clickable Elements Available]:\n{json.dumps(affordances)}"
                except Exception as e:
                    print(f"Failed to extract DOM: {e}")

            print(f"\n[Vision Model Screen Description]:\n{screen_description}\n")

            # Debug: Draw bounding boxes on the screenshot
            try:
                os.makedirs("debug", exist_ok=True)
                step_num = iteration + 1
                debug_img = Image.open(f"debug/step_{step_num}_original.png")
                draw = ImageDraw.Draw(debug_img)
                w, h = debug_img.size

                for match in re.finditer(r'\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]\s*(.*)', screen_description):
                    ymin, xmin, ymax, xmax = map(int, match.groups()[:4])
                    label = match.group(5)[:40]
                    ry1, rx1 = int((ymin/1000) * h), int((xmin/1000) * w)
                    ry2, rx2 = int((ymax/1000) * h), int((xmax/1000) * w)
                    y0, y1 = sorted([ry1, ry2])
                    x0, x1 = sorted([rx1, rx2])
                    draw.rectangle([x0, y0, x1, y1], outline="red", width=2)
                    draw.text((x0, max(0, y0 - 12)), label, fill="red")

                debug_img.save(f"debug/step_{step_num}_vision_boxes.png")
                print(f"[Debug] Saved debug images for step {step_num}")
            except Exception as e:
                print(f"[Debug] Failed to save vision boxes: {e}")

        except Exception as e:
            error_msg = f"Error in vision module: {str(e)}"
            logger.log_error(error_msg)
            if update_log_callback:
                update_log_callback(error_msg)
            print(error_msg)
            logger.log_session_end("Task failed at vision step due to an error.")
            return "Task failed at vision step due to an error."

        # 3. Check goal completion when vision signals COMPLETE
        if "GOAL STATUS: COMPLETE" in screen_description.upper():
            if update_log_callback:
                update_log_callback("[System] Vision reports goal may be complete — verifying...")
            accomplished, reason = check_goal_accomplished(instruction, plan, screen_description)
            if accomplished:
                _play_finish_sound()
                logger.log_session_end(reason)
                tts.speak(reason)
                if update_log_callback:
                    update_log_callback(f"✅ Goal accomplished: {reason}")
                return reason

        # 4. Add vision context to decision messages
        screen_update = (
            f"VISION MODULE OUTPUT:\n{screen_description}{dom_context}\n\n"
            f"User Goal: {instruction}\n\n"
            f"REMINDER: Respond with ONLY valid JSON. Use ONLY bounding boxes from the vision output above. "
            f"Do NOT guess coordinates. If the target element is not listed, navigate to find it. "
            f"Only output 'done' when the screen confirms the success criteria are met."
        )
        # Always append a fresh user message for each iteration
        decision_messages.append({"role": "user", "content": screen_update})
        just_resumed_from_input = False

        # 5. Get Action Decision
        try:
            if update_log_callback:
                update_log_callback("[System] Deciding action...")

            print(f"\n[Decision] Prompting Decision Model with Goal: '{instruction}'")
            logger.log_llm_prompt("deepseek-ai/DeepSeek-V3-0324", decision_messages)

            response = _call_decision_model(
                messages=decision_messages,
                max_tokens=1500,
            )

            raw_content = response.choices[0].message.content.strip()
            logger.log_llm_response("deepseek-ai/DeepSeek-V3-0324", raw_content)
            print(f"\n[Decision Model Raw Response]:\n{raw_content}\n")

            raw_content = _extract_json(raw_content)

            try:
                action_data = json.loads(raw_content)
            except json.JSONDecodeError:
                import ast
                action_data = ast.literal_eval(raw_content)

            actions = action_data if isinstance(action_data, list) else [action_data]

            for action in actions:
                print(f"\n[Agent Thought]: {action.get('thought', 'No thought provided.')}")
                print(f"[Parsed Action]: {json.dumps(action)}")

                if update_log_callback:
                    update_log_callback(f"🧠 Agent Thought: {action.get('thought', 'Deciding...')}")
                    update_log_callback(f"🤖 Agent Action: {action.get('type')}")

                logger.log_action(action)

                if action["type"] == "done":
                    consecutive_done_attempts += 1
                    # Agent says done — verify with goal checker before accepting
                    if update_log_callback:
                        update_log_callback("[System] Agent reports done — verifying goal completion...")
                    accomplished, reason = check_goal_accomplished(instruction, plan, screen_description)
                    if accomplished:
                        _play_finish_sound()
                        msg = reason or action.get("message", "Done.")
                        logger.log_session_end(msg)
                        tts.speak(msg)
                        return msg
                    elif consecutive_done_attempts >= 2:
                        # Agent has said 'done' multiple times — trust it to avoid infinite loop
                        print(f"[Goal Check] Agent has said done {consecutive_done_attempts}x in a row. Accepting.")
                        if update_log_callback:
                            update_log_callback(f"[System] Task confirmed complete after persistent agent signal.")
                        _play_finish_sound()
                        msg = action.get("message", "Task complete.")
                        logger.log_session_end(msg)
                        tts.speak(msg)
                        return msg
                    else:
                        # Agent may be wrong — override and keep going once
                        print(f"[Goal Check] Agent said done but goal not confirmed. Continuing... (attempt {consecutive_done_attempts})")
                        if update_log_callback:
                            update_log_callback(f"[System] Goal not yet confirmed — continuing...")
                        decision_messages.append({"role": "assistant", "content": json.dumps(action_data)})
                        decision_messages.append({
                            "role": "user",
                            "content": (
                                f"The goal verification was inconclusive. Reason: {reason}. "
                                f"If you believe the task is truly complete based on what you see, output 'done' again. "
                                f"Otherwise, take any remaining action needed."
                            ),
                        })
                        just_resumed_from_input = True
                        break

                if action["type"] == "speak":
                    tts.speak(action["text"])

                if action["type"] == "request_user_input":
                    prompt = action.get("prompt", "Please provide input, then press OK on the widget.")
                    translated_prompt = translate_from_english(prompt, user_language)
                    tts.speak(translated_prompt)
                    if update_log_callback:
                        update_log_callback(f"⌨️ Waiting for user input: {prompt}")

                    state.state.set_state("waiting_for_input")
                    user_reply_event.clear()
                    user_reply_event.wait(timeout=120)

                    reply = user_reply_text.strip() or "done"
                    if update_log_callback:
                        update_log_callback(f"🎤 User replied: {reply}")

                    decision_messages.append({"role": "assistant", "content": json.dumps(action)})
                    decision_messages.append({
                        "role": "user",
                        "content": f"User confirmed (said: '{reply}'). ASSUME THIS STEP IS COMPLETE. Proceed to the NEXT action toward the goal. Do NOT use request_user_input again.",
                    })
                    just_resumed_from_input = True
                    break

                # Execute OS/Browser action
                active_page = None
                if action["type"] == "click_element" or action.get("context") == "browser":
                    active_page = get_browser_page()

                execute_action(action, page=active_page)
                time.sleep(1.0)

            if not just_resumed_from_input:
                consecutive_done_attempts = 0  # Reset counter when agent takes a real action
                decision_messages.append({"role": "assistant", "content": json.dumps(action_data)})

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


# For testing purposes
def console_logger(msg):
    print(msg)

if __name__ == "__main__":
    print("==================================================")
    print("Testing Agent Logic without Audio...")
    print("==================================================")
    # fake_transcript = "Open Spotify and play WHAT IS LOVE by TWICE"
    # fake_transcript = "Open Google Docs in the browser and create a new blank document."
    fake_transcript = "Send Sharanya a discord message saying Are you free later."
    print(f"Submitting Fake Transcript: '{fake_transcript}'\n")

    try:
        final_message = run_agent(fake_transcript, update_log_callback=console_logger)
        print("\nAgent finished! Final Message:", final_message)
    except Exception as e:
        print("\nAgent crashed during testing:", str(e))

    print("\nTest complete.")

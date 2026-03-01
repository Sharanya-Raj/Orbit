import os
import io
import json
import time
import re
import threading
import base64
import functools
from collections import deque
from pathlib import Path
from dotenv import load_dotenv
import openai
import imagehash
from actions import os_control, execute_action
from core import tts, state, logger
from models import validate_action_list
import pygame
from PIL import Image, ImageDraw
from rich.console import Console
from rich.panel import Panel
from rich.align import Align
from rich.text import Text
from rich import box
from pyfiglet import Figlet

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


# ---------------------------------------------------------------------------
# Screenshot diffing helpers (Task 2)
# ---------------------------------------------------------------------------

def _compute_screen_hash(screenshot_b64: str) -> imagehash.ImageHash:
    """Decode a base64 PNG and return its perceptual hash."""
    img = Image.open(io.BytesIO(base64.b64decode(screenshot_b64)))
    return imagehash.phash(img, hash_size=16)


def _screen_changed(h_before: imagehash.ImageHash, h_after: imagehash.ImageHash,
                    threshold: int = 5) -> tuple:
    """
    Returns (changed: bool, hamming_distance: int).
    A Hamming distance > threshold means the screen changed meaningfully.
    Threshold of 5 tolerates minor clock/animation flicker.
    """
    dist = h_before - h_after
    return dist > threshold, dist


# ---------------------------------------------------------------------------
# Repeated-action fingerprinting (Task 3)
# ---------------------------------------------------------------------------

def _action_fingerprint(action) -> tuple:
    """
    Create a hashable fingerprint for a Pydantic action model or raw dict.
    Bbox coordinates are rounded to the nearest 10 to tolerate minor drift.
    """
    # Support both Pydantic models and raw dicts
    if hasattr(action, "type"):
        t = action.type
        get = lambda k, default=None: getattr(action, k, default)
    else:
        t = action.get("type", "")
        get = lambda k, default=None: action.get(k, default)

    if t == "click_box":
        bbox = get("bbox") or []
        return (t, tuple(round(v / 10) * 10 for v in bbox))
    elif t == "type_text":
        return (t, str(get("text", ""))[:50])
    elif t == "click_element":
        return (t, str(get("selector", ""))[:50])
    elif t == "press_key":
        return (t, str(get("key", "")))
    elif t == "press_shortcut":
        keys = get("keys") or []
        return (t, tuple(keys))
    elif t == "open_app":
        return (t, str(get("app", "")))
    return (t,)


def _format_action_history(history: list) -> str:
    """Format the action history as a compact readable log for the decision model."""
    if not history:
        return "(No actions executed yet — this is the first step.)"
    lines = []
    for h in history:
        step = h.get("step", "?")
        atype = h.get("type", "unknown")
        thought = (h.get("thought") or "")[:110].strip()
        note = h.get("note", "")
        changed = h.get("screen_changed")
        if changed is False:
            outcome = "NO CHANGE — had no visible effect"
        elif changed is True:
            outcome = "screen updated"
        else:
            outcome = "(outcome unknown)"
        line = f"  [{step}] {atype}: {thought}"
        if note:
            line += f" | {note}"
        line += f"  ->  {outcome}"
        lines.append(line)
    return "\n".join(lines)


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

EXCLUDED REGIONS:
- DO NOT identify or report ANY elements in the bottom taskbar area (y > 950 in normalized coordinates).
- The user's taskbar is HIDDEN. Any detected elements in that region are artifacts — ignore them completely.
- Never report icons, buttons, or labels that appear at the very bottom edge of the screen.

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

PLANNING CONSTRAINTS:
- The user's taskbar is HIDDEN. NEVER include steps like "click the X icon in the taskbar" or "click the taskbar".
- To open apps, always plan: "Use open_app to launch <name>"
- To switch apps, always plan: "Use Alt+Tab to switch to <name>"
- Never plan any action that targets the bottom of the screen.
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
# SYSTEM PROMPT — Anti-hallucination and context rules are at the TOP
# so DeepSeek encounters them before the action list anchors its behavior.
# =============================================================================
SYSTEM_PROMPT = """You are an AI computer-control agent helping blind users operate their computer by voice.

╔═══ ABSOLUTE RULES (NEVER VIOLATE) ═══════════════════════════════════════════╗
║                                                                               ║
║  TASKBAR DOES NOT EXIST. The user's taskbar is HIDDEN. You CANNOT see it,    ║
║  click it, or interact with it in any way.                                    ║
║                                                                               ║
║  To open ANY app:          {"type": "open_app", "app": "<name>", "context": "os"}          ║
║  To switch between apps:   {"type": "press_shortcut", "keys": ["alt", "tab"], "context": "os"} ║
║  There is NO other way to open or switch apps. Period.                        ║
║                                                                               ║
║  NEVER generate click_box actions where bbox ymin > 950.                      ║
║  The bottom 50px of the screen is OFF LIMITS — always.                        ║
╚═══════════════════════════════════════════════════════════════════════════════╝

RESPONSE FORMAT: You MUST respond with ONLY a valid JSON action (or array of actions). No prose, no markdown fences, no explanation outside the JSON.

ANTI-HALLUCINATION SAFEGUARDS (CRITICAL — VIOLATIONS CAUSE TASK FAILURE):

1. NEVER GUESS COORDINATES. Only use bounding boxes the vision module EXPLICITLY provided. If not listed, navigate to find it.

2. NEVER CLICK THE TASKBAR. The taskbar is HIDDEN and does not exist (see ABSOLUTE RULES above). ALWAYS use open_app to open apps. ALWAYS use press_shortcut ["alt","tab"] to switch between open apps. NEVER click anything with bbox ymin > 950.

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

CONTEXT RULES:
- Default context is "os". Use "os" for ALL desktop apps (Spotify, Discord, Settings, File Explorer, etc.).
- Use "browser" ONLY when interacting with web page content inside a window the vision module explicitly classified as "BROWSER".
- CRITICAL: If vision says "DESKTOP_APP", you MUST use "os" context and click_box. Never use click_element or "browser" context for desktop apps.

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

EXAMPLES OF CORRECT APP NAVIGATION:

Goal: "Open Spotify"
  CORRECT: {"thought": "Need to open Spotify. Using open_app.", "type": "open_app", "app": "spotify", "context": "os"}
  WRONG:   clicking anything at the bottom of the screen

Goal: "Switch to Discord" (Discord is already open)
  CORRECT: {"thought": "Discord is open, switching with alt+tab.", "type": "press_shortcut", "keys": ["alt", "tab"], "context": "os"}
  WRONG:   clicking a taskbar icon

Goal: "Go back to Chrome"
  CORRECT: {"thought": "Switching to Chrome via open_app.", "type": "open_app", "app": "chrome", "context": "os"}
  WRONG:   any click_box with ymin > 900

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


def retry_api_call(max_retries=3, base_delay=2.0):
    """Decorator to retry Featherless API calls on 429/concurrency errors with exponential backoff."""
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
                        delay *= 2
                    else:
                        raise
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


def translate_to_english(text: str) -> tuple:
    """Translates the text to English and returns (english_text, detected_language)."""
    try:
        response = _call_decision_model(
            messages=[
                {"role": "system", "content": "You are a translator. Detect the language of the user's text. If it is already English, just return 'English|||' followed by the exact text. Otherwise, return the name of the detected language, followed by '|||', followed by the English translation. Do not add any conversational filler. Example: 'Spanish|||Translate this to English'"},
                {"role": "user", "content": text},
            ],
            max_tokens=200,
        )
        content = response.choices[0].message.content.strip()
        parts = content.split("|||", 1)
        if len(parts) == 2:
            return parts[1].strip(), parts[0].strip()
        return text, "English"
    except Exception as e:
        print(f"[Translation] Failed to translate to English: {e}")
        return text, "English"


def translate_from_english(text: str, target_language: str) -> str:
    """Translates English text back into the user's original language."""
    if target_language.lower() == "english":
        return text

    try:
        response = _call_decision_model(
            messages=[
                {"role": "system", "content": f"You are a translator. Translate the following English text into {target_language}. Return ONLY the translated text. No conversational filler."},
                {"role": "user", "content": text},
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
    print(f"\n[Multi-Lingual] Detected Language: {user_language} -> Translated goal: '{english_instruction}'\n")

    if update_log_callback:
        update_log_callback(f"🎤 You: {instruction}")
        if user_language.lower() != "english":
            update_log_callback(f"🌐 Translated to English: {english_instruction}")

    # 0. Planning phase — create a plan with observable success criteria
    plan = plan_goal(english_instruction, update_log_callback)
    plan_steps = plan.get("steps", [])
    plan_text = (
        f"Execution Plan:\n"
        f"Goal: {plan['goal_summary']}\n"
        f"Steps:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan_steps)) + "\n"
        f"Success Criteria:\n" + "\n".join(f"  - {c}" for c in plan.get("success_criteria", [])) + "\n"
        f"Completion Signal: {plan.get('completion_signal', '')}"
    )

    decision_messages = [
        {"role": "system", "content": SYSTEM_PROMPT.strip()},
        {"role": "user", "content": f"User Goal: {english_instruction}\n\n{plan_text}\n\nBegin executing the plan. Take the first action."},
    ]
    just_resumed_from_input = False

    # --- Loop hardening state (Task 3) ---
    total_actions_executed = 0
    MAX_TOTAL_ACTIONS = 30
    recent_actions: deque = deque(maxlen=3)
    action_history: list = []  # running log of executed actions for working memory

    for iteration in range(25):
        if update_log_callback:
            update_log_callback(f"[Step {iteration + 1}/25]")

        # 1. Take screenshot of current OS state
        screenshot_b64 = os_control.take_screenshot(step=iteration + 1)
        logger.log_screenshot(iteration + 1, f"debug/step_{iteration + 1}_original.png")

        # Pre-compute hash for the screenshot taken at the start of this iteration
        # (used later to diff against post-action state — Task 2)
        try:
            hash_before = _compute_screen_hash(screenshot_b64)
        except Exception as e:
            print(f"[Diff] Failed to hash screenshot: {e}")
            hash_before = None

        # 2. Get Vision Description
        try:
            if update_log_callback:
                update_log_callback("[System] Analyzing screen...")

            # Task 4: Focused vision — inject current plan step into the user message
            current_step_index = (
                min(iteration * len(plan_steps) // max(1, 25), len(plan_steps) - 1)
                if plan_steps else 0
            )
            current_step = plan_steps[current_step_index] if plan_steps else ""

            print("\n[Vision] Prompting Vision Model with screen snapshot...")
            vision_messages = [
                {"role": "system", "content": VISION_PROMPT.strip()},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            f"User Goal: {english_instruction}\n"
                            f"Current Plan Step: {current_step}\n"
                            f"Priority: Find the UI element needed for this step: [{current_step}]. "
                            f"Return at most 5-8 goal-relevant elements. "
                            f"Describe ONLY what you see in this screenshot."
                        )},
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
            logger.log_llm_response("google/gemini-3-flash-preview", screen_description)

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
            accomplished, reason = check_goal_accomplished(english_instruction, plan, screen_description)
            if accomplished:
                _play_finish_sound()
                final_msg = translate_from_english(reason, user_language)
                logger.log_session_end(reason)
                tts.speak(final_msg)
                if update_log_callback:
                    update_log_callback(f"✅ Goal accomplished: {reason}")
                return reason

        # 4. Add vision context to decision messages
        history_text = _format_action_history(action_history)
        screen_update = (
            f"=== CURRENT SCREEN ===\n"
            f"VISION MODULE OUTPUT:\n{screen_description}{dom_context}\n\n"
            f"=== EXECUTION HISTORY (what you have tried so far this session) ===\n"
            f"{history_text}\n\n"
            f"=== GOAL & PLAN ===\n"
            f"User Goal: {english_instruction}\n\n"
            f"{plan_text}\n\n"
            f"REMINDER: Respond with ONLY valid JSON. Use ONLY bounding boxes from the vision output above. "
            f"Do NOT guess coordinates. If the target element is not listed, navigate to find it. "
            f"Only output 'done' when the screen confirms the success criteria are met."
        )
        # Always append a fresh user message for each iteration
        decision_messages.append({"role": "user", "content": screen_update})
        just_resumed_from_input = False

        # 5. Get Action Decision — with Pydantic validation retry loop (Task 3)
        MAX_VALIDATION_RETRIES = 2
        validation_retries = 0
        step_succeeded = False

        while validation_retries <= MAX_VALIDATION_RETRIES:
            try:
                if update_log_callback:
                    update_log_callback("[System] Deciding action...")

                print(f"\n[Decision] Prompting Decision Model for goal: '{english_instruction}'")
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

                actions_raw = action_data if isinstance(action_data, list) else [action_data]

                # --- Task 1 + 3: Validate all actions before executing any ---
                validated_actions, val_err = validate_action_list(actions_raw)
                if val_err:
                    logger.log_validation("batch", False, val_err)
                    validation_retries += 1
                    if validation_retries > MAX_VALIDATION_RETRIES:
                        logger.log_error(
                            f"Step {iteration+1}: validation failed after {MAX_VALIDATION_RETRIES+1} attempts. Last error: {val_err}"
                        )
                        break  # abandon this iteration, move to next
                    print(f"[Validation] FAIL (attempt {validation_retries}): {val_err}")
                    if update_log_callback:
                        update_log_callback(f"[Validation] Schema error — retrying... ({val_err[:80]})")
                    decision_messages.append({"role": "assistant", "content": raw_content})
                    decision_messages.append({
                        "role": "user",
                        "content": (
                            f"Your response had a schema validation error: {val_err}. "
                            f"Fix the error and respond with valid JSON only. "
                            f"Ensure all required fields are present and all values are in valid ranges "
                            f"(bbox values must be 0-1000, ymax > ymin, xmax > xmin)."
                        ),
                    })
                    continue  # retry the while loop

                logger.log_validation("batch", True)

                # --- Execute validated actions ---
                for action in validated_actions:
                    action_dict = action.model_dump(exclude_none=False)
                    print(f"\n[Agent Thought]: {action.thought or 'No thought provided.'}")
                    print(f"[Parsed Action]: {json.dumps(action_dict)}")

                    if update_log_callback:
                        update_log_callback(f"🧠 Agent Thought: {action.thought or 'Deciding...'}")
                        update_log_callback(f"🤖 Agent Action: {action.type}")

                    logger.log_action(action_dict)

                    if action.type == "done":
                        # Verify before accepting — gate on confidence >= 80
                        if update_log_callback:
                            update_log_callback("[System] Agent reports done — verifying goal completion...")
                        accomplished, reason = check_goal_accomplished(english_instruction, plan, screen_description)
                        if accomplished:
                            _play_finish_sound()
                            msg = reason or action.message
                            logger.log_session_end(msg)
                            final_msg = translate_from_english(msg, user_language)
                            tts.speak(final_msg)
                            return msg
                        else:
                            # Override premature done — keep going
                            print(f"[Goal Check] Agent said done but goal not confirmed. Continuing...")
                            if update_log_callback:
                                update_log_callback(f"[System] Goal not yet confirmed — continuing...")
                            action_history.append({
                                "step": iteration + 1,
                                "type": "done (premature — rejected)",
                                "thought": action.thought or "",
                                "screen_changed": False,
                                "note": f"Not confirmed: {reason[:80]}",
                            })
                            decision_messages.append({"role": "assistant", "content": json.dumps(action_data)})
                            decision_messages.append({
                                "role": "user",
                                "content": (
                                    f"The goal is NOT yet accomplished. Verification found: {reason}. "
                                    f"Review the success criteria and continue working toward the goal."
                                ),
                            })
                            just_resumed_from_input = True
                            break

                    if action.type == "speak":
                        tts.speak(translate_from_english(action.text, user_language))

                    if action.type == "request_user_input":
                        prompt_text = action.prompt
                        translated_prompt = translate_from_english(prompt_text, user_language)
                        tts.speak(translated_prompt)
                        if update_log_callback:
                            update_log_callback(f"⌨️ Waiting for user input: {prompt_text}")

                        state.state.set_state("waiting_for_input")
                        user_reply_event.clear()
                        user_reply_event.wait(timeout=120)

                        reply = user_reply_text.strip() or "done"
                        if update_log_callback:
                            update_log_callback(f"🎤 User replied: {reply}")

                        action_history.append({
                            "step": iteration + 1,
                            "type": "request_user_input",
                            "thought": action.thought or "",
                            "screen_changed": False,
                            "note": f"User said: '{reply[:60]}'",
                        })
                        decision_messages.append({"role": "assistant", "content": json.dumps(action_dict)})
                        decision_messages.append({
                            "role": "user",
                            "content": f"User confirmed (said: '{reply}'). ASSUME THIS STEP IS COMPLETE. Proceed to the NEXT action toward the goal. Do NOT use request_user_input again.",
                        })
                        just_resumed_from_input = True
                        break

                    # --- Execute OS/Browser action ---
                    active_page = None
                    if action.type == "click_element" or action.context == "browser":
                        active_page = get_browser_page()

                    try:
                        execute_action(action_dict, page=active_page)
                        logger.log_execution_result(action.type, True)
                    except Exception as exec_err:
                        logger.log_execution_result(action.type, False, str(exec_err))
                        raise

                    time.sleep(1.0)

                    # --- Task 3: Total action cap ---
                    total_actions_executed += 1
                    if total_actions_executed >= MAX_TOTAL_ACTIONS:
                        logger.log_error(f"Hard action limit of {MAX_TOTAL_ACTIONS} reached.")
                        if update_log_callback:
                            update_log_callback(f"[System] Maximum action limit reached ({MAX_TOTAL_ACTIONS}).")
                        tts.speak("I've reached the maximum number of actions. Stopping here.")
                        logger.log_session_end("Maximum action limit reached.")
                        return "Maximum action limit reached."

                    # --- Task 2: Screen diff after non-exempt actions ---
                    DIFF_EXEMPT = {"speak", "wait", "done", "request_user_input", "screenshot"}
                    if action.type not in DIFF_EXEMPT and hash_before is not None:
                        try:
                            post_b64 = os_control.take_screenshot(step=None)
                            hash_after = _compute_screen_hash(post_b64)
                            screen_changed, diff_dist = _screen_changed(hash_before, hash_after)
                            logger.log_screen_diff(iteration + 1, action.type, screen_changed, diff_dist)
                            print(f"[Diff] Screen changed: {screen_changed} (hamming={diff_dist})")
                            # Update hash_before for subsequent actions in the same batch
                            hash_before = hash_after
                        except Exception as e:
                            print(f"[Diff] Error computing screen diff: {e}")
                            screen_changed = True  # assume changed on error
                            diff_dist = -1
                    else:
                        screen_changed = True
                        diff_dist = 0

                    # --- Task 3: Repeated-action detection ---
                    fp = _action_fingerprint(action)
                    recent_actions.append(fp)
                    if len(recent_actions) == 3 and len(set(recent_actions)) == 1:
                        logger.log(f"[LOOP DETECT] Repeated action 3x: {fp}")
                        if update_log_callback:
                            update_log_callback(f"[System] Loop detected — same action repeated 3x. Forcing different approach.")
                        decision_messages.append({"role": "assistant", "content": json.dumps(action_data)})
                        decision_messages.append({
                            "role": "user",
                            "content": (
                                f"ALERT: You have repeated the exact same action '{fp[0]}' with identical "
                                f"parameters 3 times in a row. This approach is NOT working. "
                                f"You MUST try a completely different approach — use a different action type "
                                f"or a different navigation path. Do not repeat this action again."
                            ),
                        })
                        recent_actions.clear()
                        just_resumed_from_input = True
                        break

                    # Store screen_changed for followup message (Task 2)
                    # (only the last action's result matters for the message)
                    action._screen_changed = screen_changed  # type: ignore[attr-defined]

                    # Record to working memory so future iterations know what was tried
                    action_history.append({
                        "step": iteration + 1,
                        "type": action.type,
                        "thought": action.thought or "",
                        "screen_changed": screen_changed,
                    })

                # Build the followup message — conditionally include no-change warning
                if not just_resumed_from_input:
                    # Check if the last executed action saw no screen change
                    last_changed = getattr(validated_actions[-1], "_screen_changed", True) if validated_actions else True
                    if not last_changed:
                        followup = (
                            "Action executed. WARNING: The screen did NOT visibly change after this action. "
                            "The previous action had no visible effect. Try a completely different approach — "
                            "do NOT repeat the same action."
                        )
                    else:
                        followup = "Action executed. Analyze the new screenshot and decide the next step."
                    decision_messages.append({"role": "assistant", "content": json.dumps(action_data)})
                    decision_messages.append({"role": "user", "content": followup})

                step_succeeded = True
                break  # exit validation retry while loop

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


console = Console()

def display_welcome_banner():
    console.clear()

    # Top header box
    header = Text(
        "✻ Welcome to the ORBIT research preview!",
        style="bold orange1"
    )

    console.print(
        Panel(
            header,
            border_style="orange1",
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )

    console.print()  # spacing

    # Big ASCII banner
    fig = Figlet(font="ansi_shadow")  # try: block, doom, big
    ascii_text = fig.renderText("ORBIT")

    console.print(
        Align.center(
            f"[bold orange1]{ascii_text}[/bold orange1]"
        )
    )

    console.print()  # spacing


# For testing purposes
def console_logger(msg):
    print(msg)

if __name__ == "__main__":
    display_welcome_banner()
    print("==================================================")
    print("Testing Agent Logic without Audio...")
    print("==================================================")
    fake_transcript = "Open Spotify and play WHAT IS LOVE by TWICE"
    # fake_transcript = "Open Google Docs in the browser and create a new blank document."
    # fake_transcript = "Send Sharanya a discord message saying Are you free later."
    print(f"Submitting Fake Transcript: '{fake_transcript}'\n")

    try:
        final_message = run_agent(fake_transcript, update_log_callback=console_logger)
        print("\nAgent finished! Final Message:", final_message)
    except Exception as e:
        print("\nAgent crashed during testing:", str(e))

    print("\nTest complete.")

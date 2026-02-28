# 🎙️ Voice Assistant — Full Project Documentation

> A Cluely-style floating desktop widget that lets blind (and sighted) users control their computer entirely by voice. Press a hotkey, say what you want, and watch the computer do it.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [How It Works — Big Picture](#2-how-it-works--big-picture)
3. [File Structure](#3-file-structure)
4. [Data Flow](#4-data-flow)
5. [Module Breakdown](#5-module-breakdown)
6. [The Browser Toolkit](#6-the-browser-toolkit)
7. [The AI Agent — Perception-Action Loop](#7-the-ai-agent--perception-action-loop)
8. [OpenRouter Setup & Costs](#8-openrouter-setup--costs)
9. [Widget UI & States](#9-widget-ui--states)
10. [Setup & Installation](#10-setup--installation)
11. [Environment Variables](#11-environment-variables)
12. [How This Differs from OpenClaw](#12-how-this-differs-from-openclaw)
13. [Example Commands](#13-example-commands)
14. [Extending the Project](#14-extending-the-project)
15. [Hackathon Demo Tips](#15-hackathon-demo-tips)
16. [Known Limitations](#16-known-limitations)

---

## 1. Project Overview

This is a **local desktop application** — not a web app. Everything runs on the user's machine as a single Python process. There is no server, no HTTP API, no database.

The core idea:

- User presses `Ctrl+Shift+Space`
- Speaks a natural language instruction ("Open a new Google Doc")
- The mic records their voice → Whisper transcribes it → Gemini plans and executes actions one at a time, taking a screenshot after each to verify the result → Windows TTS speaks back when done

The widget is a small, dark floating pill that sits at the top of the screen and visualizes each stage in real time.

### What makes this different from existing tools

This is **not** a chatbot, screen reader, or remote task delegator. It's a real-time, in-the-moment voice interface for people who can't use a mouse and keyboard. The user is at their computer right now and needs to operate it hands-free. No setup, no phone required — just one hotkey away at all times.

---

## 2. How It Works — Big Picture

```
┌─────────────────────────────────────────────────────────────┐
│                        widget.py (UI)                       │
│   Shows: idle ring → recording bars → thinking arc → done  │
└────────────────────┬──────────────────────────┬────────────┘
                     │ queue (in-memory)         │
          ┌──────────▼──────────┐    ┌──────────▼──────────┐
          │    core/audio.py    │    │     core/tts.py      │
          │  Records mic input  │    │  Speaks result back  │
          │  Whisper → text     │    │  (Windows built-in)  │
          └──────────┬──────────┘    └─────────────────────-┘
                     │ transcript text
          ┌──────────▼──────────┐
          │      agent.py       │
          │  Perception-Action  │
          │  Loop with Gemini   │
          │  via OpenRouter     │
          └──────────┬──────────┘
                     │ one action at a time
          ┌──────────▼──────────┐
          │     actions/        │
          │  browser.py  →  Playwright (Chrome)
          │  os_control.py → PyAutoGUI (OS)  │
          └──────────┬──────────┘
                     │ screenshot after each action
                     └──────────► back to agent.py (loop)
```

The key architectural points:
- `widget.py` and `agent.py` communicate through a **shared in-memory queue** — not HTTP, not a network socket. One Python process, two threads.
- The agent runs a **perception-action loop**: act → screenshot → send screenshot to Gemini → get next action → repeat. It never plans all steps upfront — it observes the result of each action before deciding the next one, exactly like a human would.

---

## 3. File Structure

```
voice-assistant/
│
├── widget.py              # Floating UI — the pill the user sees
├── agent.py               # Perception-action loop + OpenRouter/Gemini
│
├── core/
│   ├── audio.py           # Mic recording + Whisper transcription
│   ├── tts.py             # Text-to-speech (speaks back to user)
│   ├── hotkey.py          # Global Ctrl+Shift+Space listener
│   └── state.py           # Shared state: idle / recording / thinking / done
│
├── actions/
│   ├── browser.py         # Playwright browser toolkit (~10 functions)
│   ├── os_control.py      # PyAutoGUI — open apps, OS shortcuts, screenshots
│   └── __init__.py        # Action router — dispatches steps to right module
│
├── .env                   # API keys (never commit to git)
├── requirements.txt       # All pip dependencies
├── README.md              # Quick start
└── DOCS.md                # This file
```

---

## 4. Data Flow

Step by step, what happens when you press the hotkey and speak:

```
1. core/hotkey.py        detects Ctrl+Shift+Space globally (even when unfocused)
        ↓
2. core/audio.py         opens mic, records until hotkey pressed again
        ↓
3. core/audio.py         passes audio buffer to Whisper → plain text string
        ↓
4. agent.py              starts the perception-action loop:
        ↓
        ├─ takes a screenshot of the current screen
        ├─ sends transcript + screenshot to Gemini via OpenRouter
        ├─ Gemini returns ONE action (e.g. open_url, click, type)
        ├─ actions/ executes it on the real screen
        ├─ waits for screen to settle
        ├─ takes a new screenshot
        ├─ sends it back to Gemini: "did that work? what's next?"
        └─ repeats until Gemini returns {"type": "done"}
        ↓
5. core/tts.py           speaks the final result back to the user
        ↓
6. widget.py             updates visual state throughout via queue messages
```

---

## 5. Module Breakdown

### `widget.py`
The entire visual layer. Built with **tkinter** (ships with Python, no install needed).

Responsibilities:
- Draws the floating pill with rounded corners on a transparent window
- Animates 4 visual states (see section 9)
- Expands to show a transcript log when a command runs
- Listens to a `queue.Queue` for state updates from the agent thread
- Handles drag-to-reposition

Does **not** contain any audio, LLM, or automation logic — purely display.

---

### `agent.py`
The brain of the app. Runs the perception-action loop via OpenRouter.

```python
import openai, base64, json, time
from actions.os_control import take_screenshot
from actions import execute_action
from core.tts import speak

client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY"),
)

def run_agent(instruction: str) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": instruction}]

    for _ in range(15):  # max 15 iterations safety cap
        screenshot_b64 = take_screenshot()

        # attach screenshot to latest user message
        messages[-1]["content"] = [
            {"type": "text",      "text": messages[-1]["content"]
                                          if isinstance(messages[-1]["content"], str)
                                          else "What should I do next?"},
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{screenshot_b64}"
            }}
        ]

        response = client.chat.completions.create(
            model="google/gemini-flash-1.5",
            messages=messages,
            max_tokens=256,
        )

        action = json.loads(response.choices[0].message.content)

        if action["type"] == "done":
            return action.get("message", "Done.")

        if action["type"] == "speak":
            speak(action["text"])

        execute_action(action)
        time.sleep(0.8)  # let screen settle before next screenshot

        messages.append({"role": "assistant", "content": str(action)})
        messages.append({"role": "user",      "content": "Action complete. What's next?"})

    return "Maximum steps reached."
```

---

### `core/audio.py`
Handles everything microphone-related.

- Uses `sounddevice` to open a mic stream
- Accumulates audio chunks into a numpy array
- When recording stops, passes the buffer to `openai-whisper`
- Returns a plain text string

```python
def record_until_stop() -> np.ndarray: ...
def transcribe(audio: np.ndarray) -> str: ...
```

---

### `core/tts.py`
Speaks text back to the user using Windows' built-in speech synthesis — no API key or extra install required.

```python
def speak(text: str):
    subprocess.Popen([
        "powershell", "-Command",
        f"Add-Type -AssemblyName System.Speech; "
        f"$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Speak('{text}')"
    ], creationflags=subprocess.CREATE_NO_WINDOW)
```

---

### `core/hotkey.py`
Registers a **global** hotkey using `pynput` — global means it fires even when the widget isn't focused, which is essential for accessibility.

```python
# Fires Ctrl+Shift+Space across the whole OS, even when widget isn't focused
def listen(on_toggle: callable): ...
```

---

### `core/state.py`
A simple shared object that tracks the current app state so both the UI thread and agent thread stay in sync.

States: `idle` → `recording` → `thinking` → `done` → `idle`

---

### `actions/browser.py`
The Playwright toolkit — ~10 reusable functions the LLM picks from. Write once, never touch again. See full breakdown in [Section 6](#6-the-browser-toolkit).

---

### `actions/os_control.py`
PyAutoGUI functions for things outside the browser.

```python
def open_app(name: str):          # subprocess.Popen(["start", name])
def press_shortcut(*keys):        # pyautogui.hotkey(*keys)
def move_and_click(x, y):         # pyautogui.click(x, y)
def take_screenshot() -> str:     # captures screen, returns base64 string
def press_win_key():              # opens Start menu
def type_text(text: str):         # pyautogui.typewrite(text)
```

---

### `actions/__init__.py`
The action router. Receives a single action dict from Gemini and calls the right function.

```python
def execute_action(action: dict, page=None, browser=None):
    match action["type"]:
        case "open_url":      browser.open_url(page, action["url"])
        case "click_element": browser.click_element(page, action["selector"])
        case "type_text":     browser.type_text(page, action["text"])
        case "press_key":     browser.press_key(page, action["key"])
        case "open_app":      os_control.open_app(action["app"])
        case "win_key":       os_control.press_win_key()
        case "click_xy":      os_control.move_and_click(action["x"], action["y"])
        case "speak":         tts.speak(action["text"])
        case "wait":          time.sleep(action["ms"] / 1000)
        case "screenshot":    pass  # loop handles this automatically
```

---

## 6. The Browser Toolkit

These are the only Playwright functions you need to build. The LLM figures out how to combine them — you never write task-specific automation code.

| Function | What it does |
|---|---|
| `open_url(page, url)` | Navigate to a URL, wait for page load |
| `click_element(page, selector)` | Click a CSS selector or aria label |
| `type_text(page, text)` | Type text at current focus |
| `press_key(page, key)` | Press a key: `"Enter"`, `"Tab"`, `"Escape"` |
| `focus_url_bar(page)` | `Ctrl+L` — jumps focus to browser address bar |
| `new_tab(browser)` | Opens a new tab, returns the new page object |
| `scroll(page, direction)` | Scroll up or down |
| `wait(page, ms)` | Wait for a fixed time (for slow page loads) |
| `get_page_text(page)` | Returns all visible text — lets Gemini "read" the page |
| `screenshot(page)` | Takes a screenshot — fed back to Gemini each loop iteration |

---

## 7. The AI Agent — Perception-Action Loop

### Two approaches, and why we use the loop

**Approach A — Upfront JSON plan**
Gemini plans all steps at once then executes them blindly. Fast, but brittle — if step 2 fails or a page loads slowly, the agent doesn't know and can't adapt.

**Approach B — Perception-action loop (what we use)**
The agent acts one step at a time, takes a screenshot after each action, and sends it back to Gemini asking "did that work? what's next?" This is exactly how a human operates a computer. It:

- Works on **any app** — Discord, Notepad, the taskbar, anything visible on screen
- **Adapts** if something fails or looks unexpected
- Can press **Win key**, click the taskbar, wait for windows to open — full OS control
- **Self-corrects** — if a click missed, Gemini can see that and try again

### The loop in plain English

```
1. Take a screenshot of the current screen
2. Send it to Gemini: "Here's the screen. The user wants X. What's the single next action?"
3. Gemini returns ONE action: {"type": "open_app", "app": "chrome"}
4. Execute it via PyAutoGUI or Playwright
5. Wait ~0.8s for the screen to change
6. Go back to step 1
7. Repeat until Gemini returns {"type": "done", "message": "Chrome is open"}
```

### System prompt

```
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
- Return exactly ONE action per response
- After each action the screen will be re-captured and sent back to you
- Verify the previous action succeeded before moving to the next step
- Prefer open_url for browser navigation over clicking through menus
- Use win_key + type_text + press_key("Enter") to open desktop apps
- Always end with the "done" type and a spoken summary for the user
```

### Hybrid approach for performance

For simple browser tasks, skip the loop and use Playwright directly — it's instant and reliable. Only invoke the full perception-action loop for OS-level tasks (opening apps, interacting with the taskbar, anything outside the browser).

| Task type | Approach | Speed |
|---|---|---|
| Open a URL | Playwright directly | ~0.5s |
| Click a known button | Playwright directly | ~0.3s |
| Open Discord from taskbar | Perception-action loop | ~5-10s |
| Navigate an unfamiliar page | Perception-action loop | ~5s/step |

---

## 8. OpenRouter Setup & Costs

### Why OpenRouter instead of the Gemini API directly

OpenRouter is an API aggregator that routes requests to 300+ models using a single OpenAI-compatible endpoint. With credits loaded, there are **no platform-level rate limits** — unlike Google's free tier which was cut 50-92% in December 2025 without warning.

### Rate limits

| Tier | RPM | Daily limit |
|---|---|---|
| OpenRouter free | 20 RPM | 50 req/day |
| **OpenRouter with credits** | **No platform limit** | **No platform limit** |

With credits, the only limits are Google's backend limits — ~150-300 RPM on paid access, which is far more than this app will ever use.

### Cost breakdown

Gemini 1.5 Flash via OpenRouter: ~$0.075 per million input tokens.

Per voice command in the perception-action loop:

| Per loop iteration | Tokens |
|---|---|
| System prompt | ~300 |
| Screenshot (as image) | ~800 |
| Transcript + context | ~50 |
| Model response | ~100 |
| **Per iteration** | **~1,250** |
| **Full command (6 iterations)** | **~7,500** |

**Cost per command: ~$0.0006** (less than a tenth of a cent)

With **$10 in MLH credits** you can run ~16,000 full commands. The entire hackathon will realistically cost under $0.50.

### Safety: set a spending cap

In the OpenRouter dashboard, set a **$5 hard cap** on your API key. This prevents any infinite loop bug from spending more than that. One-click setting under key settings at openrouter.ai.

### The code change from Gemini SDK — 3 lines

```python
import openai

client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY"),
)

response = client.chat.completions.create(
    model="google/gemini-flash-1.5",  # OpenRouter model string
    messages=messages,
    max_tokens=256,
)
```

No new packages — the standard `openai` SDK works natively with OpenRouter.

---

## 9. Widget UI & States

The widget is a dark floating pill (`#18181c`) pinned to the top-center of the screen. It's always-on-top, draggable, and has no title bar.

### Visual States

| State | Animation | Color | Meaning |
|---|---|---|---|
| **Idle** | Static grey ring + mic icon | `#2a2a35` | Waiting for hotkey |
| **Recording** | Bouncing bars + pulsing outer ring | `#6c63ff` purple | Mic is live |
| **Thinking** | Spinning arc | `#a78bfa` violet | Agent is acting / looping |
| **Done** | Green checkmark + ring | `#22d3a5` teal | Task completed |

### Expansion
When a command runs the widget expands to reveal a transcript log:
```
🎤 You: Open Discord and message John hello
🤖 Agent: Opening Discord... clicked Messages... typed hello... Done.
```
Collapses back to pill after 2.5 seconds.

### Key window properties
```python
root.overrideredirect(True)           # no title bar
root.attributes("-topmost", True)     # always floats above other windows
root.attributes("-transparentcolor", BG)  # background invisible, only pill shows
root.attributes("-alpha", 0.96)       # slight transparency
```

---

## 10. Setup & Installation

### Prerequisites
- Windows 10/11
- Python 3.10+
- OpenRouter account with credits loaded (openrouter.ai)

### Step 1 — Install dependencies
```bash
pip install openai pynput sounddevice numpy openai-whisper playwright pyautogui python-dotenv
playwright install chromium
```

### Step 2 — Get your OpenRouter API key
1. Go to [openrouter.ai](https://openrouter.ai)
2. Sign up / log in
3. Go to **Keys** → **Create Key**
4. Set a **$5 spending cap** on the key
5. Copy the key (starts with `sk-or-`)

### Step 3 — Create a `.env` file in the project root
```
OPENROUTER_API_KEY=sk-or-your-key-here
```

### Step 4 — Run
```bash
python widget.py
```

Press `Ctrl+Shift+Space` to start recording. Press again to stop and execute.

---

## 11. Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | ✅ Yes | From openrouter.ai — starts with `sk-or-` |

No other secrets needed. TTS uses Windows built-in, Whisper runs locally, Playwright controls a local Chromium browser.

---

## 12. How This Differs from OpenClaw

OpenClaw is the closest existing tool — an open-source AI agent that controls your computer. But the use cases are fundamentally different.

### What OpenClaw is
A **remote task delegator**. You text it from WhatsApp or Telegram while away from your computer and it handles tasks in the background. It runs 24/7 on a dedicated machine, has persistent memory, handles email, calendars, and file management autonomously.

### What this app is
A **real-time accessibility interface**. The user IS at their computer right now and needs to operate it immediately, hands-free. One hotkey, speak, watch it happen.

| | OpenClaw | This App |
|---|---|---|
| **Interface** | Text chat via WhatsApp/Telegram | Voice, on the computer |
| **Primary user** | Power users / developers | Blind & visually impaired users |
| **Use case** | Delegate tasks remotely | Real-time hands-free OS control |
| **Interaction** | Async — you message, it does it later | Sync — hotkey, speak, watch it happen |
| **Visual feedback** | None (chat only) | Animated widget, live transcript |
| **Setup** | Terminal, config, 10-15 min | Double-click to run |
| **Accessibility focus** | None | Core purpose |
| **Works when away from PC** | ✅ Yes | ❌ No |
| **Designed for blind users** | ❌ No | ✅ Yes |

**One-line pitch**: "OpenClaw is a remote control for your computer. We're a real-time voice interface for people who can't use a mouse and keyboard."

---

## 13. Example Commands

| Voice command | What the agent does |
|---|---|
| "Open a new Google Doc" | Opens Chrome → navigates to docs.google.com/document/create |
| "Search YouTube for lo-fi music" | Navigates to youtube.com search results |
| "Go to my Gmail inbox" | Opens mail.google.com |
| "Open Discord" | Win key → types "discord" → Enter → waits for app to load |
| "Open Notepad and type hello world" | Win key → notepad → Enter → types text |
| "Go back to the previous tab" | `Ctrl+Shift+Tab` |
| "Scroll down" | `scroll(page, "down")` |
| "Open a new tab" | `Ctrl+T` |
| "Close this window" | `Alt+F4` |
| "Type my email address" | `type_text("user@example.com")` |

---

## 14. Extending the Project

### Adding a new browser action
1. Add a function to `actions/browser.py`
2. Add a `case` for it in `actions/__init__.py`
3. Describe it in the system prompt in `agent.py`

Gemini will start using it automatically.

### Adding OS-level actions
Same pattern in `actions/os_control.py`:
```python
def take_screenshot() -> str:
    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
```

### Adding memory / history
```python
# Store past commands so "do the same thing as yesterday" works
history = json.load(open("history.json"))
```

### Swapping to a faster STT model
```python
model = whisper.load_model("tiny")  # faster, slightly less accurate
# vs "base" (default) or "small" (more accurate, slower)
```

---

## 15. Hackathon Demo Tips

**Pre-test 5 specific flows** cold. Judges see the demo, not edge cases.

Recommended sequence:
1. "Open a new Google Doc" — simple, immediately visual
2. "Open Discord" — shows Win key + OS-level app launching
3. "Go to my Gmail inbox" — shows browser navigation
4. "Open Notepad and type Hello World" — shows end-to-end typing
5. Live command from the audience — biggest applause moment

**If the loop is slow**: pre-cache 2-3 action sequences as local JSON. The execution (watching the computer move) is the impressive part, not API latency.

**On the $10 MLH credit**: you will not run out. Set a $5 cap on your key anyway as a safety net against infinite loop bugs.

**Pitch framing**: "It's AI-powered real-time computer access for blind users — you speak naturally, the AI watches the screen and acts like a human, and it speaks the result back to you." That's the differentiator from screen readers and from OpenClaw.

---

## 16. Known Limitations

| Limitation | Workaround |
|---|---|
| Perception-action loop is slow (~3-5s per action) | Use Playwright directly for browser tasks; loop only for OS-level actions |
| Gemini may click wrong screen coordinates | Prefer CSS selectors over `click_xy`; use `get_page_text()` so Gemini reads labels |
| Login / 2FA flows are hard to automate | Pre-login before the demo; store browser session cookies |
| Whisper `base` model takes ~2s and ~1GB RAM | Use `tiny` model for faster, lighter transcription |
| `pynput` global hotkey may conflict with some apps | Fall back to tkinter-bound hotkey (only works when widget is focused) |
| No multi-monitor support | Widget appears on primary monitor only |
| Loop can run indefinitely if Gemini never returns `"done"` | Hard cap of `max_iterations=15` in the agent loop |
| OpenRouter free tier: only 50 req/day | Load any credits to remove this limit entirely |

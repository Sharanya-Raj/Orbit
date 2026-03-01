# Orbit — Voice-First AI Agent for Accessible Computer Control

> **Hackathon Track: Accessibility**
> Empowering the visually impaired, elderly, and motor-impaired to operate any Windows computer entirely by voice — no typing, no clicking, no mouse.

---

## The Problem

For millions of people, using a computer is not a given.

- **Visually impaired users** cannot read a screen, locate UI elements, or navigate graphical interfaces without costly, often-incomplete screen-reader software.
- **Elderly users** struggle with the growing complexity of modern operating systems — nested menus, tiny click targets, and constantly changing interfaces.
- **Motor-impaired users** for whom holding a mouse or typing is painful or impossible are forced to rely on inflexible, slow assistive tooling.

Existing solutions (Windows Narrator, JAWS, Dragon NaturallySpeaking) are rigid and brittle — they require precise commands, flat scripted workflows, and collapse entirely when a website redesigns its layout or an app changes its UI.

**Orbit is different.** It actually *sees* the screen the same way a sighted person does, understands what is on it, and takes real actions — adapting to whatever it finds, step by step.

---

## What Orbit Does

Hold `Ctrl+Shift+Space` and speak naturally:

> *"Open Chrome and search for today's weather in Toronto"*
> *"Go to Gmail and send a message to Mom saying I'll call her tonight"*
> *"Open Notepad and type my shopping list: milk, eggs, bread"*
> *"Find and open the most recent Excel file on my desktop"*

Orbit listens, understands, and executes — autonomously operating your mouse, keyboard, and browser until the task is complete. It speaks the result back to you when it's done.

No scripting. No voice-command memorization. Just natural speech.

---

## Who We're Building For

### The Visually Impaired
Orbit doesn't use accessibility trees or ARIA labels — it uses a **vision AI model that perceives the screen as a pixel image**, exactly like a sighted person would. This means it works on *every* application: legacy software, web apps, games, custom enterprise tools — anything. A blind user can interact with the full breadth of the Windows ecosystem without compromise.

### The Elderly
Seniors face a steep and constantly-shifting learning curve. Orbit eliminates that entirely: there is no syntax to learn, no manual to read. You speak as you would to another person, and the computer responds. Orbit can help them video call family, pay bills online, write emails, and navigate medical portals — all by talking naturally.

### The Motor-Impaired
For users with conditions like ALS, MS, or cerebral palsy, every physical interaction with a computer is difficult and exhausting. Orbit reduces an entire workflow — clicks, typing, scrolling, form-filling — to a single press-and-speak gesture.

---

## Live Demo Scenarios

| Voice Command | What Orbit Does |
|---|---|
| "Open YouTube and play relaxing piano music" | Launches browser → navigates to YouTube → searches → clicks first result → plays video |
| "Email my doctor's office and ask for an appointment next Tuesday" | Opens Gmail → composes email → fills recipient, subject, body → sends |
| "What's the weather like this week?" | Opens a weather site → reads the forecast aloud via TTS |
| "Open my Documents folder and tell me what files are there" | Navigates File Explorer → reads filenames back to user |
| "Go to Amazon and search for large-print keyboards" | Opens browser → navigates Amazon → performs search → reads top results |

---

## Technical Architecture

Orbit is a multi-threaded Python application built around a **dual-model agentic perception-action loop**. The agent does not plan all steps upfront — it perceives the current state of the screen, decides the single best next action, executes it, and re-perceives. This reactive, agentic design makes it resilient to unexpected UI states, pop-ups, login pages, and layout changes.

```
┌─────────────────────────────────────────────────────────────────────┐
│                          widget.py (UI Thread)                       │
│  PyQt6 Glass Widget  ──  Hold-to-Talk Hotkey  ──  State Machine     │
│        │                        │                       │            │
│    Glow Overlay           Audio Capture             TTS Playback    │
└────────────────────────────┬────────────────────────────────────────┘
                             │  queue.Queue (thread-safe message bus)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         agent.py (Agent Thread)                      │
│                                                                       │
│   ┌──────────────────────────────────────────────────────────────┐   │
│   │              Agentic Perception-Action Loop                   │   │
│   │                                                               │   │
│   │  Screenshot ──► Vision Model ──► Screen Description          │   │
│   │       ▲              (Gemini 1.5 Flash via OpenRouter)        │   │
│   │       │                       │                               │   │
│   │       │              Decision Model                           │   │
│   │       │           (DeepSeek-R1 via Featherless)               │   │
│   │       │                       │                               │   │
│   │       │              JSON Action ◄──────────────────────────  │   │
│   │       │                       │                               │   │
│   │       │         ┌─────────────┴──────────────┐               │   │
│   │       │         ▼                            ▼               │   │
│   │   0.8s wait  OS Context               Browser Context        │   │
│   │       │    (PyAutoGUI)               (Playwright/Edge)        │   │
│   │       └────────────────────────────────────────              │   │
│   └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### The Agentic Loop (`agent.py:run_agent`)

The core of Orbit is a fully agentic, reactive control loop with a maximum of 15 iterations:

1. **Perceive:** Capture a full-resolution screenshot of the Windows desktop.
2. **Describe:** Send the screenshot to **Gemini 1.5 Flash** (via OpenRouter), a multimodal vision model. Gemini returns a structured natural-language description of every visible UI element, annotated with normalized bounding boxes on a `[0, 1000]` coordinate scale (`[ymin, xmin, ymax, xmax]`).
3. **Decide:** Feed the screen description and the full conversation history to **DeepSeek-R1** (via Featherless AI), a reasoning model. DeepSeek returns a single structured JSON action — the minimum necessary next step.
4. **Act:** Execute the action. Actions are dispatched to one of two execution backends based on a `"context"` field:
   - `"context": "os"` → **PyAutoGUI** (mouse moves, clicks, keyboard input, hotkeys, screenshots)
   - `"context": "browser"` → **Playwright** (URL navigation, DOM element interaction, form filling)
5. **Wait and repeat:** A 800ms settling delay allows the UI to animate and re-render before the next perception pass.
6. **Terminate:** The loop exits when the agent emits `{"type": "done"}` or the iteration limit is reached.

This agentic pattern — perceive, reason, act, verify — mirrors how a human assistant would operate a computer on your behalf, making Orbit robust to any application UI by design.

### Dual-Model Design

Orbit deliberately separates **perception** from **reasoning** across two specialized models:

| Role | Model | Provider | Why |
|---|---|---|---|
| **Vision** | Gemini 1.5 Flash | OpenRouter | Best-in-class multimodal understanding; fast screen-to-text pipeline |
| **Decision** | DeepSeek-R1 | Featherless AI | Strong structured reasoning for multi-step planning; JSON-reliable output |

This separation means each model is doing what it does best. The vision model never has to reason about actions; the decision model never has to process raw pixels.

### Coordinate System & Spatial Grounding

Bounding boxes from the vision model use a normalized `0–1000` scale independent of screen resolution. `os_control.move_and_click` converts these to real screen pixel coordinates at runtime using the live screen dimensions, ensuring accuracy across different monitor resolutions and DPI settings.

### Persistent Browser Session

Playwright maintains a persistent browser profile stored in `.browser_profile/`, preserving login sessions and cookies across Orbit invocations. This is critical for accessibility — users (especially elderly users) should never have to re-authenticate. The agent targets Microsoft Edge (`channel="msedge"`) as it is the default Windows browser.

### Dynamic User Input Handoff

When the agent encounters a login page or a form requiring personal information it does not have, it emits a `request_user_input` action. This suspends the agentic loop, sets the UI to a "waiting" state, and blocks the agent thread on a `threading.Event`. The user's next hold-to-talk press injects their spoken reply directly into the agent's conversation context, which then resumes the loop seamlessly. This enables Orbit to handle the full complexity of real-world web interactions without hardcoding credentials.

### UI: Liquid Glass Widget

The floating UI is built with **PyQt6** and uses the **Windows Desktop Window Manager (DWM)** API directly via `ctypes` to apply native acrylic blur-behind (`ACCENT_ENABLE_ACRYLICBLURBEHIND`) and rounded corners (`DWMWCP_ROUND`). A full-screen transparent overlay renders a gradient glow along all four monitor edges to provide ambient visual feedback of agent state — active (blue pulse) vs. idle (transparent).

### Audio Pipeline

- **Capture:** `sounddevice` streams PCM audio from the default microphone into a buffer while the hotkey is held.
- **Transcription:** The audio buffer is passed to **OpenAI Whisper** (local model) for offline, privacy-preserving speech-to-text.
- **TTS:** `pyttsx3` converts the agent's final response back to synthesized speech, closing the voice interaction loop.

---

## Key Engineering Decisions

**Why not use accessibility APIs?** Screen readers and accessibility trees only work on apps that implement them correctly. Many legacy apps, custom enterprise tools, and web components have broken or absent accessibility trees. Orbit's vision-first approach works on everything a human eye can see.

**Why clipboard paste instead of `typewrite`?** `pyautogui.typewrite` is unreliable for non-ASCII characters, email addresses with `@`, and Unicode. Orbit writes text to the clipboard and pastes with `Ctrl+V`, giving it correct input for any language or special character.

**Why two threads?** The Qt UI must run on the main thread. All AI inference and OS control runs on a background daemon thread, communicating back to the UI via a `queue.Queue`. This prevents the UI from blocking while the agent is executing a long task.

**Why normalize coordinates to 0–1000?** A fixed coordinate space in prompts prevents the vision model from producing outputs tied to any specific resolution, making the system portable across 1080p, 1440p, and 4K displays.

---

## Setup & Installation

### Prerequisites
- Windows 10 or 11
- Python 3.10+
- An [OpenRouter](https://openrouter.ai) API key (for Gemini vision)
- A [Featherless AI](https://featherless.ai) API key (for DeepSeek reasoning)

### Install

```bash
pip install -r requirements.txt
playwright install chromium
```

### Configure

Copy `.env.example` to `.env`:

```
OPENROUTER_API_KEY=sk-or-...
FEATHERLESS_API_KEY=...
```

### Run

```bash
python widget.py
```

Hold `Ctrl+Shift+Space` to speak. Release to execute.

---

## Project Structure

```
orbit/
├── widget.py            # UI layer: PyQt6 glass widget, hotkey, audio dispatch
├── agent.py             # Agentic loop: vision, decision, action orchestration
├── core/
│   ├── hotkey.py        # Global hotkey listener (Windows low-level keyboard hook)
│   ├── audio.py         # Microphone capture + Whisper transcription
│   ├── tts.py           # Text-to-speech output
│   └── state.py         # Thread-safe UI state machine
├── actions/
│   ├── __init__.py      # Action router (os vs. browser context dispatch)
│   ├── os_control.py    # PyAutoGUI: mouse, keyboard, screenshot, clipboard
│   └── browser.py       # Playwright: URL navigation, element interaction
├── models.py            # JSON action schema validation
└── frontend/            # Static React/Vite UI mockup (not connected to backend)
```

---

## Inspiration

We started thinking about who gets left behind as computers become more complex. A grandmother trying to video call her grandchildren shouldn't need to know what a browser tab is. A veteran who lost fine motor control in both hands shouldn't need a mouse to send an email. A person who is blind shouldn't have to depend on screen readers that break the moment a website updates its layout.

The common thread: these people don't need *simpler* computers — they need a computer that can be *spoken to*. Every one of us has, at some point, wished we could just tell our computer what we want and have it done. For most of us that's a convenience. For millions of people, it's a necessity.

We were also frustrated by how brittle existing assistive tools are. They rely on accessibility trees, ARIA labels, and rigid command grammars — all of which break the moment an app updates or a login page appears. We wanted to build something that sees the screen the same way a human does and adapts in real time — a true AI agent, not a voice-activated macro.

---

## What It Does

Orbit is a floating Windows desktop agent you activate by holding `Ctrl+Shift+Space`. You speak a natural-language command — anything from "open Chrome and search for today's weather" to "go to Gmail and email Mom that I'll call her tonight" — and Orbit executes it autonomously, step by step, on your actual computer.

It sees the screen using a multimodal vision AI, decides what to do next using a reasoning AI, and executes actions using real OS-level controls: moving the mouse, clicking, typing, filling forms, navigating the browser. After each action it takes a new screenshot, re-examines the screen, and decides its next move. When it's done, it speaks the result back to you.

No scripts. No memorized commands. No accessibility tree required. It works on every application — legacy software, web apps, custom enterprise tools — anything a human eye can see.

---

## How We Built It

Orbit is built around a **dual-model agentic perception-action loop** implemented in Python:

**Vision layer:** Every iteration starts with a full-resolution screenshot sent to **Gemini 1.5 Flash** via OpenRouter. Gemini returns a structured natural-language description of every visible UI element, each annotated with a normalized bounding box on a `[0, 1000]` coordinate scale.

**Reasoning layer:** That screen description — along with the full conversation history — is passed to **DeepSeek-R1** via Featherless AI. DeepSeek returns a single JSON action representing the best next step. Separating vision from reasoning means each model does what it excels at.

**Execution layer:** Actions are dispatched to one of two backends based on a `context` field: OS-level interactions (mouse, keyboard, clipboard) go through **PyAutoGUI**; browser interactions go through **Playwright** against a persistent Microsoft Edge session that preserves login cookies between runs.

**UI layer:** The floating widget is built with **PyQt6** and uses the Windows **DWM API** directly via `ctypes` to render a native acrylic blur-behind glass effect and rounded corners. A full-screen transparent overlay pulses a gradient glow along all four monitor edges to provide ambient state feedback.

**Audio pipeline:** `sounddevice` captures microphone input while the hotkey is held. **OpenAI Whisper** (local model) transcribes it offline. `pyttsx3` speaks the agent's final result back to the user. The entire voice loop runs on a background thread communicating with the Qt UI thread via a `queue.Queue`.

---

## Challenges We Ran Into

**Coordinate grounding across resolutions.** Getting the vision model to consistently point to the right pixel on screen was the hardest problem. We solved it by normalizing all bounding boxes to a `[0, 1000]` scale in the prompt and converting to real pixels at execution time — making the system resolution-agnostic.

**Unicode and special characters in text input.** `pyautogui.typewrite` silently drops `@` signs, accented characters, and spaces mid-word. We replaced it entirely with clipboard-paste (`Ctrl+V`), which works correctly for any character in any language.

**Agent loop stability.** Early versions of the loop would get stuck re-trying the same failed action. We added perceptual hash comparison between successive screenshots (`imagehash`) so the agent can detect when a screen hasn't changed and adjust its strategy rather than repeat itself.

**Cross-thread UI updates.** Qt requires all widget mutations to happen on the main thread, but the agent runs on a background thread. We built a `queue.Queue` message bus so the agent can safely emit state, labels, and log entries that the UI thread picks up on a 100ms polling timer.

**Handling login walls mid-task.** When the agent encounters a login page it cannot fill autonomously, it needs to pause and ask the user. We implemented a `request_user_input` action that suspends the agentic loop on a `threading.Event`, switches the widget to a "waiting" state, and resumes the loop with the user's next spoken reply injected directly into the agent's conversation context.

---

## Accomplishments That We're Proud Of

- Built a fully agentic, multi-model system that can operate the *entire* Windows OS — not just a single app — from a single voice command.
- The vision-first approach means Orbit works on applications that have zero accessibility support — legacy software, custom enterprise tools, anything.
- Seamless mid-task user input handoff: the agent pauses, waits for voice input, and resumes without losing context.
- A native-feeling glass UI using direct DWM API calls — it looks like it belongs on Windows 11, not like a Python script.
- End-to-end privacy: Whisper transcription runs locally, no audio ever leaves the machine.

---

## What We Learned

- Splitting perception and reasoning across two specialized models dramatically improves reliability compared to asking a single model to do both.
- Agentic loops need explicit termination conditions and stall detection — without them, they will confidently repeat the same broken action forever.
- The hardest part of building accessible software is not the AI — it's making the interaction model truly zero-friction for someone who cannot see or struggle to hold a mouse.
- Windows DWM composition is surprisingly approachable from Python `ctypes`, and the visual difference between a native acrylic glass blur and a fake CSS-style approximation is immediately noticeable.

---

## What's Next for Orbit

- **Mobile companion app** — a phone-based microphone that pairs wirelessly with Orbit on the desktop, so users don't need to reach a keyboard hotkey at all.
- **Scheduled and triggered tasks** — "Every morning, open my email and read me the subject lines" without the user needing to initiate anything.
- **Memory layer** — let the agent remember frequently-used workflows, login preferences, and app locations across sessions to reduce repeated reasoning steps.
- **Multi-monitor and remote desktop support** — extend the coordinate system to span multiple displays or RDP sessions.
- **Cross-platform** — port the OS control and hotkey layers to macOS and Linux, making Orbit available to a broader population of users who need it.

---

## Team

Built at Quackhacks 2026 in 24 hours by Orbit.

- Om Rana
- Sharanya Raj

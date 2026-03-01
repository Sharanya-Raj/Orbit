# Orbit — Voice-First AI Agent for Accessible Computer Control

> **Hackathon Tracks:**
> 🏆 **Accessibility Track** | 🏆 **Overall Hack** | 🗣️ **Best Use of ElevenLabs** | 🚀 **Best Use of Featherless AI**
>
> Empowering the visually impaired, elderly, and motor-impaired to operate any Windows computer entirely by voice — no typing, no clicking, no mouse.

---

## 🌎 The Problem

For millions of people, using a computer is not a given.

- **Visually impaired users** cannot read a screen, locate UI elements, or navigate graphical interfaces without costly, often-incomplete screen-reader software.
- **Elderly users** struggle with the growing complexity of modern operating systems — nested menus, tiny click targets, and constantly changing interfaces.
- **Motor-impaired users** for whom holding a mouse or typing is painful or impossible are forced to rely on inflexible, slow assistive tooling.

Existing solutions (Windows Narrator, JAWS, Dragon NaturallySpeaking) are rigid and brittle — they require precise commands, flat scripted workflows, and collapse entirely when a website redesigns its layout or an app changes its UI.

**Orbit is different.** It actually *sees* the screen the same way a sighted person does, understands what is on it, and takes real actions — adapting to whatever it finds, step by step.

---

## ✨ What Orbit Does

Hold `Ctrl+Shift+Space` and speak naturally:

> *"Open Chrome and search for today's weather in Toronto"*
> *"Go to Gmail and send a message to Mom saying I'll call her tonight"*
> *"Open Notepad and type my shopping list: milk, eggs, bread"*
> *"Find and open the most recent Excel file on my desktop"*

Orbit listens, understands, and executes — autonomously operating your mouse, keyboard, and browser until the task is complete. Along the way, it handles any pop-ups, dynamically searches for missing UI elements, and speaks the result back to you in a natural, human-like voice.

No scripting. No voice-command memorization. Just natural speech.

---

## 🏆 Hackathon Highlights

Orbit was engineered to win across multiple tracks by pushing the boundaries of what is possible with multimodal AI, low-latency reasoning, and ultra-lifelike speech synthesis.

### ♿ Accessibility & Overall Hack
Orbit reimagines the entire human-computer interface. It doesn't rely on accessibility trees, which break on legacy software and modern web apps. It uses a **vision AI model that perceives the screen as a pixel image**.
- **The Visually Impaired:** Orbit can interact with the full breadth of the Windows ecosystem without compromise. It literally reads the screen and figures out how to navigate for them.
- **The Elderly:** No syntax to learn, no manual to read. You speak as you would to a friend.
- **The Motor-Impaired:** Orbit reduces an entire complex workflow (clicks, typing, scrolling, form-filling) down to a single press-and-speak gesture.

### 🗣️ Best Use of ElevenLabs
For visually impaired or elderly users, the "voice" of the assistant is the entire interface. We integrated **ElevenLabs** (Multilingual v2 Model) to completely transform the interaction loop. 
- **Warm & Natural:** Replaced generic, robotic OS TTS with ultra-lifelike speech that feels empathetic and natural.
- **Multilingual Support:** Orbit automatically detects the language the user speaks (e.g., Spanish). It uses DeepSeek to translate the objective to English for internal OS reasoning, takes actions, and then uses ElevenLabs to synthesize the final response *back into the user's native tongue*.
- **Blocking & Async Playback:** Seamlessly integrated with `pygame` to lock audio playback only when necessary, preventing overlapping system sounds.

### 🚀 Best Use of Featherless AI
Orbit's core is a hyper-reactive loop that perceives the screen, reasons, and acts every 1-2 seconds. **Latency is life or death.**
- **DeepSeek-V3 Infrastructure:** We use **Featherless AI** to run `DeepSeek-V3-0324` at lightning speed. Featherless's serverless AI infrastructure provides the ultra-low latency inference required to keep the agentic loop running fluidly.
- **Complex JSON Routing:** The decision model relies on strict JSON boundaries to evaluate bounding boxes, DOM context, and error states. Featherless flawlessly handles the high-throughput schema requests, allowing Orbit to instantly plan and react to unexpectedly broken JSON (utilizing our custom reflection-retry loop).

---

## ⚙️ Technical Architecture

Orbit is a multi-threaded Python application built around a **dual-model agentic perception-action loop**. It does not plan every step upfront — it perceives the current state, decides the single best next action, executes it, and re-perceives.

```text
┌─────────────────────────────────────────────────────────────────────┐
│                          widget.py (UI Thread)                       │
│  PyQt6 Glass Widget  ──  Hold-to-Talk Hotkey  ──  State Machine     │
│        │                        │                       │            │
│    Glow Overlay           Audio Capture             ElevenLabs TTS  │
└────────────────────────────┬────────────────────────────────────────┘
                             │  queue.Queue (thread-safe message bus)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         agent.py (Agent Thread)                      │
│   ┌──────────────────────────────────────────────────────────────┐   │
│   │              Agentic Perception-Action Loop                   │   │
│   │                                                               │   │
│   │  Screenshot ──► Vision Model ──► Screen Description          │   │
│   │       ▲              (Gemini 1.5 Flash via OpenRouter)        │   │
│   │       │                       │                               │   │
│   │       │              Decision Model                           │   │
│   │       │           (DeepSeek-V3 via Featherless AI)            │   │
│   │       │                       │                               │   │
│   │       │              JSON Action ◄──────────────────────────  │   │
│   │       │                       │                               │   │
│   │       │         ┌─────────────┴──────────────┐               │   │
│   │       │         ▼                            ▼               │   │
│   │   Wait/Diff  OS Context               Browser Context        │   │
│   │       │    (PyAutoGUI)               (Playwright/Edge)        │   │
│   │       └────────────────────────────────────────              │   │
│   └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### The Agentic Loop (`agent.py:run_agent`)

1. **Plan & Translate:** Detect user language, translate to English, and formulate a multi-step execution plan with strictly observable success criteria.
2. **Perceive:** Capture a full-resolution screenshot of the Windows desktop.
3. **Describe:** Send to **Gemini 1.5 Flash** (via OpenRouter). Gemini returns a natural language structure of UI elements annotated with normalized `[ymin, xmin, ymax, xmax]` bounding boxes (`[0, 1000]` coordinate scale).
4. **Decide:** Feed the screen description, DOM affordances (if in a browser), and error history to **DeepSeek-V3** (via Featherless AI). DeepSeek returns a single structured JSON action.
5. **Act:** Execute the action via:
   - `"context": "os"` → **PyAutoGUI** (mouse moves, clicks, keyboard input, hotkeys).
   - `"context": "browser"` → **Playwright** (URL navigation, DOM tracking on an active Edge session).
6. **Verify:** Has the screen structurally changed? (Computed via `imagehash` perceptual diffs). Did the action loop 3x? If so, prompt DeepSeek to aggressively correct course.
7. **Terminate & Speak:** Exit when criteria are met or max steps reached; translate the final status back to the user's language and speak via **ElevenLabs Multilingual v2**.

### Dynamic User Input Handoff
When the agent encounters a login page or complex CAPTCHA it cannot bypass, it emits a `request_user_input` action. It suspends the agentic loop, switches the UI to a "waiting" state, and blocks execution on a `threading.Event`. The user's next spoken whisper injects their reply directly into the prompt context, resuming the loop seamlessly without losing browser state.

---

## 🛠️ Key Engineering Decisions

- **Why Dual-Models instead of one?** Vision models are great at drawing bounding boxes; reasoning models (like DeepSeek-V3) excel at logic and JSON adherence. Splitting perception and reasoning allows each model to do what it does best, significantly dropping error rates.
- **Why normalized `0-1000` coordinates?** A fixed coordinate space prevents the vision model from hallucinating specific screen resolution pixels, making Orbit completely agnostic to 1080p, 1440p, or 4K monitors.
- **Why perceptual hashes?** Early versions would get stuck re-trying failed clicks forever. Orbit now computes a perceptual `imagehash` of the screen after every action. If the screen doesn't change, Orbit dynamically injects a warning into DeepSeek-V3's prompt context to force a new approach.
- **Advanced JSON Reflection:** If DeepSeek-V3 outputs malformed JSON, rather than crashing, the agent catches the exception, attempts an `ast.literal_eval` fallback, and automatically feeds the parser error back to the model as a prompt to self-correct.

---

## 🚀 Setup & Installation

### Prerequisites
- Windows 10 or 11
- Python 3.10+
- [Featherless AI](https://featherless.ai) API key (for DeepSeek-V3 reasoning)
- [OpenRouter](https://openrouter.ai) API key (for Gemini vision)
- [ElevenLabs](https://elevenlabs.io) API key (for human-like TTS)

### Install

```bash
pip install -r requirements.txt
playwright install chromium
```

### Configure
Copy `.env.example` to `.env`:
```
FEATHERLESS_API_KEY=your_key_here
OPENROUTER_API_KEY=your_key_here
ELEVENLABS_API_KEY=your_key_here
```

### Run

```bash
python widget.py
```
Hold `Ctrl+Shift+Space` to speak. Release to execute.

---

## 🔮 What's Next for Orbit

- **Mobile companion app** — a phone-based microphone that pairs wirelessly with Orbit on the desktop, minimizing the need to reach a keyboard hotkey at all.
- **Scheduled accessibility chains** — "Every morning, open my email and read me the subject lines" without the user needing to initiate anything.
- **Memory layer** — allow the agent to remember frequently used workflows and login preferences to cut latency on repetitive task clusters.
- **Cross-platform** — port the OS control and hotkey layers to macOS and Linux.

---

## 👥 Team
Built at Quackhacks 2026 by Orbit.
- Om Rana
- Sharanya Raj

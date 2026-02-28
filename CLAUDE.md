# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**Orbit** is a Windows-only voice assistant that lets users control their computer hands-free. Press `Ctrl+Shift+Space` (hold-to-talk), speak a command, release to execute. The AI watches the screen after each action and adapts — it does not plan all steps upfront.

## Running the App

```bash
# Install dependencies (run once)
pip install -r requirements.txt
playwright install chromium

# Start the floating widget
python widget.py

# Test the agent loop directly (bypasses audio/hotkey)
python agent.py
```

## Environment Setup

Copy `.env.example` to `.env` and fill in both keys:
```
OPENROUTER_API_KEY=sk-or-...   # Vision model (Gemini) — openrouter.ai
FEATHERLESS_API_KEY=...        # Decision model (DeepSeek) — featherless.ai
```

## Architecture

One Python process, two threads communicating via `queue.Queue`.

```
widget.py  ──(queue)──►  agent.py
   │                         │
   ├── core/hotkey.py         ├── Vision: Gemini via OpenRouter
   ├── core/audio.py          ├── Decision: DeepSeek via Featherless
   ├── core/state.py          └── actions/
   └── core/tts.py                ├── browser.py  (Playwright/Edge)
                                  └── os_control.py (PyAutoGUI)
```

**Perception-action loop** (`agent.py:run_agent`):
1. Screenshot → Vision model (Gemini) describes the screen with normalized `[ymin, xmin, ymax, xmax]` bounding boxes per element (0–1000 scale)
2. Screen description → Decision model (DeepSeek) returns a single JSON action
3. Action is executed; wait 0.8s for UI to settle; repeat
4. Loop exits when agent returns `{"type": "done"}` or after 15 iterations

**Two AI models, two roles:**
- `vision_client` (OpenRouter/Gemini): vision — converts screenshots to text descriptions with bounding boxes
- `decision_client` (Featherless/DeepSeek): reasoning — picks the next OS/browser action from the text description

## Key Design Decisions

**Context field on actions:** Every action has `"context": "browser"` or `"context": "os"`. Browser actions (`open_url`, `click_element`) route to Playwright; everything else routes to PyAutoGUI. This prevents the agent from mistakenly using CSS selectors on desktop apps.

**Coordinate system:** Bounding boxes are normalized 0–1000. `os_control.move_and_click` converts to real pixels using screen dimensions. `click_box` action computes center point from `[ymin, xmin, ymax, xmax]`.

**Persistent browser session:** Playwright uses a persistent context stored in `.browser_profile/` so login cookies survive between runs. It opens Microsoft Edge (`channel="msedge"`), not Chromium.

**User input handoff:** When the agent sees a login page it emits `request_user_input`. This sets `state = "waiting_for_input"`, blocks the agent thread on `user_reply_event`, and the widget switches to a yellow "waiting" state. The next hold-to-talk press resumes the agent with the voice reply injected into the conversation.

**`type_text` uses clipboard paste** (not `typewrite`) to handle Unicode, `@`, spaces, and special characters reliably.

**Debug screenshots** are saved to `debug/` on every step: `step_N_original.png` (raw) and `step_N_vision_boxes.png` (with bounding boxes drawn in red).

## Extending the Agent

To add a new action:
1. Implement the function in `actions/browser.py` or `actions/os_control.py`
2. Add a `case` in `actions/__init__.py:execute_action`
3. Document the action JSON shape in the `SYSTEM_PROMPT` string in `agent.py`

## Frontend

`frontend/` is a separate Vite + React + Tailwind project (a Figma-exported UI mockup). It is **not connected** to the Python backend — it's a static UI demo only. Run it with:
```bash
cd frontend
npm install   # or pnpm install
npm run dev
```


  # AI Agent Console Interface

  This is a code bundle for AI Agent Console Interface. The original project is available at https://www.figma.com/design/x59eA3ffxf5uMXngwe6hY8/AI-Agent-Console-Interface.

# Orbit Voice Assistant

A Cluely-style floating desktop widget that lets blind (and sighted) users control their Windows computer entirely by voice. Press a hotkey, say what you want, and watch the computer do it.

## Features
- **Global Hotkey:** Press `Ctrl+Shift+Space` anytime, anywhere in Windows to trigger.
- **Natural Language:** Speak naturally (e.g., "Open a new Google Doc", "Open Discord and say hello").
- **Agentic Loop:** The AI (Gemini 1.5 Flash) operates the OS step-by-step and visually verifies actions via screenshots before taking the next step.
- **Local Control:** Uses PyAutoGUI for OS control and Playwright for browser interactions.
- **Built-in TTS:** Speaks the final result back to you.

## Setup & Installation

### Prerequisites
- Windows 10/11
- Python 3.10+
- OpenRouter account with credits (openrouter.ai)

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### Step 2: Configure API Key
1. Create a `.env` file based on `.env.example`.
2. Add your OpenRouter API key.

### Step 3: Run
```bash
python widget.py
```
> Press `Ctrl+Shift+Space` to start recording. Press again to stop and execute.

---
See `DOCS.md` for full architectural details and technical implementation.

  
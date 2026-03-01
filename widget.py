import tkinter as tk
import threading
import queue
import ctypes
from ctypes import c_int, sizeof, POINTER, pointer, Structure, byref
from core import hotkey, audio, state
from agent import run_agent
import pygame
import agent as agent_module


# ── Windows Composition Structures ──────────────────────────────────────────

class ACCENTPOLICY(Structure):
    _fields_ = [
        ("AccentState", c_int),
        ("AccentFlags", c_int),
        ("GradientColor", c_int),
        ("AnimationId", c_int),
    ]

class WINDOWCOMPOSITIONATTRIBDATA(Structure):
    _fields_ = [
        ("Attribute", c_int),
        ("Data", POINTER(ACCENTPOLICY)),
        ("SizeOfData", c_int),
    ]


# ── Windows Visual Effects ──────────────────────────────────────────────────

def apply_acrylic_blur(hwnd, tint_abgr=0x35141416):
    policy = ACCENTPOLICY()
    policy.AccentState = 4
    policy.GradientColor = tint_abgr

    data = WINDOWCOMPOSITIONATTRIBDATA()
    data.Attribute = 19
    data.Data = pointer(policy)
    data.SizeOfData = sizeof(policy)
    ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, pointer(data))


def apply_rounded_corners(hwnd):
    preference = c_int(2)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, 33, pointer(preference), sizeof(preference)
    )


def apply_mica_if_available(hwnd):
    try:
        val = c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, byref(val), sizeof(val))
    except Exception:
        pass


# ── Canvas Helpers ──────────────────────────────────────────────────────────

T_COLOR = "#000001" # Transparent keyed color


def create_round_rect(canvas, x1, y1, x2, y2, radius=25, **kw):
    r = radius
    pts = [
        x1 + r, y1,   x2 - r, y1,
        x2, y1,  x2, y1 + r,
        x2, y2 - r,  x2, y2,
        x2 - r, y2,  x1 + r, y2,
        x1, y2,  x1, y2 - r,
        x1, y1 + r,  x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


# ── State Styles ────────────────────────────────────────────────────────────

STATE_STYLES = {
    "idle": {
        "dot": "#10b981", # Emerald
        "text": "#9ca3af", # Gray-400
        "label": "Orbit  ·  Hold to speak",
    },
    "recording": {
        "dot": "#ef4444", # Red
        "text": "#fecaca", # Red-200
        "label": "Listening…",
    },
    "thinking": {
        "dot": "#8b5cf6", # Violet
        "text": "#e9d5ff", # Purple-200
        "label": "Thinking…",
    },
    "waiting_for_input": {
        "dot": "#3b82f6", # Blue
        "text": "#bfdbfe", # Blue-200
        "label": "What should I say?",
    },
    "done": {
        "dot": "#10b981",
        "text": "#9ca3af",
        "label": "Done",
    },
}


# ── Main Widget ─────────────────────────────────────────────────────────────

class VoiceWidget:
    W, H = 340, 56
    DOT_R = 5
    DOT_X = 24

    def __init__(self, root: tk.Tk):
        self.root = root
        self._setup_window()
        self._build_canvas()
        self._init_drag()

        self._pre_record_state = "idle"
        self.msg_queue: queue.Queue = queue.Queue()

        print("Registering global hold-to-talk hotkey…")
        hotkey.listen(self.on_hotkey_start, self.on_hotkey_stop)

        try:
            pygame.mixer.init()
        except Exception as e:
            print(f"[Widget] pygame init failed: {e}")

        self.set_ui_state("idle")
        self._tick()
        print("Initialization complete. Orbit is ready.")

    def _setup_window(self):
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=T_COLOR)
        self.root.attributes("-transparentcolor", T_COLOR)

        sx = self.root.winfo_screenwidth()
        x = (sx - self.W) // 2
        self.root.geometry(f"{self.W}x{self.H}+{x}+14")
        self.root.update_idletasks()

        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        apply_mica_if_available(hwnd)
        apply_acrylic_blur(hwnd, tint_abgr=0x35141416)
        apply_rounded_corners(hwnd)

    def _build_canvas(self):
        self.canvas = tk.Canvas(
            self.root, bg=T_COLOR, highlightthickness=0,
            width=self.W, height=self.H,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        cy = self.H // 2

        # Status dot — slightly larger and centered
        dr = self.DOT_R
        self.dot = self.canvas.create_oval(
            self.DOT_X - dr, cy - dr, self.DOT_X + dr, cy + dr,
            fill="#10b981", outline="",
        )

        # Label text — crisp modern font
        self.label = self.canvas.create_text(
            self.DOT_X + 18, cy,
            text="Orbit  ·  Hold to speak",
            fill="#9ca3af",
            font=("Segoe UI Variable Display Semibold", 10),
            anchor="w",
        )

    # ── Drag ────────────────────────────────────────────────────────────────

    def _init_drag(self):
        self._dx = 0
        self._dy = 0
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)

    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        x = self.root.winfo_pointerx() - self._dx
        y = self.root.winfo_pointery() - self._dy
        self.root.geometry(f"+{x}+{y}")

    # ── UI State ────────────────────────────────────────────────────────────

    def _truncate(self, text, mx=38):
        return text if len(text) <= mx else text[: mx - 1] + "…"

    def set_label(self, text, color=None):
        cur = state.state.get_state()
        c = color or STATE_STYLES.get(cur, STATE_STYLES["idle"])["text"]
        self.canvas.itemconfig(self.label, text=self._truncate(text), fill=c)

    def set_ui_state(self, new_state):
        state.state.set_state(new_state)
        s = STATE_STYLES.get(new_state, STATE_STYLES["idle"])

        self.canvas.itemconfig(self.dot, fill=s["dot"])
        self.set_label(s["label"], s["text"])

        if new_state == "done":
            self.root.after(3000, lambda: self.set_ui_state("idle"))

    # ── Hotkey Handlers ─────────────────────────────────────────────────────

    def on_hotkey_start(self):
        cur = state.state.get_state()
        print(f"[Hotkey] Pressed. Current state: {cur}")

        if cur in ("idle", "done", "waiting_for_input"):
            self._pre_record_state = cur
            try:
                pygame.mixer.music.load("sounds/Note_block_bell.mp3")
                pygame.mixer.music.play()
            except Exception:
                pass

            state.state.set_state("recording")
            self.msg_queue.put({"type": "state", "val": "recording"})
            threading.Thread(target=audio.start_recording, daemon=True).start()

    def on_hotkey_stop(self):
        cur = state.state.get_state()
        print(f"[Hotkey] Released. Current state: {cur}")

        if cur == "recording":
            was_waiting = self._pre_record_state == "waiting_for_input"
            state.state.set_state("thinking")
            self.msg_queue.put({"type": "state", "val": "thinking"})

            def process():
                audio_data = audio.stop_recording()
                # transcript = audio.transcribe(audio_data)
                
                #hardcoded example
                transcript = "Open Spotify and play the song 'Shape of You' by Ed Sheeran"
                if transcript.strip():
                    self.msg_queue.put({"type": "text", "val": transcript})
                    print(f"\n[You] {transcript}\n")

                    if was_waiting:
                        agent_module.user_reply_text = transcript
                        agent_module.user_reply_event.set()
                    else:
                        run_agent(
                            transcript,
                            update_log_callback=lambda m: self.msg_queue.put(
                                {"type": "text", "val": m}
                            ),
                        )
                        state.state.set_state("done")
                        self.msg_queue.put({"type": "state", "val": "done"})
                else:
                    print("\n[System] No speech detected.\n")
                    state.state.set_state("done")
                    self.msg_queue.put({"type": "state", "val": "done"})

            threading.Thread(target=process, daemon=True).start()

    # ── Main Loop ───────────────────────────────────────────────────────────

    def _tick(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                if msg["type"] == "state":
                    self.set_ui_state(msg["val"])
                elif msg["type"] == "text":
                    self.set_label(msg["val"])
        except queue.Empty:
            pass

        self.root.after(100, self._tick)


# ── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Starting Orbit Voice Assistant…")
    root = tk.Tk()
    print("Initializing GUI window…")
    app = VoiceWidget(root)
    print("=" * 58)
    print("Widget started! Look for the floating window.")
    print("Press Ctrl+Shift+Space to start recording.")
    print("=" * 58)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("\nExiting…")
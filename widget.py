import sys
import threading
import queue
import ctypes
from ctypes import c_int, sizeof, POINTER, pointer, Structure, byref
import datetime

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QObject
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QMouseEvent, QFontMetrics, QLinearGradient
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QScrollArea, QFrame, QSizePolicy)

from core import hotkey, audio, state
from agent import run_agent
import agent as agent_module
import pygame


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

def apply_acrylic(hwnd) -> None:
    # A bit more opaque tint so text is readable, but still very translucent
    tint_abgr = 0x600F0F14 
    policy = ACCENTPOLICY()
    policy.AccentState = 4           # ACCENT_ENABLE_ACRYLICBLURBEHIND
    policy.GradientColor = tint_abgr
    data = WINDOWCOMPOSITIONATTRIBDATA()
    data.Attribute = 19
    data.Data = pointer(policy)
    data.SizeOfData = sizeof(policy)
    ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, pointer(data))

def apply_rounded_corners(hwnd):
    preference = c_int(2)           # DWMWCP_ROUND
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, 33, pointer(preference), sizeof(preference)
    )

def glass_window(hwnd):
    try:
        val = c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, byref(val), sizeof(val))
    except Exception:
        pass
    apply_acrylic(hwnd)
    apply_rounded_corners(hwnd)


# ── Design Tokens ────────────────────────────────────────────────────────────

GLOW_IDLE    = QColor(0,0,0,0)  # transparent when idle
GLOW_ACTIVE  = QColor(14, 165, 233, 100) # Faded navy/sky blue glow effect

STATE_GLOWS = {
    "idle":              GLOW_IDLE,
    "recording":         GLOW_ACTIVE,
    "thinking":          GLOW_ACTIVE,
    "waiting_for_input": GLOW_ACTIVE,
    "done":              GLOW_ACTIVE,
}

STATE_LABELS = {
    "idle":              "Holding to speak",
    "recording":         "Listening...",
    "thinking":          "Thinking...",
    "waiting_for_input": "Waiting for reply...",
    "done":              "Done",
}

WIDGET_W = 440
COLLAPSED_H = 56
EXPANDED_H = 480


# ── Full-Screen Glow Window ───────────────────────────────────────────────────

class BorderGlowWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Click-through
        hwnd = int(self.winId())
        ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex_style | 0x00000020 | 0x00080000)
        
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        
        self.glow_color = GLOW_IDLE
        self.show()

    def set_color(self, color):
        self.glow_color = color
        self.update()

    def paintEvent(self, event):
        if self.glow_color.alpha() == 0:
            return
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        t = 24  # Faded glow edge thickness
        w, h = self.width(), self.height()
        painter.setPen(Qt.PenStyle.NoPen)
        
        # Top edge
        grad_t = QLinearGradient(0, 0, 0, t)
        grad_t.setColorAt(0, self.glow_color)
        grad_t.setColorAt(1, QColor(0,0,0,0))
        painter.setBrush(QBrush(grad_t))
        painter.drawRect(0, 0, w, t)
        
        # Bottom edge
        grad_b = QLinearGradient(0, h, 0, h-t)
        grad_b.setColorAt(0, self.glow_color)
        grad_b.setColorAt(1, QColor(0,0,0,0))
        painter.setBrush(QBrush(grad_b))
        painter.drawRect(0, h-t, w, t)
        
        # Left edge
        grad_l = QLinearGradient(0, 0, t, 0)
        grad_l.setColorAt(0, self.glow_color)
        grad_l.setColorAt(1, QColor(0,0,0,0))
        painter.setBrush(QBrush(grad_l))
        painter.drawRect(0, 0, t, h)
        
        # Right edge
        grad_r = QLinearGradient(w, 0, w-t, 0)
        grad_r.setColorAt(0, self.glow_color)
        grad_r.setColorAt(1, QColor(0,0,0,0))
        painter.setBrush(QBrush(grad_r))
        painter.drawRect(w-t, 0, t, h)


# ── Unified Glass Layout ──────────────────────────────────────────────────────

class MainGlassWidget(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Fix starting geometry
        self.setFixedSize(WIDGET_W, COLLAPSED_H)
        
        screen = QApplication.primaryScreen().geometry()
        self.move((screen.width() - WIDGET_W) // 2, 14)
        
        hwnd = int(self.winId())
        glass_window(hwnd)

        self._drag_pos = None
        self.is_expanded = False
        
        # Main layout spanning entire glass frame
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(16, 0, 16, 0)
        self.main_layout.setSpacing(0)
        
        # -- Top Bar --
        self.top_bar = QWidget()
        self.top_bar.setFixedHeight(COLLAPSED_H)
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        # Status Dot
        self.dot = QWidget()
        self.dot.setFixedSize(8, 8)
        self.dot.setStyleSheet("background-color: #cbd5e1; border-radius: 4px;")
        top_layout.addWidget(self.dot)
        
        top_layout.addSpacing(8)
        
        # Title Label
        self.title_lbl = QLabel(STATE_LABELS["idle"])
        self.title_lbl.setStyleSheet("color: #e2e8f0; font-family: 'Segoe UI Variable Display'; font-size: 14px; font-weight: 600;")
        top_layout.addWidget(self.title_lbl)
        
        top_layout.addStretch()
        
        # Chevron Button
        self.chevron = QLabel("›")
        self.chevron.setStyleSheet("color: #94a3b8; font-family: 'Segoe UI'; font-size: 18px;")
        self.chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chevron.mousePressEvent = self._toggle_expand
        top_layout.addWidget(self.chevron)
        
        self.main_layout.addWidget(self.top_bar)
        
        # -- Expanded Area --
        self.expanded_area = QWidget()
        expanded_layout = QVBoxLayout(self.expanded_area)
        expanded_layout.setContentsMargins(0, 0, 0, 16) # Bottom margin
        expanded_layout.setSpacing(8)
        
        # Separator
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 20);")
        expanded_layout.addWidget(sep)
        
        # Title & Clear button row
        act_row = QWidget()
        act_layout = QHBoxLayout(act_row)
        act_layout.setContentsMargins(0, 4, 0, 4)
        
        act_title = QLabel("Activity Log")
        act_title.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: bold; text-transform: uppercase;")
        act_layout.addWidget(act_title)
        
        act_layout.addStretch()
        
        clear_btn = QLabel("Clear")
        clear_btn.setStyleSheet("color: #64748b; font-size: 11px;")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.mousePressEvent = lambda e: self.clear_steps()
        act_layout.addWidget(clear_btn)
        
        expanded_layout.addWidget(act_row)
        
        # Scroll Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("""
            QScrollArea { background: transparent; }
            QScrollBar:vertical { background: transparent; width: 4px; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,50); border-radius: 2px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 8, 0)
        self.scroll_layout.setSpacing(6)
        self.scroll_layout.addStretch()
        
        self.scroll.setWidget(self.scroll_content)
        expanded_layout.addWidget(self.scroll)
        
        self.main_layout.addWidget(self.expanded_area)
        self.expanded_area.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        
        # Pure liquid glass background (managed partially by OS blur)
        # We draw an extremely light fill to let background shine fully
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 15, 20, 60)) 
        painter.drawRoundedRect(rect, 16, 16)
        
        # A single minimal 1px white border (non-glowing, minimal)
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Shift 0.5 to keep lines perfectly crisp on some DPIs
        painter.drawRoundedRect(0, 0, rect.width()-1, rect.height()-1, 16, 16)
        
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            # Only drag if clicking top bar area
            if event.pos().y() <= COLLAPSED_H:
                if event.pos().x() < WIDGET_W - 40: # Leave chevron clickable
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        
    def _toggle_expand(self, event):
        self.set_expanded(not self.is_expanded)
        
    def set_expanded(self, state: bool):
        self.is_expanded = state
        if self.is_expanded:
            self.chevron.setText("⌄")
            self.expanded_area.show()
            self.setFixedSize(WIDGET_W, EXPANDED_H)
        else:
            self.chevron.setText("›")
            self.expanded_area.hide()
            self.setFixedSize(WIDGET_W, COLLAPSED_H)
            
    def add_step(self, step):
        # A clean minimal text layout
        # [time] (small gray)
        # **TYPE** Content
        
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        
        # Color code only the heading text slightly to break monotony
        type_color = "#94a3b8"
        if step["type"] == "THINKING": type_color = "#c084fc"
        elif step["type"] == "ACTION": type_color = "#fb923c"
        elif step["type"] == "RESULT": type_color = "#34d399"
        elif step["type"] == "USER":   type_color = "#e2e8f0"
        
        lbl_type = QLabel(step["type"])
        lbl_type.setStyleSheet(f"color: {type_color}; font-size: 11px; font-weight: bold;")
        header_row.addWidget(lbl_type)
        
        header_row.addStretch()
        
        lbl_ts = QLabel(step["ts"])
        lbl_ts.setStyleSheet("color: #475569; font-size: 10px; font-family: Consolas;")
        header_row.addWidget(lbl_ts)
        
        layout.addLayout(header_row)
        
        lbl_content = QLabel(step["content"])
        lbl_content.setWordWrap(True)
        # Smaller uniform font
        lbl_content.setStyleSheet("color: #cbd5e1; font-size: 12px; font-family: 'Segoe UI Variable Text';")
        layout.addWidget(lbl_content)
        
        count = self.scroll_layout.count()
        self.scroll_layout.insertWidget(count - 1, card)
        QTimer.singleShot(50, self.scroll_to_bottom)

    def scroll_to_bottom(self):
        scrollbar = self.scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_steps(self):
        while self.scroll_layout.count() > 1:
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


# ── Core Controller ───────────────────────────────────────────────────────────

class VoiceWidget(QObject):
    def __init__(self):
        super().__init__()
        self._pre_record_state = "idle"
        self.msg_queue = queue.Queue()

        self.glow_win = BorderGlowWindow()
        self.main_win = MainGlassWidget(self)
        self.main_win.show()

        print("Registering hold-to-talk hotkey…")
        hotkey.listen(self.on_hotkey_start, self.on_hotkey_stop)

        try:
            pygame.mixer.init()
        except Exception as e:
            print(f"[Widget] pygame init error: {e}")

        self.set_ui_state("idle")
        
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)
        print("Orbit ready.")

    def _classify(self, msg: str) -> tuple[str, str]:
        m = msg.lower()
        if "🧠" in msg: return "THINKING", msg.replace("🧠 Agent Thought: ", "").strip()
        if "🤖" in msg: return "ACTION", msg.replace("🤖 Agent Action: ", "").strip()
        if "✅" in msg: return "RESULT", msg.replace("✅ ", "").strip()
        if "🎤" in msg: return "USER", msg.replace("🎤 ", "").strip()
        if "[system]" in m or "[plan]" in m or "[step" in m: return "SYSTEM", msg
        return "SYSTEM", msg

    def set_ui_state(self, new_state):
        state.state.set_state(new_state)
        
        glow = STATE_GLOWS.get(new_state, GLOW_IDLE)
        label_text = STATE_LABELS.get(new_state, "Holding to speak")
        
        # Minimal UI update
        self.main_win.title_lbl.setText(label_text)
        
        # Color dot matching glow (or subtle gray if idle)
        dot_c = glow.name() if new_state != "idle" else "#64748b"
        self.main_win.dot.setStyleSheet(f"background-color: {dot_c}; border-radius: 4px;")
        
        # Only the edge of the monitor pulses
        self.glow_win.set_color(glow)

        if new_state in ("thinking", "recording") and not self.main_win.is_expanded:
            self.main_win.set_expanded(True)

        if new_state == "done":
            QTimer.singleShot(3500, lambda: self.set_ui_state("idle"))

    def set_label(self, text):
        max_len = 50
        trunc = text if len(text) <= max_len else text[:max_len-1] + "…"
        self.main_win.title_lbl.setText(trunc)

    def _tick(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg["type"]
                if kind == "state":
                    self.set_ui_state(msg["val"])
                elif kind == "text":
                    self.set_label(msg["val"])
                elif kind == "step":
                    msg["ts"] = datetime.datetime.now().strftime("%H:%M:%S")
                    self.main_win.add_step(msg)
                elif kind == "log":
                    st, content = self._classify(msg["val"])
                    step = {"type": st, "content": content, "ts": datetime.datetime.now().strftime("%H:%M:%S")}
                    self.main_win.add_step(step)
        except queue.Empty:
            pass

    def on_hotkey_start(self):
        cur = state.state.get_state()
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
        if cur == "recording":
            was_waiting = self._pre_record_state == "waiting_for_input"
            state.state.set_state("thinking")
            self.msg_queue.put({"type": "state", "val": "thinking"})

            def process():
                audio_data = audio.stop_recording()
                transcript = audio.transcribe(audio_data)
                if transcript.strip():
                    self.msg_queue.put({"type": "text", "val": transcript})
                    self.msg_queue.put({"type": "step", "step_type": "USER", "content": transcript})
                    print(f"\n[You] {transcript}\n")

                    if was_waiting:
                        agent_module.user_reply_text = transcript
                        agent_module.user_reply_event.set()
                    else:
                        run_agent(
                            transcript,
                            update_log_callback=lambda m: self.msg_queue.put({"type": "log", "val": m}),
                        )
                        state.state.set_state("done")
                        self.msg_queue.put({"type": "state", "val": "done"})
                else:
                    print("\n[System] No speech detected.\n")
                    state.state.set_state("done")
                    self.msg_queue.put({"type": "state", "val": "done"})

            threading.Thread(target=process, daemon=True).start()


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # We enforce Segoe UI globally if available
    font = QFont("Segoe UI Variable Display", 11)
    app.setFont(font)
    
    print("Starting Orbit…")
    print("=" * 54)
    print("Orbit started. Hold Ctrl+Shift+Space to speak.")
    print("=" * 54)
    
    widget = VoiceWidget()
    
    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        print("\nExiting…")
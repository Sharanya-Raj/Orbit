import tkinter as tk
import threading
import queue
from core import hotkey, audio, state
from agent import run_agent
import pygame
import agent as agent_module  # needed to set user_reply_text and signal event

BG = '#18181c'  # widget background (transparent outer if possible)

class VoiceWidget:
    def __init__(self, root):
        self.root = root
        
        # UI Setup for the Pill Widget
        self.root.overrideredirect(True)          # No title bar
        self.root.attributes("-topmost", True)    # Always on top
        self.root.configure(bg=BG)
        
        # Position at Top Center initially
        window_width = 300
        window_height = 50
        screen_width = self.root.winfo_screenwidth()
        x = int(screen_width / 2 - window_width / 2)
        y = 20
        self.root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        
        # Build UI Elements
        self.pill_frame = tk.Frame(self.root, bg=BG)
        self.pill_frame.pack(fill=tk.BOTH, expand=True)
        
        self.status_label = tk.Label(
            self.pill_frame, 
            text="🎙️ Orbit (Idle)", 
            fg="#2a2a35", 
            bg=BG, 
            font=("Segoe UI", 12, "bold")
        )
        self.status_label.pack(pady=10)
        
        # Transcript Log Extension
        self.log_frame = tk.Frame(self.root, bg='#1e1e24')
        self.log_text = tk.Text(
            self.log_frame, 
            height=5, 
            bg='#1e1e24', 
            fg='white', 
            font=("Segoe UI", 10),
            state=tk.DISABLED, 
            bd=0
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Draggable logic
        self.pill_frame.bind("<ButtonPress-1>", self.start_drag)
        self.pill_frame.bind("<B1-Motion>", self.do_drag)
        self.status_label.bind("<ButtonPress-1>", self.start_drag)
        self.status_label.bind("<B1-Motion>", self.do_drag)
        
        self.x_offset = 0
        self.y_offset = 0
        self._pre_record_state = "idle"  # tracks state before recording started
        
        # Queue for cross-thread comms
        self.msg_queue = queue.Queue()
        
        # Register global hotkey
        print("Registering global hold-to-talk hotkey...")
        hotkey.listen(self.on_hotkey_start, self.on_hotkey_stop)
        
        # State tick loop
        self.update_ui()
        
        # Initialize pygame mixer once for sound effects
        try:
            pygame.mixer.init()
        except Exception as e:
            print(f"[Widget] pygame init failed (sounds disabled): {e}")
        
        self.set_ui_state("idle")
        print("Initialization complete. Widget is ready.")

    def start_drag(self, event):
        self.x_offset = event.x
        self.y_offset = event.y

    def do_drag(self, event):
        x = self.root.winfo_pointerx() - self.x_offset
        y = self.root.winfo_pointery() - self.y_offset
        self.root.geometry(f"+{x}+{y}")

    def add_log(self, text):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def set_ui_state(self, new_state):
        if new_state == "idle":
            self.status_label.config(text="🎙️ Orbit (Idle)", fg="#2a2a35")
            self.collapse()
        elif new_state == "recording":
            self.status_label.config(text="🎙️ Recording...", fg="#6c63ff")
            self.collapse()
        elif new_state == "thinking":
            self.status_label.config(text="🤖 Thinking...", fg="#a78bfa")
            self.expand()
        elif new_state == "waiting_for_input":
            self.status_label.config(text="⌨️ Type it in, then press hotkey", fg="#fbbf24")
            self.expand()
        elif new_state == "done":
            self.status_label.config(text="✅ Done", fg="#22d3a5")
            # Auto collapse after a few seconds
            self.root.after(3000, lambda: self.set_ui_state("idle"))

    def expand(self):
        """Show transcript log"""
        self.root.geometry("350x150")
        self.log_frame.pack(fill=tk.BOTH, expand=True)

    def collapse(self):
        """Hide transcript log to just pill"""
        self.log_frame.pack_forget()
        
    def on_hotkey_start(self):
        current_state = state.state.get_state()
        print(f"[Hotkey] Pressed. Current state is: {current_state}")
        
        if current_state in ["idle", "done", "waiting_for_input"]:
            self._pre_record_state = current_state  # remember what we were doing before recording
            # Play a short sound to indicate recording started
            try:
                pygame.mixer.music.load("sounds/Note_block_bell.mp3")
                pygame.mixer.music.play()
            except Exception:
                pass  # Sound is optional — don't crash if file is missing
            
            # Start Recording
            state.state.set_state("recording")
            self.msg_queue.put({"type": "state", "val": "recording"})
            threading.Thread(target=audio.start_recording, daemon=True).start()

    def on_hotkey_stop(self):
        current_state = state.state.get_state()
        print(f"[Hotkey] Released. Current state is: {current_state}")
        
        if current_state == "recording":
            was_waiting = self._pre_record_state == "waiting_for_input"
            
            state.state.set_state("thinking")
            self.msg_queue.put({"type": "state", "val": "thinking"})
            
            def process_audio():
                audio_data = audio.stop_recording()
                
                self.msg_queue.put({"type": "log", "val": "[System] Transcribing audio..."})
                transcript = audio.transcribe(audio_data)
                
                if transcript.strip():
                    self.msg_queue.put({"type": "log", "val": f"[System] Transcript: {transcript}"})
                    print(f"\n🎤 You said: {transcript}\n")
                    
                    if was_waiting:
                        # Resume the paused agent with the user's voice reply
                        agent_module.user_reply_text = transcript
                        agent_module.user_reply_event.set()  # unblock agent thread
                        # Agent continues from where it left off — state will be set to done by agent
                    else:
                        # Normal: start a fresh agent task
                        result = run_agent(
                            transcript, 
                            update_log_callback=lambda m: self.msg_queue.put({"type": "log", "val": m})
                        )
                        state.state.set_state("done")
                        self.msg_queue.put({"type": "state", "val": "done"})
                else:
                    self.msg_queue.put({"type": "log", "val": "[System] No speech detected."})
                    print("\n[System] No speech detected.\n")
                    state.state.set_state("done")
                    self.msg_queue.put({"type": "state", "val": "done"})

            threading.Thread(target=process_audio, daemon=True).start()

    def update_ui(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                if msg["type"] == "state":
                    self.set_ui_state(msg["val"])
                elif msg["type"] == "log":
                    self.add_log(msg["val"])
        except queue.Empty:
            pass
            
        # Run 10 times a second
        self.root.after(100, self.update_ui)


if __name__ == "__main__":
    print("Starting Orbit Voice Assistant...")
    root = tk.Tk()
    print("Initializing GUI window...")
    app = VoiceWidget(root)
    print("==========================================================")
    print("Widget started! Look for the small floating pill at the top of your screen.")
    print("Press Ctrl+Shift+Space to start recording.")
    print("==========================================================")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("\nExiting...")

import sys
import time
import threading

from PyQt6.QtWidgets import QApplication
from widget import VoiceWidget

if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = VoiceWidget()

    def simulate_agent():
        print("Simulating agent...")
        time.sleep(1)
        widget.msg_queue.put({"type": "state", "val": "recording"})
        widget.msg_queue.put({"type": "text", "val": "Listening..."})
        time.sleep(2)
        
        widget.msg_queue.put({"type": "state", "val": "thinking"})
        widget.msg_queue.put({"type": "text", "val": "Thinking..."})
        widget.msg_queue.put({"type": "log", "val": "[System] Initialized agent"})
        time.sleep(1)
        
        widget.msg_queue.put({"type": "step", "step_type": "THINKING", "content": "Let me test the new log UI"})
        time.sleep(1)
        
        widget.msg_queue.put({"type": "step", "step_type": "ACTION", "content": "I am opening the browser"})
        time.sleep(1)
        
        widget.msg_queue.put({"type": "step", "step_type": "RESULT", "content": "Done!"})
        widget.msg_queue.put({"type": "state", "val": "done"})
        widget.msg_queue.put({"type": "text", "val": "Done!"})
        print("Simulation complete.")

    threading.Thread(target=simulate_agent, daemon=True).start()
    
    sys.exit(app.exec())

class AppState:
    def __init__(self):
        # States: "idle" -> "recording" -> "thinking" -> "done"
        self.current = "idle"

    def set_state(self, new_state: str):
        self.current = new_state

    def get_state(self) -> str:
        return self.current

# Global instance to be shared
state = AppState()

import subprocess

def speak(text: str):
    """Speaks text back to the user using Windows' built-in speech synthesis."""
    subprocess.Popen([
        "powershell", "-Command",
        f"Add-Type -AssemblyName System.Speech; "
        f"$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Speak('{text}')"
    ], creationflags=subprocess.CREATE_NO_WINDOW)

if __name__ == "__main__":
    speak("Testing the text to speech engine.")

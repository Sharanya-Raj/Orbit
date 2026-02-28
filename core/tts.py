import subprocess

def speak(text: str):
    """Speaks text to the user using Windows built-in speech synthesis.
    Blocking — waits for speech to finish before returning, so actions
    don't interrupt mid-sentence.
    """
    # Sanitize: remove single quotes to avoid PowerShell injection
    safe_text = text.replace("'", "")
    subprocess.run(
        [
            "powershell", "-Command",
            f"Add-Type -AssemblyName System.Speech; "
            f"$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Speak('{safe_text}')"
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )

if __name__ == "__main__":
    speak("Testing the text to speech engine.")

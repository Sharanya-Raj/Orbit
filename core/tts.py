import subprocess
import os
import io

try:
    import pygame
    from elevenlabs.client import ElevenLabs
    HAS_ELEVENLABS = True
except ImportError:
    HAS_ELEVENLABS = False

el_client = None

def get_client():
    global el_client
    if el_client is None:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            return None
        el_client = ElevenLabs(api_key=api_key)
    return el_client

def speak(text: str):
    """Speaks text to the user. Blocking wait."""
    
    # Try ElevenLabs first if available
    if HAS_ELEVENLABS:
        client = get_client()
        if client:
            try:
                audio_stream = client.text_to_speech.convert(
                    text=text,
                    voice_id="21m00Tcm4TlvDq8ikWAM", # Default 'Rachel' voice
                    model_id="eleven_multilingual_v2",
                    output_format="mp3_44100_128",
                )
                
                # Consume the generator stream into bytes
                audio_bytes = b"".join([chunk for chunk in audio_stream if chunk])
                
                pygame.mixer.init()
                pygame.mixer.music.load(io.BytesIO(audio_bytes))
                pygame.mixer.music.play()
                
                # Blocking — wait for speech to finish before returning
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
                return
                
            except Exception as e:
                print(f"[TTS Error] ElevenLabs TTS failed, falling back to OS TTS: {e}")

    # Fallback: Windows built-in speech synthesis
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

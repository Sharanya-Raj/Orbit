import pyaudio
import numpy as np
import os
import io
import wave
from elevenlabs.client import ElevenLabs

# Global flag for recording state
is_recording = False
audio_data = []
stream = None
actual_samplerate = 16000
pa = None

# Initialize the ElevenLabs client
# Requires ELEVENLABS_API_KEY to be set in the environment (.env)
el_client = None

def get_model():
    # Model loading is no longer required for ElevenLabs API
    # Returning None to satisfy legacy signatures in other files that might call this
    return None

def _callback(in_data, frame_count, time_info, status):
    global is_recording, audio_data
    if is_recording:
        chunk = np.frombuffer(in_data, dtype=np.float32)
        audio_data.append(chunk)
    return (in_data, pyaudio.paContinue)

def start_recording():
    global is_recording, audio_data, pa, stream, actual_samplerate
    is_recording = True
    audio_data = []

    actual_samplerate = 16000
    pa = pyaudio.PyAudio()
    
    stream = pa.open(format=pyaudio.paFloat32,
                     channels=1,
                     rate=16000,
                     input=True,
                     frames_per_buffer=1024,
                     stream_callback=_callback)
    stream.start_stream()

def stop_recording() -> np.ndarray:
    global is_recording, audio_data, pa, stream
    is_recording = False
    
    if stream is not None:
        stream.stop_stream()
        stream.close()
        stream = None
        
    if pa is not None:
        pa.terminate()
        pa = None
    
    # Wait for the stream to fully process the last chunk
    if len(audio_data) > 0:
        audio = np.concatenate(audio_data, axis=0).flatten()
        return audio
        
    return np.array([])

def transcribe(audio_array: np.ndarray, model=None) -> str:
    """Uses ElevenLabs Scribe API to transcribe the numpy audio array into text."""
    global el_client
    if len(audio_array) == 0:
        return ""
        
    if el_client is None:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            print("[STT Error] ELEVENLABS_API_KEY environment variable is not set.")
            return "(Transcription failed: No ElevenLabs API key)"
        el_client = ElevenLabs(api_key=api_key)

    try:
        # ElevenLabs expects a standard audio file format (like mp3, wav, flac).
        # We need to convert our float32 numpy array into an in-memory WAV file (int16).
        
        # 1. Normalize and scale to int16 format
        audio_normalized = np.clip(audio_array, -1.0, 1.0)
        audio_int16 = (audio_normalized * 32767).astype(np.int16)
        
        # 2. Write to an in-memory BytesIO buffer as a WAV file
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)           # Mono
            wav_file.setsampwidth(2)           # 2 bytes per sample (int16)
            wav_file.setframerate(16000)       # 16kHz
            wav_file.writeframes(audio_int16.tobytes())
            
        wav_buffer.seek(0)

        # 3. Send to ElevenLabs Speech-to-Text API
        print("[STT] Sending audio to ElevenLabs for transcription...")
        response = el_client.speech_to_text.convert(
            file=wav_buffer,
            model_id="scribe_v2"
        )
        
        return response.text.strip()
        
    except Exception as e:
        print(f"[STT Error] Speech-to-text failed: {e}")
        return f"(Transcription error: {str(e)})"

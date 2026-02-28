import sounddevice as sd
import numpy as np
import whisper

# Global flag for recording state
is_recording = False
audio_data = []

def get_model():
    # Load whisper model (tiny for speed, base/small for better accuracy)
    return whisper.load_model("tiny")

def start_recording():
    global is_recording, audio_data
    is_recording = True
    audio_data = []
    
    def callback(indata, frames, time, status):
        if is_recording:
            audio_data.append(indata.copy())
            
    # Standard recording settings
    # whisper expects 16000Hz, mono
    sd.InputStream(samplerate=16000, channels=1, callback=callback).start()

def stop_recording() -> np.ndarray:
    global is_recording, audio_data
    is_recording = False
    
    # Wait for the stream to fully process the last chunk
    if len(audio_data) > 0:
        return np.concatenate(audio_data, axis=0).flatten()
    return np.array([])

def transcribe(audio_array: np.ndarray, model=None) -> str:
    """Uses whisper to transcribe the numpy audio array into text."""
    if len(audio_array) == 0:
        return ""
    
    if model is None:
        model = get_model()
        
    # whisper requires float32 between -1 and 1
    audio_float = audio_array.astype(np.float32)

    # Pad/trim audio to whisper expected format if necessary
    audio = whisper.pad_or_trim(audio_float)
    
    # make log-Mel spectrogram and move to the same device as the model
    mel = whisper.log_mel_spectrogram(audio).to(model.device)
    
    # decode the audio
    options = whisper.DecodingOptions(fp16=False)
    result = whisper.decode(model, mel, options)
    
    return result.text

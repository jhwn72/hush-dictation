"""Microphone capture at the device's native rate, resampled to 16 kHz mono for Whisper."""
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

TARGET_SR = 16000
SILENCE_RMS = 0.0004   # below this the mic is muted/dead; anything louder goes to Whisper's own speech detector
SPEECH_TARGET = 0.06   # speaking level we normalize quiet recordings (whispers) up to
MAX_GAIN = 40.0


def list_mics() -> list[dict]:
    out = []
    try:
        default_in = sd.default.device[0]
        hostapis = sd.query_hostapis()
        seen = set()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] < 1:
                continue
            api = hostapis[d["hostapi"]]["name"]
            # One entry per device: prefer the default host API (MME on Windows, CoreAudio on macOS)
            if d["hostapi"] != sd.default.hostapi and i != default_in:
                continue
            if d["name"] in seen:
                continue
            seen.add(d["name"])
            out.append({"id": i, "name": d["name"], "api": api, "default": i == default_in})
    except Exception:  # PortAudio errors when no audio hardware is present
        pass
    return out


def resample(audio: np.ndarray, sr: int) -> np.ndarray:
    if sr == TARGET_SR or audio.size == 0:
        return audio.astype(np.float32)
    if sr % TARGET_SR == 0:
        k = sr // TARGET_SR
        n = audio.size // k * k
        return audio[:n].reshape(-1, k).mean(axis=1).astype(np.float32)
    duration = audio.size / sr
    t_new = np.linspace(0, duration, int(duration * TARGET_SR), endpoint=False)
    t_old = np.arange(audio.size) / sr
    return np.interp(t_new, t_old, audio).astype(np.float32)


class Recorder:
    def __init__(self, device=None):
        self.device = device
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None
        self._sr = TARGET_SR
        self.level = 0.0  # smoothed, auto-ranged loudness 0..1 for the UI meter
        self._peak = 0.004

    @property
    def active(self):
        return self._stream is not None

    def _cb(self, indata, frames, time_info, status):
        mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        with self._lock:
            self._chunks.append(mono)
        rms = float(np.sqrt(np.mean(mono ** 2))) if mono.size else 0.0
        # meter auto-ranges to the loudest recent sound, so whispering still moves the waveform
        self._peak = max(rms, self._peak * 0.995, 0.004)
        self.level = self.level * 0.6 + min(1.0, rms / self._peak * 0.85) * 0.4

    def start(self):
        if self._stream:
            return
        with self._lock:
            self._chunks = []
        self._peak = 0.004
        dev = self.device if self.device not in ("", None) else None
        info = sd.query_devices(dev, "input")
        self._sr = int(info["default_samplerate"]) or 48000
        self._stream = sd.InputStream(
            device=dev, samplerate=self._sr, channels=1, dtype="float32", blocksize=int(self._sr * 0.03), callback=self._cb
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        stream, self._stream = self._stream, None
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self.level = 0.0
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            self._chunks = []
        return resample(audio, self._sr)

    def snapshot_seconds(self) -> float:
        with self._lock:
            return sum(c.size for c in self._chunks) / self._sr

    def peek(self) -> np.ndarray:
        """Everything recorded so far at 16 kHz, without stopping (for streaming transcription). Resampled the
        same way stop() does, so sample positions line up with the final audio."""
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
        return resample(audio, self._sr)


def save_wav(path: Path, audio: np.ndarray, sr: int = TARGET_SR):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        pcm = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1)
    return resample(pcm, sr)


def normalize(pcm: np.ndarray) -> np.ndarray:
    """Whisper mode: raise quiet speech to a normal level. The loudest 10% of 30 ms frames stand in for the
    speech level, so long pauses don't fool it; a tanh soft-limiter keeps boosted peaks from clipping."""
    if pcm.size < 480:
        return pcm
    n = pcm.size // 480 * 480
    frames = np.sqrt(np.mean(pcm[:n].reshape(-1, 480) ** 2, axis=1))
    level = float(np.percentile(frames, 90))
    if level <= 0 or level >= SPEECH_TARGET * 0.7:
        return pcm  # already at speaking level
    gain = min(MAX_GAIN, SPEECH_TARGET / level)
    return (np.tanh(pcm * gain * 1.2) / np.tanh(1.2)).astype(np.float32)  # Whisper's VAD requires float32


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0

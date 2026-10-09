"""Quiet-speech check: synthesized speech at decreasing volume (+ room noise) through the silence gate and Whisper."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np

from hush import audio
from hush.config import DEFAULTS
from hush.stt import Transcriber

TEXT = "can you move the standup to ten tomorrow and tell the team"
wav = os.path.join(tempfile.gettempdir(), "hush_whisper_test.wav")
ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
      f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('{TEXT}'); $s.Dispose()")
subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
clean = audio.load_wav(Path(wav))
clean = clean / np.sqrt(np.mean(clean ** 2)) * 0.06  # normal speaking level

stt = Transcriber(dict(DEFAULTS))
stt.load()
rng = np.random.default_rng(0)
use_norm = "--norm" in sys.argv
for gain in (1.0, 0.25, 0.1, 0.05, 0.025, 0.012, 0.006):
    pcm = clean * gain + rng.normal(0, 0.0008, clean.size).astype(np.float32)  # quiet room hiss
    rms = audio.rms(pcm)
    gated = rms < audio.SILENCE_RMS if hasattr(audio, "SILENCE_RMS") else rms < 0.0025
    if use_norm:
        pcm = audio.normalize(pcm)
    text = "(dropped as silence)" if gated else stt.transcribe(pcm)
    print(f"volume x{gain:<5} rms={rms:.4f}  ->  {text}")

# noise only (nobody talking): must not hallucinate words once boosted
for noise in (0.0008, 0.003):
    pcm = rng.normal(0, noise, 16000 * 4).astype(np.float32)
    rms = audio.rms(pcm)
    if rms < audio.SILENCE_RMS:
        print(f"noise only rms={rms:.4f}  ->  (dropped as silence)")
        continue
    if use_norm:
        pcm = audio.normalize(pcm)
    print(f"noise only rms={rms:.4f}  ->  {stt.transcribe(pcm)!r}")

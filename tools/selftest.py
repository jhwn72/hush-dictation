"""End-to-end check without the GUI: Windows TTS speech -> Whisper -> dictionary -> Ollama cleanup -> style.
Run: .venv\\Scripts\\python tools\\selftest.py"""
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np

from hush import audio, textproc
from hush.config import DEFAULTS
from hush.llm import LLM
from hush.stt import Transcriber

SAY = [
    "um so I was thinking we could uh meet on thursday at two actually no make that three if that works for you",
    "can you make the sales counter adjust between periods like right now its only thirty days but I want a daily view and the different quarters",
    "will this take a lot of credits every time it runs or is it automatic",
    "btw the pixel forge team wants the new drop live by friday",
]


def tts(text):
    path = os.path.join(tempfile.gettempdir(), f"flow_tts_{abs(hash(text))}.wav")
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SetOutputToWaveFile('{path}'); $s.Speak(\"{text}\"); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    with wave.open(path) as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1)
    return audio.resample(pcm, sr)


s = dict(DEFAULTS)
stt = Transcriber(s)
stt.load()
print("stt:", stt.info())
llm = LLM(s)
print("llm:", llm.status())
llm.warm()
entries = [{"phrase": "PIXELFORGE", "replacement": None}, {"phrase": "btw", "replacement": "by the way"}]
for say in SAY:
    pcm = tts(say)
    t0 = time.time()
    raw = stt.transcribe(pcm, prompt="PIXELFORGE")
    t1 = time.time()
    text, dfx = textproc.apply_dictionary(raw, entries)
    clean = llm.cleanup(text, "formal", "ai", "Claude", ["PIXELFORGE"])
    t2 = time.time()
    clean, more = textproc.apply_dictionary(clean, entries)
    final = textproc.apply_style(clean, "formal")
    print(f"\nRAW   ({t1 - t0:.2f}s for {len(pcm) / 16000:.1f}s audio): {raw}\nFINAL ({t2 - t1:.2f}s llm): {final}"
          f"\nfixes={textproc.diff_fixes(raw, final)} dict={dfx + more}")

sample = "Hey, are you free for lunch tomorrow? Let's do 12 if that works for you."
print("\ncasual:     ", textproc.apply_style(sample, "casual"))
print("very casual:", textproc.apply_style(sample, "very_casual"))
print("snippet:    ", textproc.apply_snippets("My LinkedIn.", [{"trigger": "my LinkedIn", "expansion": "https://linkedin.com/in/x"}]))
print("dictionary: ", textproc.apply_dictionary("the pixelforge and Pixel Forge team", entries))

"""Streaming vs one-shot on a long dictation: same words, much less waiting after release.
Feeds a ~50 s synthesized ramble into the streamer in real time, then measures the wait after "release"."""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np

from hush import audio
from hush.config import DEFAULTS
from hush.streaming import StreamingTranscriber
from hush.stt import Transcriber

TEXT = ("Okay so for the launch plan, first we need the photos done by Tuesday so the product pages can go live. "
        "Then on Wednesday we email the wholesale buyers about the new minimums, and Thursday is the soft launch to the "
        "newsletter list. If the numbers look good we open it up to everyone on Friday morning. "
        "For the budget, keep the ad spend under two thousand dollars for the first week, and split it between "
        "Instagram and TikTok. I also want a quick recap every evening with sales, refunds, and anything weird. "
        "Last thing, make sure the size chart is fixed before any of this goes out, because that caused most of "
        "the returns last time.")
wav = os.path.join(tempfile.gettempdir(), "hush_stream_test.wav")
ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Rate = -1; "
      f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('{TEXT}'); $s.Dispose()")
subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
full = audio.load_wav(Path(wav))
full = full / np.sqrt(np.mean(full ** 2)) * 0.06
print(f"audio: {full.size / 16000:.1f}s")

stt = Transcriber(dict(DEFAULTS))
stt.load()

# one-shot: everything after release
t0 = time.time()
one = stt.transcribe(full)
one_wait = time.time() - t0

# streaming: audio arrives in real time while the streamer works in the background
recorded = {"n": 0}
lock = threading.Lock()


def peek():
    with lock:
        return full[: recorded["n"]]


st = StreamingTranscriber(stt, peek)
step = 1600  # 0.1 s
start = time.time()
while recorded["n"] < full.size:
    with lock:
        recorded["n"] = min(full.size, recorded["n"] + step)
    time.sleep(max(0, start + recorded["n"] / 16000 - time.time()))
t0 = time.time()
streamed = st.finish(full)
stream_wait = time.time() - t0

words = lambda s: [w.strip(".,!?").lower() for w in s.split()]
import difflib
sim = difflib.SequenceMatcher(a=words(one), b=words(streamed)).ratio()
print(f"\nONE-SHOT  wait after release: {one_wait:.2f}s\n  {one}\n")
print(f"STREAMING wait after release: {stream_wait:.2f}s\n  {streamed}\n")
print(f"word overlap between the two: {sim:.0%}")

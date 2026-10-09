"""End-to-end meeting test: a scripted two-person call (two synthesized voices), the mic track also picking up
the other side through speakers, through transcription -> Me/Them merge -> summary. --cloud for Claude."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np

from hush import audio
from hush.config import DEFAULTS
from hush.llm import LLM
from hush.meeting import format_transcript, merge_speakers
from hush.stt import Transcriber

CALL = [  # (speaker, start second, line)
    ("me", 0.5, "Hey Priya, thanks for jumping on. Can we push the launch to Friday?"),
    ("them", 6.0, "Friday works, but the photos need to be done by Wednesday."),
    ("me", 11.5, "Okay, I'll ping the photographer today and send you the shot list tonight."),
    ("them", 18.0, "Perfect. I'll move the newsletter to Thursday and update the product pages."),
]
VOICES = {"me": "Microsoft David Desktop", "them": "Microsoft Zira Desktop"}


def tts(text, voice):
    path = os.path.join(tempfile.gettempdir(), f"hush_call_{abs(hash((text, voice)))}.wav")
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"try {{ $s.SelectVoice('{voice}') }} catch {{}}; $s.SetOutputToWaveFile('{path}'); $s.Speak(\"{text}\"); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    pcm = audio.load_wav(Path(path))
    return pcm / np.sqrt(np.mean(pcm ** 2)) * 0.06


length = 26 * 16000
mic, sysa = np.zeros(length, np.float32), np.zeros(length, np.float32)
rng = np.random.default_rng(1)
for who, start, line in CALL:
    pcm = tts(line, VOICES[who])
    i = int(start * 16000)
    if who == "me":
        mic[i:i + pcm.size] += pcm[: length - i]
    else:
        sysa[i:i + pcm.size] += pcm[: length - i]
        j = i + 800  # 50 ms later the speakers' sound reaches the mic, quieter
        mic[j:j + pcm.size] += 0.3 * pcm[: length - j]
mic += rng.normal(0, 0.0008, length).astype(np.float32)

stt = Transcriber(dict(DEFAULTS))
stt.load()
me = stt.transcribe_segments(audio.normalize(mic))
them = stt.transcribe_segments(audio.normalize(sysa))
transcript = format_transcript(merge_speakers(me, them))
print("TRANSCRIPT\n" + transcript + "\n")
lines = transcript.split("\n\n")
ok = (len(lines) == 4 and [l.split(":")[0] for l in lines] == ["Me", "Them", "Me", "Them"])
print("PASS turns interleaved, echo removed" if ok else "FAIL turn structure")

settings = dict(DEFAULTS)
if "--cloud" in sys.argv:
    settings["llm_provider"] = "cloud"
llm = LLM(settings)
t0 = time.time()
notes = llm.summarize_meeting(transcript, me_name="Alex", labeled=True)
print(f"\nSUMMARY ({time.time() - t0:.1f}s, {llm.last_backend})")
for k in ("title", "overview", "key_points", "action_items"):
    print(f"  {k}: {notes.get(k)}")
items = " ".join(notes.get("action_items") or []).lower()
print("PASS owners attributed" if ("alex" in items and ("priya" in items or "them" in items)) else "CHECK action item owners")

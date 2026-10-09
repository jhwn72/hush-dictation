"""Live check of hush.loopback.SystemAudioRecorder: records system audio while a short line plays out loud."""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hush import audio
from hush.loopback import SystemAudioRecorder

r = SystemAudioRecorder()
r.start()
time.sleep(0.8)
subprocess.run(["powershell", "-NoProfile", "-Command",
                "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('Sounds good, talk soon.')"])
time.sleep(0.5)
pcm = r.stop()
print(f"captured {pcm.size / 16000:.1f}s at 16 kHz, rms={audio.rms(pcm):.4f}, error={r.error!r}")
print("PASS" if pcm.size > 16000 and audio.rms(pcm) > 0.002 and not r.error else "FAIL")

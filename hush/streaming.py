"""Streaming transcription for long dictations: transcribe finished stretches of speech while the user is still
talking, cutting only at pauses, so releasing the shortcut leaves just the last few seconds to do.

Short dictations never produce a chunk and behave exactly like a one-shot transcription.
"""
import logging
import threading

import numpy as np

from . import audio as audio_mod
from .textproc import looks_like_hallucination

log = logging.getLogger("flow.stream")

SR = audio_mod.TARGET_SR
MIN_CHUNK = 10.0     # seconds of new audio before we look for a cut
FORCE_CHUNK = 26.0   # cut at the quietest point even without a clear pause (Whisper's window is 30 s)
KEEP_LIVE = 1.0      # never cut inside the last second: the speaker may be mid-word
FRAME = int(0.3 * SR)  # pause detection resolution


def find_cut(audio: np.ndarray, start: int, force: bool) -> int | None:
    """Sample index of the quietest 300 ms between MIN_CHUNK*0.8 after start and KEEP_LIVE before the end,
    if it's quiet enough to be a pause (or force)."""
    lo = start + int(MIN_CHUNK * 0.8 * SR)
    hi = audio.size - int(KEEP_LIVE * SR)
    if hi - lo < FRAME:
        return None
    seg = audio[lo:hi]
    n = seg.size // FRAME
    if n < 1:
        return None
    frames = np.sqrt(np.mean(seg[: n * FRAME].reshape(n, FRAME) ** 2, axis=1))
    speech = float(np.percentile(np.sqrt(np.mean(audio[start:hi][: (hi - start) // FRAME * FRAME]
                                                  .reshape(-1, FRAME) ** 2, axis=1)), 90))
    i = int(np.argmin(frames))
    if force or frames[i] < max(0.002, speech * 0.25):
        return lo + i * FRAME + FRAME // 2
    return None


class StreamingTranscriber:
    def __init__(self, stt, peek, prompt: str = ""):
        self._stt = stt
        self._peek = peek          # () -> audio recorded so far, 16 kHz float32
        self._prompt = prompt      # dictionary vocabulary for Whisper
        self._parts: list[str] = []
        self._cut = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="stream-stt", daemon=True)
        self._thread.start()

    def _prompt_for_next(self) -> str:
        tail = " ".join(self._parts)[-200:]  # previous words keep punctuation and casing continuous across cuts
        return (self._prompt + " " + tail).strip()

    def _run(self):
        while not self._stop.wait(1.0):
            try:
                audio = self._peek()
                new = (audio.size - self._cut) / SR
                if new < MIN_CHUNK:
                    continue
                cut = find_cut(audio, self._cut, force=new >= FORCE_CHUNK)
                if cut is None:
                    continue
                chunk = audio[self._cut:cut]
                text = self._transcribe(chunk)
                if self._stop.is_set() and not text:
                    break
                if text:
                    self._parts.append(text)
                self._cut = cut
                log.info("streamed %.1fs chunk (%d chars)", chunk.size / SR, len(text))
            except Exception:
                log.exception("streaming chunk failed; the rest will be transcribed at the end")
                break

    def _transcribe(self, chunk):
        if chunk.size < SR * 0.3 or audio_mod.rms(chunk) < audio_mod.SILENCE_RMS:
            return ""
        text = self._stt.transcribe(audio_mod.normalize(chunk), prompt=self._prompt_for_next())
        return "" if looks_like_hallucination(text, chunk.size / SR) else text.strip()

    def finish(self, full_audio: np.ndarray) -> str:
        """Called after recording stops with the complete 16 kHz audio: transcribe what's left, return all of it."""
        self._stop.set()
        self._thread.join()  # waits for a chunk already in flight
        tail = full_audio[self._cut:]
        text = self._transcribe(tail)
        if text:
            self._parts.append(text)
        if self._cut:
            log.info("streaming: %d chunk(s) done while talking, %.1fs left at release", len(self._parts) - bool(text),
                     tail.size / SR)
        return " ".join(self._parts).strip()

    def cancel(self):
        self._stop.set()

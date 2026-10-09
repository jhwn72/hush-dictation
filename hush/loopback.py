"""Records what the computer is playing (the other side of a Zoom/Meet/Teams call) via WASAPI loopback.
Windows only; the loopback stream delivers silence while nothing plays, so the timeline stays continuous."""
import logging
import threading
import warnings

import numpy as np

from .audio import resample

log = logging.getLogger("flow.loopback")
SR = 48000


class SystemAudioRecorder:
    def __init__(self):
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.level = 0.0
        self.error = ""

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        import soundcard as sc

        warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)  # "data discontinuity" chatter
        spk = sc.default_speaker()
        self._device = sc.get_microphone(id=str(spk.name), include_loopback=True)
        self._chunks, self._stop, self.error = [], threading.Event(), ""
        self._thread = threading.Thread(target=self._run, name="loopback", daemon=True)
        self._thread.start()

    def _run(self):
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COM must be set up in every thread that touches WASAPI
        try:
            with self._device.recorder(samplerate=SR, channels=1) as rec:
                while not self._stop.is_set():
                    block = rec.record(numframes=SR // 10)[:, 0].astype(np.float32)
                    with self._lock:
                        self._chunks.append(block)
                    rms = float(np.sqrt(np.mean(block ** 2))) if block.size else 0.0
                    self.level = self.level * 0.6 + min(1.0, rms * 10) * 0.4
        except Exception as e:  # device unplugged, exclusive mode, etc.: the meeting continues mic-only
            self.error = str(e)
            log.exception("system audio capture stopped")

    def stop(self) -> np.ndarray:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self.level = 0.0
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            self._chunks = []
        return resample(audio, SR)

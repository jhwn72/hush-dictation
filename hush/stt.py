"""Speech-to-text. faster-whisper on CUDA (falls back to CPU) everywhere except Apple Silicon, which uses MLX."""
import os
import threading
from pathlib import Path

import numpy as np

from .config import IS_APPLE_SILICON, IS_WIN

MLX_REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "distil-large-v3": "mlx-community/distil-whisper-large-v3",
}


def _add_cuda_dlls():
    """pip's nvidia-cublas/cudnn wheels put DLLs in site-packages/nvidia/*/bin; Windows won't find them otherwise."""
    if not IS_WIN:
        return
    try:
        import nvidia  # namespace package from the nvidia-* wheels
    except ImportError:
        return
    for root in getattr(nvidia, "__path__", []):
        for sub in Path(root).iterdir():
            b = sub / "bin"
            if b.is_dir():
                os.add_dll_directory(str(b))
                os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


class Transcriber:
    def __init__(self, settings, on_status=None):
        self.s = settings
        self.on_status = on_status or (lambda st: None)
        self.state = "idle"  # idle | loading | ready | error
        self.backend = ""
        self.device = ""
        self.error = ""
        self._model = None
        self._lock = threading.Lock()

    def info(self) -> dict:
        return {"state": self.state, "backend": self.backend, "device": self.device,
                "model": self.s["whisper_model"], "error": self.error}

    def _set(self, state, error=""):
        self.state, self.error = state, error
        self.on_status(self.info())

    def load(self):
        with self._lock:
            self._model = None
            self._set("loading")
            try:
                if IS_APPLE_SILICON:
                    self._load_mlx()
                else:
                    self._load_ct2()
                self._set("ready")
            except Exception as e:  # surface any load failure in the UI instead of crashing
                self._set("error", f"{type(e).__name__}: {e}")

    def _load_mlx(self):
        import mlx_whisper

        repo = MLX_REPOS.get(self.s["whisper_model"], self.s["whisper_model"])
        self._model = ("mlx", repo)
        self.backend, self.device = "mlx-whisper", "Apple GPU"
        mlx_whisper.transcribe(np.zeros(16000, np.float32), path_or_hf_repo=repo, language="en")  # download + warm

    def _load_ct2(self):
        _add_cuda_dlls()
        from faster_whisper import WhisperModel

        name = self.s["whisper_model"]
        want = self.s["whisper_device"]
        attempts = [("cuda", "float16"), ("cpu", "int8")] if want in ("auto", "cuda") else [("cpu", "int8")]
        last = None
        for device, compute in attempts:
            try:
                model = WhisperModel(name, device=device, compute_type=compute,
                                     cpu_threads=max(4, (os.cpu_count() or 8) // 2))
                list(model.transcribe(np.zeros(16000, np.float32), language="en")[0])  # proves the kernels run
                self._model = ("ct2", model)
                self.backend, self.device = "faster-whisper", "CUDA" if device == "cuda" else "CPU"
                return
            except Exception as e:
                last = e
                if want == "cuda":
                    break
        raise last

    def transcribe(self, audio: np.ndarray, prompt: str = "") -> str:
        if self.state != "ready":
            raise RuntimeError("Speech model is still loading" if self.state == "loading" else f"Speech model unavailable: {self.error}")
        lang = self.s["language"] or None
        kind, model = self._model
        prompt = prompt[:800] or None
        if kind == "mlx":
            import mlx_whisper

            res = mlx_whisper.transcribe(audio, path_or_hf_repo=model, language=lang, initial_prompt=prompt,
                                         condition_on_previous_text=False)
            return res.get("text", "").strip()
        segments, _ = model.transcribe(
            audio, language=lang, initial_prompt=prompt, beam_size=5, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 700}, condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def transcribe_long(self, audio: np.ndarray, progress=None) -> str:
        """Meeting audio: same engine, keeps previous-text conditioning for coherence across segments."""
        if self.state != "ready":
            raise RuntimeError("Speech model is not ready")
        lang = self.s["language"] or None
        kind, model = self._model
        total = max(1.0, audio.size / 16000)
        if kind == "mlx":
            import mlx_whisper

            return mlx_whisper.transcribe(audio, path_or_hf_repo=model, language=lang).get("text", "").strip()
        segments, _ = model.transcribe(audio, language=lang, beam_size=5, vad_filter=True)
        parts = []
        for s in segments:
            parts.append(s.text.strip())
            if progress:
                progress(min(0.99, s.end / total))
        return "\n".join(_paragraphs(parts))

    def transcribe_segments(self, audio: np.ndarray, prompt: str = "", progress=None) -> list[tuple[float, float, str]]:
        """Meeting audio with timestamps: [(start_s, end_s, text)], used to interleave Me / Them."""
        if self.state != "ready":
            raise RuntimeError("Speech model is not ready")
        if audio.size < 16000 * 0.5:
            return []
        lang = self.s["language"] or None
        kind, model = self._model
        total = max(1.0, audio.size / 16000)
        if kind == "mlx":
            import mlx_whisper

            res = mlx_whisper.transcribe(audio, path_or_hf_repo=model, language=lang, initial_prompt=prompt or None)
            return [(s["start"], s["end"], s["text"].strip()) for s in res.get("segments", []) if s["text"].strip()]
        segments, _ = model.transcribe(audio, language=lang, beam_size=5, vad_filter=True,
                                       initial_prompt=prompt[:800] or None)
        out = []
        for s in segments:
            if s.text.strip():
                out.append((s.start, s.end, s.text.strip()))
            if progress:
                progress(min(0.99, s.end / total))
        return out


def _paragraphs(parts, size=6):
    for i in range(0, len(parts), size):
        yield " ".join(parts[i:i + size])

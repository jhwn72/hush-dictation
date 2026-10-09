"""Paths and user settings. Settings live in a JSON file next to the database."""
import copy
import json
import os
import platform
import sys
import threading
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_APPLE_SILICON = IS_MAC and platform.machine() == "arm64"


def data_dir() -> Path:
    if IS_WIN:
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "Hush"
    (d / "audio").mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS = {
    "user_name": "",
    "welcomed": False,  # the first-run welcome has been shown
    # Key names: ctrl, alt, shift, cmd (Win key on Windows), plus _l/_r variants, space, a-z, 0-9.
    "dictate_hotkey": ["ctrl", "cmd"] if not IS_MAC else ["fn"],  # macOS: the Fn / 🌐 key
    "hotkey_mode": "hold",  # hold = push-to-talk, toggle = press once to start, again to stop
    # hold this to speak an instruction instead of dictating. It contains the dictate keys, so you can also
    # start dictating and add the extra key.
    "command_hotkey": ["ctrl", "cmd", "alt"] if not IS_MAC else ["fn", "ctrl"],
    "transform_modifiers": ["cmd", "alt"],  # + 1..9 runs a transform on the selected text
    "mic_device": None,
    "whisper_model": "large-v3-turbo",
    "whisper_device": "auto",  # auto | cuda | cpu (ignored on Apple Silicon, which uses MLX)
    "language": "en",  # "" = auto-detect
    "ollama_url": "http://127.0.0.1:11434",
    "llm_model": "qwen2.5:7b-instruct",
    "llm_provider": "local",  # local = Ollama; cloud = Anthropic (falls back to Ollama when offline)
    "cloud_model": "claude-haiku-4-5",
    "auto_cleanup": True,
    "screen_context": True,  # read text around the cursor / on screen to guide cleanup (stays on this machine)
    "learn_corrections": True,  # add words you correct after dictating to the dictionary
    "note_system_audio": True,  # notetaker also records the computer's sound (the other side of a call), Windows
    "styles": {"personal": "formal", "work": "formal", "email": "formal", "other": "formal"},
    "auto_transform": {"enabled": False, "transform_id": "polish"},
    "restore_clipboard": True,
    "keep_audio": True,
    "show_overlay": True,
}


class Settings:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._data = copy.deepcopy(DEFAULTS)
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                for k, v in saved.items():
                    if isinstance(v, dict) and isinstance(self._data.get(k), dict):
                        self._data[k].update(v)
                    else:
                        self._data[k] = v
            except (OSError, ValueError):
                pass

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def all(self) -> dict:
        return copy.deepcopy(self._data)

    def update(self, partial: dict):
        with self._lock:
            for k, v in partial.items():
                if k not in DEFAULTS:
                    continue
                if isinstance(v, dict) and isinstance(self._data.get(k), dict):
                    self._data[k].update(v)
                else:
                    self._data[k] = v
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self._path)

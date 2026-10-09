"""Wires everything together: windows, the JS bridge, and the dictation / transform / notetaker pipelines."""
import base64
import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import requests
import webview

from . import audio as audio_mod
from . import textproc
from .config import IS_MAC, IS_WIN, Settings, data_dir
from .db import DB
from .hotkeys import Hotkeys
from .llm import LLM, format_context

from . import autostart
from .learn import CorrectionWatcher
from .streaming import StreamingTranscriber
from .meeting import format_transcript, merge_speakers

if IS_WIN:
    from .context_win import ContextGrab, read_field
    from .loopback import SystemAudioRecorder
elif IS_MAC:  # Accessibility API for context/learning; system audio needs a virtual driver, so mic only
    from .context_mac import ContextGrab, read_field
    SystemAudioRecorder = None
else:
    ContextGrab = read_field = SystemAudioRecorder = None
from .platform_utils import (active_app, categorize, copy_selection, erase_typed, foreground_window, matching_rules,
                             paste_text, press_enter, restore_clipboard)
from .stt import Transcriber

log = logging.getLogger("flow")

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

UI = Path(__file__).resolve().parent.parent / "ui"
MAIN_TITLE = "Hush"
OVERLAY_TITLE = "Hush overlay"
OVERLAY_W, OVERLAY_H = 236, 50


class Engine:
    def __init__(self):
        self.dir = data_dir()
        self.settings = Settings(self.dir / "settings.json")  # name starts blank; the first-run welcome asks for it
        self.db = DB(self.dir / "flow.db")
        self.llm = LLM(self.settings)
        self.stt = Transcriber(self.settings, on_status=lambda st: self.emit("stt", st))
        self.recorder = audio_mod.Recorder(self.settings["mic_device"])
        self.note_recorder = audio_mod.Recorder(self.settings["mic_device"])
        self.sys_recorder = SystemAudioRecorder() if SystemAudioRecorder else None  # the other side of a call
        self.note_started = 0.0
        self.hotkeys = Hotkeys(self.settings, self.start_dictation, self.stop_dictation, self.cancel_dictation,
                               self.lock_dictation, self.run_transform_hotkey)
        self.hotkeys.on_tap = self.tap_dictation
        self.hotkeys.on_command = self.command_dictation
        self.cmd_mode = False  # the recording in progress is a spoken instruction (command mode)
        self.target_app = {}
        self.target_hwnd = 0
        self.ctx = None  # ContextGrab for the dictation in progress
        self.last_paste = None  # what the last dictation typed, and where, for "scratch that"
        self.learner = CorrectionWatcher(
            read_field=read_field, foreground=foreground_window,
            known_words=lambda: {d["phrase"].lower() for d in self.db.list_dictionary()},
            on_learn=self._learned,
        ) if read_field else None
        self.dictating = False
        self.main = None
        self.overlay = None
        self.overlay_hwnd = 0
        self.pill = None  # native Windows pill (pill_win.Pill); macOS uses the webview overlay
        self.quitting = False
        self._overlay_hide_timer = None

    # ---------------- UI plumbing ----------------
    def emit(self, name, data=None):
        if not self.main:
            return
        try:
            self.main.evaluate_js(f"window.flowEvent && window.flowEvent({json.dumps(name)}, {json.dumps(data)})")
        except Exception:
            pass

    def overlay_state(self, state, text=""):
        if self.pill:
            if self._overlay_hide_timer:
                self._overlay_hide_timer.cancel()
                self._overlay_hide_timer = None
            if state == "hidden":
                self.pill.set_state("idle")
            elif state in ("recording", "locked", "processing", "hint", "command"):
                self.pill.set_state(state)
            else:
                self.pill.set_state("msg", text, {"error": "bad", "done": "good"}.get(state, ""))
            return
        if not self.overlay or not self.settings["show_overlay"]:
            return
        if self._overlay_hide_timer:
            self._overlay_hide_timer.cancel()
            self._overlay_hide_timer = None
        try:
            self.overlay.evaluate_js(f"window.setState && setState({json.dumps(state)}, {json.dumps(text)})")
            if self.overlay_hwnd:
                # Win32 directly: pywebview's show() activates the window, which steals focus from the
                # app being dictated into and sends our Ctrl+V to the pill instead
                user32 = ctypes.windll.user32
                if state == "hidden":
                    user32.ShowWindow(self.overlay_hwnd, 0)  # SW_HIDE
                else:
                    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
                    user32.SetWindowPos(self.overlay_hwnd, -1, 0, 0, 0, 0,  # HWND_TOPMOST
                                        SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            elif state == "hidden":
                self.overlay.hide()
            else:
                self.overlay.show()
        except Exception:
            log.exception("overlay")

    def overlay_flash(self, state, text, seconds=1.6):
        self.overlay_state(state, text)
        self._overlay_hide_timer = threading.Timer(seconds, lambda: self.overlay_state("hidden"))
        self._overlay_hide_timer.start()

    def _level_loop(self, rec, event):
        while rec.active:
            lvl = round(rec.level, 3)
            if rec is self.note_recorder and self.sys_recorder and self.sys_recorder.active:
                lvl = max(lvl, round(self.sys_recorder.level, 3))  # meter moves for both sides of the call
            if self.pill and rec is self.recorder:
                self.pill.set_level(lvl)
            elif self.overlay and rec is self.recorder:
                try:
                    self.overlay.evaluate_js(f"window.setLevel && setLevel({lvl})")
                except Exception:
                    pass
            self.emit(event, lvl)
            time.sleep(0.06)

    # ---------------- boot ----------------
    def boot(self):
        if IS_WIN:
            self._style_windows()
            threading.Thread(target=autostart.ensure_app_shortcut, daemon=True).start()
        if IS_MAC:
            try:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(autostart.mac_set_dock_icon)
            except Exception:
                log.debug("dock icon", exc_info=True)
            self.mac_permissions(prompt=True)
        if IS_WIN or IS_MAC:
            try:
                if IS_WIN:
                    from .pill_win import Pill
                else:
                    from .pill_mac import Pill
                self.pill = Pill(self.pill_mic, self.pill_cancel, self.pill_confirm, self.pill_note)
                self.pill.set_hotkey(self._hotkey_labels())
                self.pill.set_enabled(self.settings["show_overlay"])
            except Exception:
                # macOS falls back to the simpler webview overlay; Windows just runs without a pill
                self.pill = None
                log.exception("native pill failed to start")
        threading.Thread(target=self.stt.load, daemon=True).start()
        threading.Thread(target=self._boot_llm, daemon=True).start()
        self.hotkeys.start()

    def _boot_llm(self):
        st = self.llm.status()
        if not st["running"]:
            exe = shutil.which("ollama") or (
                str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe") if IS_WIN else None)
            if exe and Path(exe).exists():
                flags = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW
                subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                for _ in range(40):
                    time.sleep(0.25)
                    st = self.llm.status()
                    if st["running"]:
                        break
        self.emit("llm", st)
        if st["installed"]:
            self.llm.warm()
            self.emit("llm", self.llm.status())

    def _style_windows(self):
        user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
        for _ in range(40):
            hwnd = user32.FindWindowW(None, MAIN_TITLE)
            if hwnd:
                break
            time.sleep(0.1)
        if hwnd:
            on = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))  # dark title bar
            color = ctypes.c_int(0x00070505)  # caption color #050507 (BGR)
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))
            # the Hush icon in the title bar / taskbar instead of Python's
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
            for size, which in ((256, 1), (32, 0)):  # ICON_BIG, ICON_SMALL
                hicon = user32.LoadImageW(None, str(UI / "icon.ico"), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
                if hicon:
                    user32.SendMessageW(hwnd, 0x80, which, hicon)  # WM_SETICON
        ohwnd = user32.FindWindowW(None, OVERLAY_TITLE)
        if ohwnd:
            class RECT(ctypes.Structure):
                _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
            rc = RECT()
            user32.GetWindowRect(ohwnd, ctypes.byref(rc))
            w, h = rc.r - rc.l, rc.b - rc.t
            rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, h, h)
            user32.SetWindowRgn(ohwnd, rgn, True)
            # Never take focus away from the app being dictated into
            GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
            ex = user32.GetWindowLongW(ohwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(ohwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            self.overlay_hwnd = ohwnd
        log.info("windows styled: main=%s overlay=%s", hwnd, ohwnd)

    def _learned(self, heard, fixed):
        """A correction the user made after dictating: remember the spelling."""
        if fixed.lower() in {d["phrase"].lower() for d in self.db.list_dictionary()}:
            return
        did = self.db.add_dictionary(fixed, None, auto=True)
        self.emit("learned", {"id": did, "heard": heard, "phrase": fixed})
        if self.pill and not self.dictating:  # don't cover the waveform if the next dictation already started
            self.pill.set_state("msg", f"Learned “{fixed}”", "good")
            self._overlay_hide_timer = threading.Timer(1.8, lambda: self.overlay_state("hidden"))
            self._overlay_hide_timer.start()

    def mac_permissions(self, prompt=False) -> dict:
        """macOS privacy permissions for the process that's actually running. With prompt=True, macOS adds
        this exact program to the Accessibility / Input Monitoring lists and shows its prompts, so the
        switch the user turns on is the one that counts (the app runs Python inside Hush.app)."""
        if not IS_MAC:
            return {}
        out = {"accessibility": None, "input_monitoring": None, "post_events": None}
        try:
            import ApplicationServices as AS
            opts = {AS.kAXTrustedCheckOptionPrompt: True} if prompt else {}
            out["accessibility"] = bool(AS.AXIsProcessTrustedWithOptions(opts))
        except Exception:
            log.debug("accessibility check", exc_info=True)
        try:
            import Quartz
            listen = bool(Quartz.CGPreflightListenEventAccess())
            if not listen and prompt:
                listen = bool(Quartz.CGRequestListenEventAccess())
            post = bool(Quartz.CGPreflightPostEventAccess())
            if not post and prompt:
                post = bool(Quartz.CGRequestPostEventAccess())
            out["input_monitoring"], out["post_events"] = listen, post
        except Exception:
            log.debug("input monitoring check", exc_info=True)
        out["program"] = os.path.realpath(sys.executable)  # what to look for in the lists if "Hush" isn't there
        log.info("macOS permissions: %s", out)
        self.permissions = out
        self.emit("permissions", out)
        return out

    def _hotkey_labels(self):
        names = ({"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "cmd": "⌘", "space": "Space", "fn": "fn"} if IS_MAC else
                 {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win", "space": "Space"})
        out = []
        for k in self.settings["dictate_hotkey"]:
            base, _, side = k.partition("_")
            label = names.get(base, k.upper())
            out.append(("Right " if side == "r" else "Left " if side == "l" else "") + label)
        return out

    # ---- pill buttons ----
    def pill_mic(self):
        if self.dictating:
            return
        self.hotkeys.recording = self.hotkeys.locked = True
        self.hotkeys._locked_at = time.time()
        self.start_dictation()
        if self.dictating:
            self.lock_dictation()
        else:
            self.hotkeys.recording = self.hotkeys.locked = False

    def pill_confirm(self):
        self.hotkeys.recording = self.hotkeys.locked = False
        self.stop_dictation()

    def pill_cancel(self):
        self.hotkeys.recording = self.hotkeys.locked = False
        self.cancel_dictation()

    def pill_note(self):
        if self.note_recorder.active:
            self.stop_note()
        else:
            self.start_note()
        self.emit("note_state", {"on": self.note_recorder.active, "started": self.note_started})

    # ---------------- dictation ----------------
    def start_dictation(self):
        if self.dictating:
            return
        self.target_app = active_app()
        self.target_hwnd = foreground_window()
        self.cmd_mode = False
        try:
            self.recorder.device = self.settings["mic_device"]
            self.recorder.start()
        except Exception as e:
            self.hotkeys.recording = False
            self.overlay_flash("error", "Mic unavailable")
            self.emit("toast", {"kind": "bad", "text": f"Microphone error: {e}"})
            return
        self.dictating = True
        # read the room while the user is still talking (text around the cursor, the page/chat on screen)
        if self.learner:
            self.learner.finish_now()  # judge the previous dictation before this one changes the box
        self.ctx = ContextGrab() if (ContextGrab and self.settings["screen_context"]) else None
        # long dictations: transcribe finished stretches while the user is still talking
        self.streamer = None
        if self.stt.state == "ready" and not os.environ.get("HUSH_TEST_WAV"):
            vocab = ", ".join(e["phrase"] for e in self.db.list_dictionary() if not e["replacement"])
            self.streamer = StreamingTranscriber(self.stt, self.recorder.peek, prompt=vocab)
        self.overlay_state("recording")
        self.emit("recording", {"on": True, "app": self.target_app.get("name", "")})
        threading.Thread(target=self._level_loop, args=(self.recorder, "level"), daemon=True).start()

    def command_dictation(self):
        """The shortcut became the command shortcut: what's being said is an instruction, not text to type."""
        if not self.dictating:
            return
        self.cmd_mode = True
        self.overlay_state("command")
        self.emit("recording", {"on": True, "command": True, "app": self.target_app.get("name", "")})

    def lock_dictation(self):
        self.overlay_state("locked")
        self.emit("recording", {"on": True, "locked": True, "app": self.target_app.get("name", "")})

    def tap_dictation(self):
        """A single quick tap of the shortcut: not push-to-talk, not a double-tap. Discard and explain."""
        self._drop_streamer()
        self.recorder.stop()
        self.dictating = False
        self.emit("recording", {"on": False})
        self.overlay_state("hint")

    def _drop_streamer(self):
        if getattr(self, "streamer", None):
            self.streamer.cancel()
            self.streamer = None

    def cancel_dictation(self):
        self._drop_streamer()
        self.recorder.stop()
        self.dictating = False
        self.overlay_flash("cancelled", "Cancelled", 0.9)
        self.emit("recording", {"on": False})

    def stop_dictation(self):
        if not self.dictating:
            return
        pcm = self.recorder.stop()
        self.dictating = False
        self.emit("recording", {"on": False})
        test_wav = os.environ.get("HUSH_TEST_WAV")  # dev hook: dictate a wav file instead of the mic
        if test_wav:
            pcm = audio_mod.load_wav(Path(test_wav))
        dur = pcm.size / audio_mod.TARGET_SR
        if dur < 0.4 or audio_mod.rms(pcm) < audio_mod.SILENCE_RMS:
            self._drop_streamer()
            self.overlay_state("hidden")
            return
        pcm = audio_mod.normalize(pcm)  # whisper mode: quiet speech is boosted, normal speech untouched
        if self.stt.state != "ready":
            self._drop_streamer()
            self.overlay_flash("empty", "Still starting up…" if self.stt.state == "loading" else "Speech model unavailable", 1.8)
            return
        self.overlay_state("processing")
        try:
            self._process(pcm, dur, dict(self.target_app))
        except Exception as e:
            log.exception("dictation failed")
            self.overlay_flash("error", "Something went wrong")
            self.emit("toast", {"kind": "bad", "text": str(e)})

    def _process(self, pcm, dur, app):
        t0 = time.time()
        entries = self.db.list_dictionary()
        vocab = [e["phrase"] for e in entries if not e["replacement"]]
        prompt = ", ".join(vocab)
        streamer, self.streamer = getattr(self, "streamer", None), None
        raw = streamer.finish(pcm) if streamer else self.stt.transcribe(pcm, prompt=prompt)
        if textproc.looks_like_hallucination(raw, dur):
            self.overlay_flash("empty", "Didn't catch that", 1.2)
            return
        t_stt = time.time() - t0

        category = categorize(app)
        style_key = category if category in ("personal", "work", "email") else "other"
        style = self.settings["styles"].get(style_key, "formal")

        ctx = self.ctx.result(wait=0.25) if getattr(self, "ctx", None) else {}
        recent = [r["text"] for r in self.db.recent_in_app(app.get("name", ""), within=600, limit=2)]
        context_block = format_context(ctx, app, recent[::-1])
        continuing = textproc.continues_sentence(ctx.get("before", ""))
        log.info("context: before=%d after=%d nearby=%d recent=%d", len(ctx.get("before", "")),
                 len(ctx.get("after", "")), len(ctx.get("nearby", "")), len(recent))
        if self.cmd_mode:
            return self._process_command(raw, ctx, app, entries, category, dur, pcm, t0)

        text, dict_fixes = textproc.apply_dictionary(raw, entries)
        text = textproc.remove_fillers(text)  # guaranteed baseline, even if the model is down or skipped
        # spoken commands ("scratch that", "press enter", "new line") are handled here, never sent to the model
        text, voice_actions = textproc.voice_commands(text)
        if "undo" in voice_actions:
            return self._scratch_last(ctx)
        if not text.strip():
            self.overlay_flash("empty", "Scratched", 1.0)
            return
        used_llm = False
        rules = matching_rules(app, self.db.list_rules())
        if self.settings["auto_cleanup"]:
            if textproc.count_words(text) >= 3 or context_block or rules:
                try:
                    text = self.llm.cleanup(text, style, category, app.get("name", ""), vocab, context=context_block,
                                            rules=rules)
                    text, more = textproc.apply_dictionary(text, entries)
                    dict_fixes += more
                    used_llm = True
                except requests.RequestException:
                    log.warning("cleanup model unreachable; using filler-stripped transcript")
        if not text.strip():
            self.overlay_flash("empty", "Only filler words", 1.2)
            return
        text, _ = textproc.apply_snippets(text, self.db.list_snippets())

        transform_id = None
        at = self.settings["auto_transform"]
        if at.get("enabled"):
            t = self.db.get_transform(at.get("transform_id"))
            if t:
                try:
                    text = self.llm.transform(text, t["prompt"])
                    transform_id = t["id"]
                except requests.RequestException:
                    pass
        if not (rules and used_llm):  # the user's own app rules decide punctuation/casing when they exist
            text = textproc.apply_style(text, style, continuing=continuing)
        text = textproc.fit_to_cursor(text, ctx.get("before", ""), ctx.get("after", ""), vocab)
        if not text.strip():
            self.overlay_state("hidden")
            return

        self.overlay_state("hidden")
        log.info("paste into %s (target hwnd %s, foreground %s): %d chars, cleanup=%s, %.2fs total", app.get("name"),
                 self.target_hwnd, foreground_window(), len(text), self.llm.last_backend if used_llm else "none",
                 time.time() - t0)
        paste_text(text, restore=self.settings["restore_clipboard"], target_hwnd=self.target_hwnd)
        if "send" in voice_actions:
            press_enter()
        elif self.learner and self.settings["learn_corrections"] and ctx.get("editable"):
            self.learner.watch(self.target_hwnd, ctx.get("before", ""), text, ctx.get("after", ""))

        audio_file = None
        if self.settings["keep_audio"]:
            audio_file = f"{uuid.uuid4().hex}.wav"
            audio_mod.save_wav(self.dir / "audio" / audio_file, pcm)
        did = self.db.add_dictation(
            ts=time.time(), raw=raw, text=text, app=app.get("name", ""), category=category, duration=dur,
            words=textproc.count_words(text), fixes=textproc.diff_fixes(raw, text) if self.settings["auto_cleanup"] else 0,
            dict_fixes=dict_fixes, audio=audio_file, transform=transform_id,
        )
        row = self.db.get_dictation(did)
        row["latency"] = {"stt": round(t_stt, 2), "total": round(time.time() - t0, 2)}
        self.emit("dictation", {"row": row, "stats": self.db.stats()})
        # remembered for "scratch that"; a sent message can't be taken back from here
        self.last_paste = None if "send" in voice_actions else {
            "hwnd": self.target_hwnd, "text": text, "t": time.time(), "id": did, "kind": "dictation"}

    def _scratch_last(self, ctx):
        """'Scratch that' on its own: remove the previous dictation, but only from the box it went into."""
        lp = self.last_paste
        if not lp or time.time() - lp["t"] > 300 or foreground_window() != lp["hwnd"]:
            self.overlay_flash("empty", "Nothing to scratch here", 1.4)
            return
        typed, before = lp["text"], ctx.get("before", "")
        if ctx.get("editable"):
            trailing = len(before) - len(before.rstrip(" "))
            if not before.rstrip(" ").endswith(typed.rstrip(" ")):
                # the user moved the cursor or edited since; don't delete the wrong thing
                self.overlay_flash("empty", "Last dictation isn't at the cursor", 1.6)
                return
            n = 0 if ("\n" in typed or lp["kind"] != "dictation") else len(typed.rstrip(" ")) + trailing
            erase_typed(n)
        elif time.time() - lp["t"] <= 60:
            erase_typed(0)  # app hides its text from us: fall back to the app's own undo, only right after
        else:
            self.overlay_flash("empty", "Too late to scratch that", 1.4)
            return
        self.last_paste = None
        if lp.get("id"):
            self.db.delete_dictation(lp["id"])
            self.emit("deleted", {"id": lp["id"], "stats": self.db.stats()})
        self.overlay_flash("done", "Scratched", 1.0)

    def _process_command(self, raw, ctx, app, entries, category, dur, pcm, t0):
        """Command mode: apply the spoken instruction to the selection, or write what was asked at the cursor."""
        instruction = textproc.remove_fillers(textproc.apply_dictionary(raw, entries)[0])
        selected = ctx.get("selected", "") if ctx.get("editable") else ""
        old_clip = None
        if not selected.strip():  # app didn't report a selection: ask it via the clipboard (Ctrl+C) to be sure
            selected, old_clip = copy_selection()
        # screen context helps with tone/facts; the selection itself is sent separately
        context_block = format_context({k: v for k, v in ctx.items() if k != "selected"}, app, [])
        self.overlay_state("processing", "Rewriting" if selected.strip() else "Writing")
        try:
            out = self.llm.command(instruction, selected, context_block, rules=matching_rules(app, self.db.list_rules()))
        except requests.RequestException as e:
            if old_clip is not None:
                restore_clipboard(old_clip)
            self.overlay_flash("error", "Model unreachable")
            self.emit("toast", {"kind": "bad", "text": f"Command failed: {e}"})
            return
        if not out:
            self.overlay_flash("empty", "Nothing to write", 1.2)
            return
        self.overlay_state("hidden")
        paste_text(out, restore=self.settings["restore_clipboard"] and old_clip is None, target_hwnd=self.target_hwnd)
        if old_clip is not None:
            threading.Timer(0.8, lambda: restore_clipboard(old_clip)).start()
        log.info("command in %s: %d chars selected -> %d chars, %s, %.2fs", app.get("name"), len(selected), len(out),
                 self.llm.last_backend, time.time() - t0)
        did = self.db.add_dictation(
            ts=time.time(), raw=instruction, text=out, app=app.get("name", ""), category=category, duration=dur,
            words=textproc.count_words(out), fixes=0, dict_fixes=0, audio=None, transform="command",
        )
        self.emit("dictation", {"row": self.db.get_dictation(did), "stats": self.db.stats()})
        # "scratch that" after a rewrite uses the app's undo, which brings the original selection back
        self.last_paste = {"hwnd": self.target_hwnd, "text": out, "t": time.time(), "id": did, "kind": "command"}

    # ---------------- transforms ----------------
    def run_transform_hotkey(self, slot):
        t = self.db.transform_for_slot(slot)
        if not t:
            return
        target = foreground_window()
        self.hotkeys.wait_modifiers_released()
        time.sleep(0.05)
        sel, old = copy_selection()
        if not sel.strip():
            self.overlay_flash("empty", "Select some text first")
            return
        self.overlay_state("processing", t["name"])
        try:
            out = self.llm.transform(sel, t["prompt"])
        except requests.RequestException as e:
            restore_clipboard(old)
            self.overlay_flash("error", "Ollama isn't reachable")
            self.emit("toast", {"kind": "bad", "text": f"Transform failed: {e}"})
            return
        self.overlay_state("hidden")
        paste_text(out, restore=False, target_hwnd=target)
        threading.Timer(0.8, lambda: restore_clipboard(old)).start()
        self.overlay_flash("done", t["name"], 1.0)
        self.emit("toast", {"kind": "good", "text": f"{t['name']} applied"})

    # ---------------- notetaker ----------------
    def start_note(self):
        if self.note_recorder.active:
            return {"ok": True}
        self.note_recorder.device = self.settings["mic_device"]
        self.note_recorder.start()
        sys_on = bool(self.sys_recorder and self.settings["note_system_audio"])
        if sys_on:
            try:
                self.sys_recorder.start()
            except Exception:
                log.exception("system audio unavailable; recording the mic only")
                sys_on = False
        self.note_started = time.time()
        if self.pill:
            self.pill.set_note(True)
        threading.Thread(target=self._level_loop, args=(self.note_recorder, "note_level"), daemon=True).start()
        return {"ok": True, "started": self.note_started, "system_audio": sys_on}

    def stop_note(self):
        if not self.note_recorder.active:
            return None
        pcm = self.note_recorder.stop()
        sys_pcm = self.sys_recorder.stop() if (self.sys_recorder and self.sys_recorder.active) else None
        if self.pill:
            self.pill.set_note(False)
        dur = pcm.size / audio_mod.TARGET_SR
        fname = f"note-{uuid.uuid4().hex}.wav"
        audio_mod.save_wav(self.dir / "audio" / fname, self._mix(pcm, sys_pcm))
        nid = self.db.add_note(ts=self.note_started, title="Processing…", duration=dur, transcript="",
                               summary="", status="processing", audio=fname)
        threading.Thread(target=self._process_note, args=(nid, pcm, sys_pcm), daemon=True).start()
        return nid

    @staticmethod
    def _mix(mic, sys_pcm):
        """One playback file with both sides of the call."""
        if sys_pcm is None or not sys_pcm.size:
            return mic
        n = max(mic.size, sys_pcm.size)
        out = np.zeros(n, np.float32)
        out[: mic.size] += audio_mod.normalize(mic) * 0.7
        out[: sys_pcm.size] += audio_mod.normalize(sys_pcm) * 0.7
        return np.clip(out, -1, 1)

    def _process_note(self, nid, pcm, sys_pcm=None):
        try:
            has_them = sys_pcm is not None and sys_pcm.size > 16000 * 2 and audio_mod.rms(sys_pcm) > 0.002
            has_me = pcm.size > 16000 * 2 and audio_mod.rms(pcm) > 0.002
            vocab = ", ".join(e["phrase"] for e in self.db.list_dictionary() if not e["replacement"])
            prog = lambda base, span: (lambda p: self.emit("note_progress", {"id": nid, "p": round(base + p * span, 2)}))
            if has_them:
                # both sides of a call: transcribe each with timestamps, then interleave as Me / Them
                me = self.stt.transcribe_segments(audio_mod.normalize(pcm), vocab, prog(0, 0.5)) if has_me else []
                them = self.stt.transcribe_segments(audio_mod.normalize(sys_pcm), vocab, prog(0.5, 0.5))
                transcript = format_transcript(merge_speakers(me, them))
            elif has_me:
                transcript = self.stt.transcribe_long(audio_mod.normalize(pcm), progress=prog(0, 1))
            else:
                transcript = ""
            if textproc.count_words(transcript) < 8:
                data = {"title": "Untitled", "overview": "No meaningful audio recorded", "key_points": [], "action_items": []}
            else:
                self.emit("note_progress", {"id": nid, "p": 1, "stage": "summary"})
                data = self.llm.summarize_meeting(transcript, me_name=self.settings["user_name"] or "the user",
                                                  labeled=has_them)
            self.db.update_note(nid, title=data.get("title") or "Untitled", transcript=transcript,
                                summary=json.dumps(data), status="done")
        except Exception as e:
            log.exception("note processing failed")
            self.db.update_note(nid, title="Couldn't process note", status="error", summary=json.dumps({"overview": str(e)}))
        self.emit("note", self.db.get_note(nid))

    def quit(self):
        self.quitting = True
        self.hotkeys.stop()
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass


class Api:
    """Everything here is callable from JS as window.pywebview.api.<name>(...)."""

    def __init__(self, engine: Engine):
        self._e = engine

    # ---- state ----
    def get_state(self):
        e = self._e
        return {
            "settings": e.settings.all(),
            "stats": e.db.stats(),
            "stt": e.stt.info(),
            "llm": e.llm.status(),
            "platform": "mac" if IS_MAC else "win" if IS_WIN else "linux",
            "data_dir": str(e.dir),
            "note_recording": e.note_recorder.active,
            "note_started": e.note_started,
            "launch_at_login": autostart.is_enabled(),
            "permissions": getattr(e, "permissions", {}),
        }

    def get_stats(self):
        return self._e.db.stats()

    def get_recap(self, weeks_ago=0):
        return self._e.db.weekly_recap(int(weeks_ago))

    def set_settings(self, partial):
        e = self._e
        before = e.settings.all()
        e.settings.update(partial)
        if any(before[k] != e.settings[k] for k in ("whisper_model", "whisper_device") if k in partial):
            threading.Thread(target=e.stt.load, daemon=True).start()
        if "llm_model" in partial or "ollama_url" in partial:
            threading.Thread(target=e._boot_llm, daemon=True).start()
        if partial.get("show_overlay") is False:
            e.overlay_state("hidden")
        return e.settings.all()

    def save_api_key(self, key):
        from .llm import set_api_key
        set_api_key((key or "").strip())
        return self._e.llm.status()

    def test_cloud(self):
        return self._e.llm.test_cloud()

    def pause_hotkeys(self, paused):
        self._e.hotkeys.paused = bool(paused)
        self._e.hotkeys.reset()

    def check_permissions(self):
        return self._e.mac_permissions(prompt=False)

    def open_privacy(self, pane):
        """Open System Settings at Accessibility / Input Monitoring / Microphone (macOS)."""
        anchors = {"accessibility": "Privacy_Accessibility", "input_monitoring": "Privacy_ListenEvent",
                   "microphone": "Privacy_Microphone"}
        if IS_MAC and pane in anchors:
            subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{anchors[pane]}"])
        return True

    def restart(self):
        """Quit and reopen Hush (macOS needs a restart before newly granted permissions apply)."""
        if IS_MAC and autostart.MAC_APP.exists():
            subprocess.Popen(["/bin/sh", "-c", f"sleep 1.5; open '{autostart.MAC_APP}'"], start_new_session=True)
        elif IS_WIN:
            subprocess.Popen([str(Path(sys.executable).with_name("pythonw.exe")), "-m", "hush"],
                             cwd=str(Path(__file__).resolve().parent.parent), creationflags=0x00000008)  # DETACHED
        threading.Thread(target=self._e.quit, daemon=True).start()
        return True

    def set_launch_at_login(self, on):
        return autostart.set_enabled(bool(on))

    def capture_hotkey(self):
        """Record the next key combination the user presses (seen by the global listener, so Fn works on Mac)."""
        return self._e.hotkeys.capture(timeout=10.0)

    def set_hotkey(self, which, combo):
        """which: dictate | command | transform. Returns {"ok": True, "settings": ...} or {"ok": False, "error": ...}."""
        from .hotkeys import MODIFIERS, generic
        combo = [str(k) for k in (combo or [])]
        if not combo:
            return {"ok": False, "error": "No keys were pressed"}
        s = self._e.settings
        if which == "transform":
            if not generic(combo) <= MODIFIERS:
                return {"ok": False, "error": "Use modifier keys only (like Win + Alt); the slot number is added"}
            key = "transform_modifiers"
        elif which in ("dictate", "command"):
            other = s["command_hotkey"] if which == "dictate" else s["dictate_hotkey"]
            if generic(combo) == generic(other):
                return {"ok": False, "error": "That's already the " + ("command" if which == "dictate" else "dictate") + " shortcut"}
            if which == "command" and combo == ["esc"]:
                return {"ok": False, "error": "Esc is reserved for cancelling"}
            key = which + "_hotkey"
        else:
            return {"ok": False, "error": "Unknown shortcut"}
        s.update({key: combo})
        if self._e.pill:
            self._e.pill.set_hotkey(self._e._hotkey_labels())
        return {"ok": True, "settings": s.all()}

    def list_mics(self):
        return audio_mod.list_mics()

    def pull_model(self, name):
        e = self._e

        def run():
            try:
                with requests.post(f"{e.llm.url}/api/pull", json={"model": name, "stream": True}, stream=True, timeout=3600) as r:
                    for line in r.iter_lines():
                        if not line:
                            continue
                        msg = json.loads(line)
                        p = msg["completed"] / msg["total"] if msg.get("total") and msg.get("completed") else None
                        e.emit("pull", {"status": msg.get("status", ""), "p": p, "error": msg.get("error")})
                e.settings.update({"llm_model": name})
                e._boot_llm()
            except Exception as ex:
                e.emit("pull", {"status": "error", "error": str(ex)})

        threading.Thread(target=run, daemon=True).start()
        return True

    # ---- history ----
    def list_dictations(self, query="", offset=0, limit=60):
        return self._e.db.list_dictations(query or "", int(offset), int(limit))

    def delete_dictation(self, did):
        row = self._e.db.delete_dictation(did)
        if row and row.get("audio"):
            (self._e.dir / "audio" / row["audio"]).unlink(missing_ok=True)
        return self._e.db.stats()

    def flag_dictation(self, did):
        self._e.db.toggle_flag(did)
        return True

    def copy_text(self, text):
        import pyperclip
        pyperclip.copy(text)
        return True

    def get_audio(self, did):
        row = self._e.db.get_dictation(did)
        if not row or not row.get("audio"):
            return None
        p = self._e.dir / "audio" / row["audio"]
        if not p.exists():
            return None
        return "data:audio/wav;base64," + base64.b64encode(p.read_bytes()).decode()

    def retry_dictation(self, did):
        """Re-run cleanup on the saved raw transcript (e.g. after adding dictionary words)."""
        e = self._e
        row = e.db.get_dictation(did)
        if not row:
            return None
        entries = e.db.list_dictionary()
        text, _ = textproc.apply_dictionary(row["raw"], entries)
        style = e.settings["styles"].get(row["category"] if row["category"] in ("personal", "work", "email") else "other", "formal")
        text = e.llm.cleanup(text, style, row["category"], row["app"] or "", [x["phrase"] for x in entries if not x["replacement"]])
        text, _ = textproc.apply_snippets(textproc.apply_dictionary(text, entries)[0], e.db.list_snippets())
        return textproc.apply_style(text, style)

    # ---- dictionary ----
    def list_dictionary(self):
        return self._e.db.list_dictionary()

    def add_dictionary(self, phrase, replacement=None):
        self._e.db.add_dictionary(phrase.strip(), (replacement or "").strip() or None)
        return self._e.db.list_dictionary()

    def update_dictionary(self, did, phrase, replacement=None):
        self._e.db.update_dictionary(did, phrase.strip(), (replacement or "").strip() or None)
        return self._e.db.list_dictionary()

    def star_dictionary(self, did):
        self._e.db.star_dictionary(did)
        return self._e.db.list_dictionary()

    def delete_dictionary(self, did):
        self._e.db.delete_dictionary(did)
        return self._e.db.list_dictionary()

    # ---- snippets ----
    def list_snippets(self):
        return self._e.db.list_snippets()

    def add_snippet(self, trigger, expansion):
        self._e.db.add_snippet(trigger.strip(), expansion)
        return self._e.db.list_snippets()

    def update_snippet(self, sid, trigger, expansion):
        self._e.db.update_snippet(sid, trigger.strip(), expansion)
        return self._e.db.list_snippets()

    def delete_snippet(self, sid):
        self._e.db.delete_snippet(sid)
        return self._e.db.list_snippets()

    # ---- per-app rules ----
    def list_rules(self):
        return {"rules": self._e.db.list_rules(), "apps": self._e.db.recent_apps()}

    def add_rule(self, app, instruction):
        if app.strip() and instruction.strip():
            self._e.db.add_rule(app.strip(), instruction.strip())
        return self.list_rules()

    def update_rule(self, rid, app, instruction, enabled=True):
        self._e.db.update_rule(rid, app.strip(), instruction.strip(), enabled)
        return self.list_rules()

    def delete_rule(self, rid):
        self._e.db.delete_rule(rid)
        return self.list_rules()

    # ---- transforms ----
    def list_transforms(self):
        return self._e.db.list_transforms()

    def save_transform(self, tid, name, description, prompt):
        self._e.db.save_transform(tid or f"custom_{uuid.uuid4().hex[:8]}", name.strip(), description.strip(), prompt.strip())
        return self._e.db.list_transforms()

    def delete_transform(self, tid):
        self._e.db.delete_transform(tid)
        return self._e.db.list_transforms()

    def reset_transforms(self):
        self._e.db.reset_transforms()
        return self._e.db.list_transforms()

    def test_transform(self, tid, text):
        t = self._e.db.get_transform(tid)
        if not t:
            return {"error": "Transform not found"}
        try:
            return {"text": self._e.llm.transform(text, t["prompt"])}
        except requests.RequestException as ex:
            return {"error": f"Ollama isn't reachable: {ex}"}

    # ---- notes ----
    def start_note(self):
        try:
            return self._e.start_note()
        except Exception as ex:
            return {"ok": False, "error": str(ex)}

    def stop_note(self):
        return self._e.stop_note()

    def list_notes(self):
        return self._e.db.list_notes()

    def get_note(self, nid):
        return self._e.db.get_note(nid)

    def rename_note(self, nid, title):
        self._e.db.update_note(nid, title=title.strip() or "Untitled")
        return True

    def delete_note(self, nid):
        row = self._e.db.delete_note(nid)
        if row and row.get("audio"):
            (self._e.dir / "audio" / row["audio"]).unlink(missing_ok=True)
        return self._e.db.list_notes()

    # ---- app ----
    def open_data_dir(self):
        if IS_WIN:
            os.startfile(self._e.dir)
        elif IS_MAC:
            subprocess.Popen(["open", str(self._e.dir)])
        return True

    def quit(self):
        threading.Thread(target=self._e.quit, daemon=True).start()
        return True


def run():
    logging.basicConfig(
        filename=str(data_dir() / "flow.log"), level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if IS_WIN:  # group under "Hush" in the taskbar (with its own icon), not under Python
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Hush.App")
    autostart.mac_brand_process()  # macOS: "Hush" in the menu bar, not "python"
    engine = Engine()
    api = Api(engine)
    engine.main = webview.create_window(
        MAIN_TITLE, url=str(UI / "index.html"), js_api=api, width=1320, height=860,
        min_size=(940, 620), background_color="#050507", text_select=True,
    )

    def on_closing():
        if engine.quitting:
            return True
        engine.main.minimize()  # keep hotkeys alive; quit from the sidebar
        return False

    engine.main.events.closing += on_closing

    try:
        screen = webview.screens[0]
        sx, sy = (screen.width - OVERLAY_W) // 2, screen.height - OVERLAY_H - 96
    except Exception:
        sx, sy = None, None
    engine.overlay = webview.create_window(
        OVERLAY_TITLE, url=str(UI / "overlay.html"), width=OVERLAY_W, height=OVERLAY_H, x=sx, y=sy,
        frameless=True, on_top=True, resizable=False, focus=False, transparent=IS_MAC,
        background_color="#0B0B0F", easy_drag=False, hidden=False,
    )
    engine.overlay.events.loaded += lambda: threading.Timer(0.6, engine.overlay.hide).start()

    webview.start(engine.boot, private_mode=False, storage_path=str(engine.dir / "webview"))

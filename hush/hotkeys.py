"""Global hotkeys: push-to-talk dictation, hands-free lock, Esc cancel, command mode, modifier+digit transforms,
and capturing new shortcuts for Settings.

On macOS the Fn / 🌐 key is invisible to pynput, so a small Quartz event tap reports it as the key "fn".
"""
import logging
import threading
import time

from pynput import keyboard

from .config import IS_MAC
from .platform_utils import injecting

log = logging.getLogger("flow.hotkeys")

GENERIC = {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
           "shift_l": "shift", "shift_r": "shift", "cmd_l": "cmd", "cmd_r": "cmd"}
MODIFIERS = {"ctrl", "alt", "shift", "cmd", "fn"}
TAP_MAX = 0.3            # a press shorter than this counts as a tap, not push-to-talk
DOUBLE_TAP_WINDOW = 0.4  # time allowed between the two taps of a double-tap
# macOS virtual keycodes for the number row (chars change under Option, keycodes don't)
MAC_DIGITS = {29: "0", 18: "1", 19: "2", 20: "3", 21: "4", 23: "5", 22: "6", 26: "7", 28: "8", 25: "9"}
MAC_FN_KEYCODES = {63, 179}  # Fn, and the 🌐 Globe key on newer keyboards


def key_name(key) -> str | None:
    if isinstance(key, keyboard.Key):
        return key.name
    vk = getattr(key, "vk", None)
    if vk is not None:
        if IS_MAC:
            if vk in MAC_DIGITS:
                return MAC_DIGITS[vk]
            if vk in MAC_FN_KEYCODES:
                return None  # reported by the Quartz tap instead
        elif 48 <= vk <= 57 or 65 <= vk <= 90:
            return chr(vk).lower()
        elif 96 <= vk <= 105:  # numpad
            return str(vk - 96)
    ch = getattr(key, "char", None)
    return ch.lower() if ch and ch.isprintable() else None


def generic(names) -> set[str]:
    return {GENERIC.get(n, n) for n in names}


class Hotkeys:
    def __init__(self, settings, on_start, on_stop, on_cancel, on_lock, on_transform):
        self.s = settings
        self.on_start, self.on_stop, self.on_cancel = on_start, on_stop, on_cancel
        self.on_lock, self.on_transform = on_lock, on_transform
        self._down: set[str] = set()
        self.recording = False
        self.locked = False
        self.paused = False  # legacy: the Settings page used to capture shortcuts in the browser
        self._started_at = 0.0
        self._locked_at = 0.0
        self._tap_pending = False  # released quickly; waiting to see if a second tap follows
        self._tap_timer = None
        self._base = []  # the combo that started this recording; letting go of it ends push-to-talk
        self.on_tap = lambda: None  # single quick tap in hold mode (recording is discarded)
        self.on_command = lambda: None  # this recording is a command-mode instruction, not dictation
        self.command = False
        self._capture = None  # {"combo": [...], "done": Event} while Settings records a new shortcut
        self._listener = None

    def _held(self) -> set[str]:
        return self._down | generic(self._down)

    def _combo_down(self, combo) -> bool:
        return bool(combo) and set(combo) <= self._held()

    def _exactly(self, combo) -> bool:
        """combo is held and nothing else is (so Ctrl+Win doesn't fire while you're pressing Ctrl+Win+Shift+S)."""
        return self._combo_down(combo) and not (generic(self._down) - generic(combo))

    def start(self):
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self._listener.daemon = True
        self._listener.start()
        if IS_MAC:
            threading.Thread(target=self._mac_fn_tap, name="fn-key", daemon=True).start()

    def stop(self):
        if self._listener:
            self._listener.stop()

    def wait_modifiers_released(self, timeout=1.5):
        end = time.time() + timeout
        while time.time() < end and (self._held() & MODIFIERS):
            time.sleep(0.02)

    # ---------------- capturing a new shortcut (Settings) ----------------
    def capture(self, timeout=10.0):
        """Wait for the user to press and release a key combination; returns its key names (or [] on timeout).
        Normal shortcuts are suspended meanwhile, and Esc on its own cancels."""
        cap = {"combo": [], "held": set(), "done": threading.Event()}
        self._capture = cap
        self._down.clear()
        try:
            cap["done"].wait(timeout)
        finally:
            self._capture = None
            self._down.clear()
        combo = cap["combo"]
        return [] if combo == ["esc"] else combo

    def _capture_press(self, name):
        cap = self._capture
        cap["held"].add(name)
        if name not in cap["combo"]:
            cap["combo"].append(name)

    def _capture_release(self, name):
        cap = self._capture
        cap["held"].discard(name)
        if not cap["held"] and cap["combo"]:
            combo = cap["combo"]
            if len(combo) > 1:  # in a combination the side doesn't matter: Ctrl_L+Win == Ctrl+Win
                combo[:] = sorted(set(GENERIC.get(n, n) for n in combo), key=lambda n: (n not in MODIFIERS, n))
            cap["done"].set()

    # ---------------- key events ----------------
    def _press(self, key):
        if injecting.is_set():
            return
        name = key_name(key)
        if name:
            self._press_name(name)

    def _release(self, key):
        if injecting.is_set():
            return
        name = key_name(key)
        if name:
            self._release_name(name)

    def _press_name(self, name):
        if self._capture is not None:
            return self._capture_press(name)
        if self.paused or name in self._down:
            return
        self._down.add(name)
        combo = self.s["dictate_hotkey"]
        cmd_combo = self.s.get("command_hotkey") or []
        mode = self.s["hotkey_mode"]

        if self.recording:
            if name == "esc":
                self._cancel_tap()
                self.recording = self.locked = False
                threading.Thread(target=self.on_cancel, daemon=True).start()
            elif not self.command and not self.locked and not self._tap_pending and self._combo_down(cmd_combo):
                # e.g. holding Ctrl+Win and adding Alt: this recording becomes a command
                self.command = True
                threading.Thread(target=self.on_command, daemon=True).start()
            elif self._tap_pending and self._combo_down(self._base):
                # second tap of a double-tap: keep the recording going, hands-free
                self._cancel_tap()
                self.locked = True
                self._locked_at = time.time()
                threading.Thread(target=self.on_lock, daemon=True).start()
            elif self.locked and self._combo_down(self._base) and time.time() - self._locked_at > 0.3:
                self._finish()
            elif mode == "toggle" and self._combo_down(self._base) and time.time() - self._started_at > 0.3:
                self._finish()
            return

        if self._exactly(cmd_combo) and not generic(cmd_combo) <= generic(combo):
            self._begin(cmd_combo, command=True)  # command shortcut pressed directly
            return
        if self._exactly(combo):
            self._begin(combo, command=False)
            return

        mods = self.s["transform_modifiers"]
        if name.isdigit() and name != "0" and self._combo_down(mods):
            threading.Thread(target=self.on_transform, args=(int(name),), daemon=True).start()

    def _begin(self, base, command):
        self.recording, self.locked = True, False
        self.command = command
        self._base = list(base)
        self._started_at = time.time()
        threading.Thread(target=self._start_then_command if command else self.on_start, daemon=True).start()

    def _release_name(self, name):
        if self._capture is not None:
            return self._capture_release(name)
        self._down.discard(name)
        if name in MODIFIERS:  # some layouts report the generic name on release only
            self._down -= {k for k in list(self._down) if GENERIC.get(k) == name}
        if (self.recording and not self.locked and not self._tap_pending and self.s["hotkey_mode"] == "hold"
                and not self._combo_down(self._base)):
            if time.time() - self._started_at < TAP_MAX:
                # Quick tap: maybe the first half of a double-tap. Decide once the window closes.
                self._tap_pending = True
                self._tap_timer = threading.Timer(DOUBLE_TAP_WINDOW, self._tap_timeout)
                self._tap_timer.daemon = True
                self._tap_timer.start()
            else:
                self._finish()

    def _start_then_command(self):
        self.on_start()
        self.on_command()

    def _tap_timeout(self):
        if not self._tap_pending:
            return
        self._tap_pending = False
        self.recording = self.locked = False
        self.on_tap()

    def _cancel_tap(self):
        self._tap_pending = False
        if self._tap_timer:
            self._tap_timer.cancel()
            self._tap_timer = None

    def _finish(self):
        self.recording = self.locked = False
        threading.Thread(target=self.on_stop, daemon=True).start()

    def reset(self):
        self._down.clear()

    # ---------------- macOS: the Fn / Globe key ----------------
    def _mac_fn_tap(self):
        """Listen-only Quartz tap for flags-changed events; turns Fn transitions into "fn" press/release.
        Needs the same Input Monitoring permission as the rest of the shortcuts."""
        try:
            import Quartz

            fn_mask = Quartz.kCGEventFlagMaskSecondaryFn
            state = {"down": False}

            def callback(proxy, etype, event, refcon):
                if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                    Quartz.CGEventTapEnable(tap, True)  # macOS switches slow taps off; switch it back on
                    return event
                code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                if code in MAC_FN_KEYCODES:
                    down = bool(Quartz.CGEventGetFlags(event) & fn_mask)
                    if down != state["down"]:
                        state["down"] = down
                        if not injecting.is_set():
                            (self._press_name if down else self._release_name)("fn")
                return event

            tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                          Quartz.kCGEventTapOptionListenOnly,
                                          Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged), callback, None)
            if tap is None:
                log.warning("Fn key unavailable: grant Input Monitoring to Hush (or your terminal) and restart")
                return
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            Quartz.CFRunLoopRun()
        except Exception:
            log.exception("Fn key listener failed")

"""Simulates key sequences against the Hotkeys state machine: hold, single tap, double-tap hands-free, Esc."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pynput.keyboard import Key, KeyCode

from hush.hotkeys import Hotkeys

events = []
s = {"dictate_hotkey": ["ctrl", "cmd"], "command_hotkey": ["ctrl", "cmd", "alt"], "hotkey_mode": "hold",
     "transform_modifiers": ["cmd", "alt"]}
hk = Hotkeys(s, lambda: events.append("start"), lambda: events.append("stop"), lambda: events.append("cancel"),
             lambda: events.append("lock"), lambda n: events.append(f"tf{n}"))
hk.on_tap = lambda: events.append("tap")
hk.on_command = lambda: events.append("command")


def keys(press, hold, release):
    for k in press:
        hk._press(k); time.sleep(0.05)
    time.sleep(hold)
    for k in release:
        hk._release(k)


def combo(hold):
    hk._press(Key.ctrl_l); hk._press(Key.cmd); time.sleep(hold); hk._release(Key.cmd); hk._release(Key.ctrl_l)


def run(name, fn, expect):
    events.clear(); hk.recording = hk.locked = False; hk._cancel_tap(); hk.reset()
    fn(); time.sleep(0.6)
    print(("PASS" if events == expect else "FAIL"), name, events)


run("hold to talk", lambda: combo(1.0), ["start", "stop"])
run("single tap", lambda: combo(0.1), ["start", "tap"])
run("double tap then tap to stop", lambda: (combo(0.1), time.sleep(0.15), combo(0.1), time.sleep(1.0), combo(0.1)),
    ["start", "lock", "stop"])
run("double tap then Esc", lambda: (combo(0.1), time.sleep(0.15), combo(0.1), time.sleep(0.5),
                                    hk._press(Key.esc), hk._release(Key.esc)), ["start", "lock", "cancel"])
run("two slow taps", lambda: (combo(0.1), time.sleep(0.8), combo(0.1)), ["start", "tap", "start", "tap"])
run("command mode: hold Ctrl+Win+Alt", lambda: keys([Key.ctrl_l, Key.alt_l, Key.cmd], 1.0, [Key.cmd, Key.alt_l, Key.ctrl_l]),
    ["start", "command", "stop"])
run("command mode: add Alt while dictating", lambda: keys([Key.ctrl_l, Key.cmd], 0.3, []) or keys([Key.alt_l], 0.8,
    [Key.alt_l, Key.cmd, Key.ctrl_l]), ["start", "command", "stop"])
run("plain dictation is not command", lambda: combo(1.0), ["start", "stop"])
run("command shortcut without the dictate keys", lambda: (s.update(command_hotkey=["ctrl", "shift", "space"]),
    keys([Key.ctrl_l, Key.shift_l, Key.space], 1.0, [Key.space, Key.shift_l, Key.ctrl_l]),
    s.update(command_hotkey=["ctrl", "cmd", "alt"])), ["start", "command", "stop"])
run("single-key dictate (like Fn)", lambda: (s.update(dictate_hotkey=["alt_r"]), keys([Key.alt_r], 1.0, [Key.alt_r]),
    s.update(dictate_hotkey=["ctrl", "cmd"])), ["start", "stop"])
run("Fn key via the Mac tap path", lambda: (s.update(dictate_hotkey=["fn"]), hk._press_name("fn"), time.sleep(1.0),
    hk._release_name("fn"), s.update(dictate_hotkey=["ctrl", "cmd"])), ["start", "stop"])


def capture_case(press, release, want):
    out = {}
    t = __import__("threading").Thread(target=lambda: out.update(c=hk.capture(timeout=3)))
    t.start(); time.sleep(0.2)
    keys(press, 0.2, release); t.join()
    print(("PASS" if out.get("c") == want else "FAIL"), "capture", want, "->", out.get("c"))


capture_case([Key.ctrl_l, Key.cmd], [Key.cmd, Key.ctrl_l], ["cmd", "ctrl"])
capture_case([Key.alt_r], [Key.alt_r], ["alt_r"])
capture_case([Key.esc], [Key.esc], [])
run("shortcuts still work after capturing", lambda: combo(1.0), ["start", "stop"])
run("transform Win+Alt+1 still works", lambda: keys([Key.cmd, Key.alt_l, KeyCode.from_char("1")], 0.1,
    [KeyCode.from_char("1"), Key.alt_l, Key.cmd]), ["tf1"])

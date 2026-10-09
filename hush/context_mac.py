"""macOS counterpart of context_win: reads the focused text box through the Accessibility API (the same
permission Hush already needs to paste), for "read the room" and learning from corrections.

Chromium/Electron apps (Slack, Discord, Claude, VS Code, Chrome) only expose their text after being asked via
AXManualAccessibility, which we set once per app. Visible "nearby" text isn't read on macOS yet.
"""
import logging
import threading
import time

log = logging.getLogger("flow.context")

MAX_BEFORE, MAX_AFTER = 1500, 300
_enabled_pids: set[int] = set()


def _ax():
    import ApplicationServices as AS  # pyobjc-framework-ApplicationServices
    return AS


def _get(AS, element, attr):
    err, value = AS.AXUIElementCopyAttributeValue(element, attr, None)
    return value if err == 0 else None


def _wake_electron(AS):
    """Ask the frontmost app to build its accessibility tree (needed for Chromium/Electron)."""
    from AppKit import NSWorkspace

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    pid = int(app.processIdentifier()) if app else 0
    if pid and pid not in _enabled_pids:
        _enabled_pids.add(pid)
        try:
            AS.AXUIElementSetAttributeValue(AS.AXUIElementCreateApplication(pid), "AXManualAccessibility", True)
            time.sleep(0.05)
        except Exception:
            pass


def _focused(AS):
    _wake_electron(AS)
    return _get(AS, AS.AXUIElementCreateSystemWide(), AS.kAXFocusedUIElementAttribute)


def _capture():
    AS = _ax()
    out = {"before": "", "after": "", "selected": "", "nearby": "", "field": "", "editable": False}
    el = _focused(AS)
    if el is None:
        return out
    out["field"] = str(_get(AS, el, AS.kAXTitleAttribute) or _get(AS, el, AS.kAXDescriptionAttribute) or "")[:120]
    value = _get(AS, el, AS.kAXValueAttribute)
    if not isinstance(value, str):
        return out
    out["editable"] = True
    rng = _get(AS, el, AS.kAXSelectedTextRangeAttribute)
    loc, length = len(value), 0  # no caret info: assume typing at the end
    if rng is not None:
        ok, r = AS.AXValueGetValue(rng, AS.kAXValueTypeCFRange, None)
        if ok:
            loc, length = int(r.location), int(r.length)  # UTF-16 units; close enough outside emoji-heavy text
    out["before"] = value[:loc][-MAX_BEFORE:]
    out["selected"] = value[loc:loc + length]
    out["after"] = value[loc + length:][:MAX_AFTER]
    return out


def read_field():
    """Full text of the focused text box right now (for learning from corrections). None if unreadable."""
    try:
        AS = _ax()
        el = _focused(AS)
        value = _get(AS, el, AS.kAXValueAttribute) if el is not None else None
        return value if isinstance(value, str) else None
    except Exception:
        log.debug("read_field failed", exc_info=True)
        return None


def window_title(pid: int) -> str:
    """Title of an app's focused window (the page title for browsers), used for app detection and app rules."""
    try:
        AS = _ax()
        win = _get(AS, AS.AXUIElementCreateApplication(pid), AS.kAXFocusedWindowAttribute)
        title = _get(AS, win, AS.kAXTitleAttribute) if win is not None else None
        return str(title or "")
    except Exception:
        return ""


class ContextGrab:
    """Same interface as context_win.ContextGrab: capture in the background while the user talks."""

    def __init__(self):
        self._res = None
        self._done = threading.Event()
        self.t0 = time.time()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self._res = _capture()
        except Exception:
            log.debug("context capture failed", exc_info=True)
        self._done.set()

    def result(self, wait=0.3):
        self._done.wait(wait)
        return self._res or {}

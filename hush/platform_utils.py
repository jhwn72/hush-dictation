"""OS glue: which app is focused, pasting text, grabbing the current selection."""
import re
import threading
import time

import pyperclip
from pynput.keyboard import Controller, Key

from .config import IS_MAC, IS_WIN

_kb = Controller()
injecting = threading.Event()  # set while we send synthetic keys, so the hotkey listener ignores them

CATEGORY_RULES = [
    ("ai", ["chatgpt", "claude", "cursor", "copilot", "gemini", "perplexity", "windsurf", "codex", "grok", "poe"]),
    ("email", ["outlook", "olk.exe", "thunderbird", "gmail", "superhuman", "spark", "mail.exe", " mail", "proton mail", "hey.com"]),
    ("work", ["slack", "teams", "zoom", "webex", "linear", "jira", "asana", "clickup"]),
    ("personal", ["whatsapp", "telegram", "discord", "instagram", "messenger", "signal", "messages", "imessage", "wechat", "snapchat"]),
    ("documents", ["winword", "word", "google docs", "notion", "obsidian", "notepad", "pages", "onenote", "evernote", "craft", "bear"]),
]

FRIENDLY = {
    "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox", "brave.exe": "Brave", "arc.exe": "Arc",
    "slack.exe": "Slack", "discord.exe": "Discord", "winword.exe": "Word", "outlook.exe": "Outlook",
    "olk.exe": "Outlook", "code.exe": "VS Code", "cursor.exe": "Cursor", "claude.exe": "Claude",
    "chatgpt.exe": "ChatGPT", "notion.exe": "Notion", "whatsapp.exe": "WhatsApp", "telegram.exe": "Telegram",
    "ms-teams.exe": "Teams", "notepad.exe": "Notepad", "explorer.exe": "Explorer",
}
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "arc.exe", "opera.exe", "vivaldi.exe",
            "Google Chrome", "Safari", "Firefox", "Arc", "Brave Browser", "Microsoft Edge"}


def active_app() -> dict:
    """{'name': friendly app name, 'process': raw process/app name, 'title': window title}"""
    try:
        if IS_WIN:
            return _active_app_win()
        if IS_MAC:
            return _active_app_mac()
    except Exception:
        pass
    return {"name": "", "process": "", "title": ""}


def _active_app_win():
    import ctypes
    import ctypes.wintypes

    import psutil

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    proc = psutil.Process(pid.value).name()
    name = FRIENDLY.get(proc.lower(), proc.rsplit(".", 1)[0].capitalize())
    return {"name": name, "process": proc, "title": buf.value}


def _active_app_mac():
    from AppKit import NSWorkspace

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    name = str(app.localizedName() or "")
    title = ""
    try:  # the focused window's title (the page title in browsers) via Accessibility
        from .context_mac import window_title
        pid = int(app.processIdentifier())
        title = window_title(pid) if pid > 0 else ""
    except Exception:
        pass
    return {"name": name, "process": name, "title": title}


_BROWSER_SUFFIX = re.compile(
    r"\s+and \d+ more pages?|\s+-\s+(Microsoft\W*Edge|Google Chrome|Mozilla Firefox|Brave|Opera|Vivaldi|Arc)$", re.I)


def categorize(app: dict) -> str:
    proc = (app.get("process") or "").lower()
    title = app.get("title") or ""
    is_browser = app.get("process") in BROWSERS or proc in BROWSERS
    # In browsers the site is in the title ("Inbox - Gmail - Google Chrome"), minus the browser's own suffix
    # ("and 13 more pages - Personal - Microsoft Edge"). Elsewhere titles hold file names, so use the process only.
    haystack = f" {_BROWSER_SUFFIX.sub('', title).lower()} " if is_browser else f" {proc} "
    for cat, needles in CATEGORY_RULES:
        if any(re.search(r"(?<![a-z])" + re.escape(n.strip()) + r"(?![a-z])", haystack) for n in needles):
            return cat
    return "other"


def matching_rules(app: dict, rules: list[dict]) -> list[str]:
    """Instructions from the user's app rules that apply to this app. A rule's app ("Slack", "gmail",
    "Cursor") matches the app name or process, or, in a browser, the site in the tab title."""
    name = (app.get("name") or "").lower()
    proc = (app.get("process") or "").lower().removesuffix(".exe")
    is_browser = app.get("process") in BROWSERS or (app.get("process") or "").lower() in BROWSERS
    site = _BROWSER_SUFFIX.sub("", app.get("title") or "").lower() if is_browser else ""
    out = []
    for r in rules:
        if not r.get("enabled", 1):
            continue
        key = (r.get("app") or "").strip().lower().removesuffix(".exe")
        if not key:
            continue
        word = re.compile(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])")
        if key in (name, proc) or word.search(name) or (site and word.search(site)):
            out.append(r["instruction"].strip())
    return out


def foreground_window() -> int:
    """Identifies where the user is typing: the window handle on Windows, the frontmost app's pid on macOS."""
    if IS_WIN:
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow()
    if IS_MAC:
        try:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            pid = int(app.processIdentifier()) if app else 0
            return pid if pid > 0 else 0  # -1 when macOS can't tell (e.g. our own unbundled process)
        except Exception:
            return 0
    return 0


def focus_window(hwnd: int):
    """Give focus back to the window (Windows) or app (macOS, by pid) we're dictating into."""
    if IS_MAC and hwnd:
        try:
            from AppKit import NSRunningApplication
            if foreground_window() != hwnd:
                app = NSRunningApplication.runningApplicationWithProcessIdentifier_(hwnd)
                if app:
                    app.activateWithOptions_(0)
                    time.sleep(0.1)
        except Exception:
            pass
        return
    if not IS_WIN or not hwnd:
        return
    import ctypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    fg = user32.GetForegroundWindow()
    if fg == hwnd or not user32.IsWindow(hwnd):
        return
    # Windows only lets the foreground thread hand out focus; borrow its input state for a moment
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(me, fg_tid, True)
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        user32.AttachThreadInput(me, fg_tid, False)
    time.sleep(0.05)


def wait_keys_released(timeout: float = 2.0):
    """Block until Ctrl/Alt/Shift/Win are physically up, so our Ctrl+V isn't read as Win+Ctrl+V."""
    if IS_MAC:
        try:
            import Quartz
            mods = (Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskAlternate
                    | Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskShift)
            end = time.time() + timeout
            while time.time() < end and Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateHIDSystemState) & mods:
                time.sleep(0.02)
        except Exception:
            time.sleep(0.15)
        return
    if not IS_WIN:
        return
    import ctypes
    gaks = ctypes.windll.user32.GetAsyncKeyState
    end = time.time() + timeout
    while time.time() < end:
        if not any(gaks(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            return
        time.sleep(0.02)


def _chord(key):
    mod = Key.cmd if IS_MAC else Key.ctrl
    with _kb.pressed(mod):
        _kb.press(key)
        _kb.release(key)


def _safe_paste() -> str:
    try:
        return pyperclip.paste() or ""
    except Exception:
        return ""


def paste_text(text: str, restore: bool = True, target_hwnd: int = 0):
    old = _safe_paste() if restore else None
    pyperclip.copy(text)
    wait_keys_released()
    focus_window(target_hwnd)
    time.sleep(0.04)
    injecting.set()
    try:
        _chord("v")
    finally:
        time.sleep(0.05)
        injecting.clear()
    if restore and old is not None:
        def put_back():
            if _safe_paste() == text:  # don't clobber something the user copied in the meantime
                pyperclip.copy(old)
        threading.Timer(0.8, put_back).start()


def press_enter():
    """'Press enter' / 'send it': submit after pasting (Slack, ChatGPT, Messages...)."""
    wait_keys_released()
    time.sleep(0.12)  # let the app finish handling the paste first
    injecting.set()
    try:
        _kb.press(Key.enter)
        _kb.release(Key.enter)
    finally:
        time.sleep(0.03)
        injecting.clear()


def erase_typed(n_chars: int = 0):
    """'Scratch that': remove what we just pasted. Backspaces when we know exactly how much it was,
    otherwise the app's own undo (Ctrl/Cmd+Z), which reverts a paste as one step in nearly every app."""
    wait_keys_released()
    injecting.set()
    try:
        if n_chars:
            for _ in range(n_chars):
                _kb.press(Key.backspace)
                _kb.release(Key.backspace)
                time.sleep(0.004)
        else:
            _chord("z")
    finally:
        time.sleep(0.03)
        injecting.clear()


def copy_selection(timeout: float = 0.5) -> tuple[str, str]:
    """Copy the focused app's selection. Returns (selection, previous clipboard)."""
    old = _safe_paste()
    sentinel = f"__hush_{time.time()}__"
    pyperclip.copy(sentinel)
    wait_keys_released()
    injecting.set()
    try:
        _chord("c")
    finally:
        time.sleep(0.05)
        injecting.clear()
    end = time.time() + timeout
    sel = sentinel
    while time.time() < end:
        sel = _safe_paste()
        if sel != sentinel:
            break
        time.sleep(0.03)
    if sel == sentinel:
        pyperclip.copy(old)
        return "", old
    return sel, old


def restore_clipboard(old: str):
    try:
        pyperclip.copy(old)
    except Exception:
        pass

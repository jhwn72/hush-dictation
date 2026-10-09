"""App shortcuts and "Open Hush when you log in", per OS. No admin rights needed on either.

Windows: a Start menu shortcut (created on first launch) and an optional shortcut in the Startup folder.
macOS:   ~/Applications/Hush.app (built by tools/install_mac_app.sh) and an optional LaunchAgent.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

from .config import IS_MAC, IS_WIN

log = logging.getLogger("flow.autostart")
ROOT = Path(__file__).resolve().parent.parent
MAC_APP = Path.home() / "Applications" / "Hush.app"
MAC_AGENT = Path.home() / "Library" / "LaunchAgents" / "app.hush.dictation.plist"


def _win_programs() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _win_shortcut(path: Path):
    """A .lnk that starts Hush with no console window, with the Hush icon."""
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:HUSH_LNK); "
        "$s.TargetPath = $env:HUSH_EXE; $s.Arguments = '-m hush'; $s.WorkingDirectory = $env:HUSH_DIR; "
        "$s.IconLocation = $env:HUSH_ICO; $s.Description = 'Hush voice dictation'; $s.Save()"
    )
    env = dict(os.environ, HUSH_LNK=str(path), HUSH_EXE=str(pythonw), HUSH_DIR=str(ROOT),
               HUSH_ICO=str(ROOT / "ui" / "icon.ico"))
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   creationflags=0x08000000, timeout=20)  # CREATE_NO_WINDOW


def ensure_app_shortcut():
    """Windows: put Hush in the Start menu (once). macOS: the .app is built by run.sh."""
    if not IS_WIN:
        return
    lnk = _win_programs() / "Hush.lnk"
    if not lnk.exists():
        try:
            _win_shortcut(lnk)
            log.info("created Start menu shortcut")
        except Exception:
            log.exception("couldn't create the Start menu shortcut")


def is_enabled() -> bool:
    if IS_WIN:
        return (_win_programs() / "Startup" / "Hush.lnk").exists()
    if IS_MAC:
        return MAC_AGENT.exists()
    return False


def set_enabled(on: bool) -> bool:
    """Start Hush at login (or stop doing so). Returns the new state."""
    try:
        if IS_WIN:
            lnk = _win_programs() / "Startup" / "Hush.lnk"
            if on:
                _win_shortcut(lnk)
            elif lnk.exists():
                lnk.unlink()
        elif IS_MAC:
            if on:
                # open the .app (so permissions stay attributed to "Hush"); fall back to running from the folder
                args = (["/usr/bin/open", "-a", str(MAC_APP)] if MAC_APP.exists()
                        else [str(ROOT / ".venv" / "bin" / "python"), "-m", "hush"])
                items = "".join(f"<string>{a}</string>" for a in args)
                MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
                MAC_AGENT.write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict><key>Label</key><string>app.hush.dictation</string>'
                    f"<key>ProgramArguments</key><array>{items}</array>"
                    f"<key>WorkingDirectory</key><string>{ROOT}</string>"
                    "<key>RunAtLoad</key><true/></dict></plist>\n", encoding="utf-8")
            elif MAC_AGENT.exists():
                MAC_AGENT.unlink()
    except Exception:
        log.exception("couldn't change open-at-login")
    return is_enabled()


def mac_brand_process():
    """macOS: show "Hush" (not "python") in the menu bar and Dock while running from the .app or a terminal."""
    if not IS_MAC:
        return
    try:
        from Foundation import NSBundle
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = "Hush"
    except Exception:
        log.debug("couldn't rename the process", exc_info=True)


def mac_set_dock_icon():
    """Call on the main thread once the app is running."""
    try:
        from AppKit import NSApplication, NSImage
        img = NSImage.alloc().initWithContentsOfFile_(str(ROOT / "ui" / "icon.png"))
        if img is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception:
        log.debug("couldn't set the Dock icon", exc_info=True)


if __name__ == "__main__":  # python -m hush.autostart on|off
    print(set_enabled(len(sys.argv) > 1 and sys.argv[1] == "on"))

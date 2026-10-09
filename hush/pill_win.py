"""Recording pill host for Windows (drawing lives in pill_core).

A per-pixel-alpha layered window drawn with Pillow. It is created with WS_EX_NOACTIVATE and answers
WM_MOUSEACTIVATE with MA_NOACTIVATE, so neither showing it nor clicking it ever takes focus away from the
app you're dictating into. Fully transparent pixels are click-through, so only the pill itself is clickable.

States: idle (tiny bar; hover expands into mic + notetaker buttons with a tooltip), recording (waveform),
locked (hands-free: cancel, waveform, confirm), processing (dot shimmer), msg (dot + text), hint (tap warning).
"""
import ctypes
import logging
import threading
import time
from ctypes import wintypes as W

import numpy as np

from .pill_core import PillCore

log = logging.getLogger("flow.pill")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shcore = ctypes.WinDLL("shcore")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON), ("hCursor", W.HANDLE),
                ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR), ("hIconSm", W.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("dwFlags", W.DWORD), ("hwndTrack", W.HWND), ("dwHoverTime", W.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]


user32.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
user32.CreateWindowExW.restype = W.HWND
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.UpdateLayeredWindow.argtypes = [W.HWND, W.HDC, ctypes.POINTER(W.POINT), ctypes.POINTER(W.SIZE), W.HDC,
                                       ctypes.POINTER(W.POINT), W.DWORD, ctypes.POINTER(BLENDFUNCTION), W.DWORD]
user32.GetDC.argtypes = [W.HWND]
user32.GetDC.restype = W.HDC
user32.ReleaseDC.argtypes = [W.HWND, W.HDC]
user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.SetTimer.argtypes = [W.HWND, ctypes.c_size_t, W.UINT, ctypes.c_void_p]
user32.KillTimer.argtypes = [W.HWND, ctypes.c_size_t]
user32.GetMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(W.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(W.MSG)]
user32.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT]
user32.LoadCursorW.argtypes = [W.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = W.HANDLE
user32.SetCursor.argtypes = [W.HANDLE]
user32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
user32.GetForegroundWindow.restype = W.HWND
user32.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
user32.MonitorFromWindow.restype = W.HMONITOR
user32.GetMonitorInfoW.argtypes = [W.HMONITOR, ctypes.POINTER(MONITORINFO)]
gdi32.CreateCompatibleDC.argtypes = [W.HDC]
gdi32.CreateCompatibleDC.restype = W.HDC
gdi32.CreateDIBSection.argtypes = [W.HDC, ctypes.c_void_p, W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]
gdi32.CreateDIBSection.restype = W.HBITMAP
gdi32.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
gdi32.SelectObject.restype = W.HGDIOBJ
gdi32.DeleteObject.argtypes = [W.HGDIOBJ]
gdi32.DeleteDC.argtypes = [W.HDC]
kernel32.GetModuleHandleW.restype = W.HMODULE
shcore.GetDpiForMonitor.argtypes = [W.HMONITOR, ctypes.c_int, ctypes.POINTER(W.UINT), ctypes.POINTER(W.UINT)]

WM_TIMER, WM_MOUSEMOVE, WM_LBUTTONUP, WM_MOUSELEAVE = 0x0113, 0x0200, 0x0202, 0x02A3
WM_SETCURSOR, WM_MOUSEACTIVATE, WM_APP = 0x0020, 0x0021, 0x8000
MA_NOACTIVATE, TME_LEAVE = 3, 0x2
IDC_ARROW, IDC_HAND = 32512, 32649


class Pill(PillCore):
    def __init__(self, on_mic, on_cancel, on_confirm, on_note):
        super().__init__(on_mic, on_cancel, on_confirm, on_note)
        self._timer = False
        self._tracking = False
        self.hwnd = None
        self._proc = WNDPROC(self._wndproc)
        self._ready = threading.Event()
        threading.Thread(target=self._run, name="pill", daemon=True).start()
        self._ready.wait(3)

    def _wake(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP, 0, 0)

    def _stop_timer(self):
        user32.KillTimer(self.hwnd, 1)
        self._timer = False

    def _hide(self):
        user32.ShowWindow(self.hwnd, 0)

    # ---------------- window thread ----------------
    def _run(self):
        try:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2: crisp at any scale
        except (AttributeError, OSError):
            pass
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
        wc.lpszClassName = "FlowPill"
        user32.RegisterClassExW(ctypes.byref(wc))
        ex = 0x00080000 | 0x00000008 | 0x00000080 | 0x08000000  # LAYERED | TOPMOST | TOOLWINDOW | NOACTIVATE
        self.hwnd = user32.CreateWindowExW(ex, "FlowPill", "Hush pill", 0x80000000, 0, 0, 1, 1, None, None, hinst, None)
        self._refresh_monitor()
        self._render()
        user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
        self._ready.set()
        msg = W.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _refresh_monitor(self):
        mon = user32.MonitorFromWindow(user32.GetForegroundWindow(), 1)  # MONITOR_DEFAULTTOPRIMARY
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(mon, ctypes.byref(mi))
        dx, dy = W.UINT(96), W.UINT(96)
        try:
            shcore.GetDpiForMonitor(mon, 0, ctypes.byref(dx), ctypes.byref(dy))
        except OSError:
            pass
        self._scale = dx.value / 96
        wk = mi.rcWork
        self._anchor = ((wk.left + wk.right) // 2, wk.bottom - round(10 * self._scale))

    def _start_timer(self):
        if not self._timer:
            self._t_last = time.time()
            user32.SetTimer(self.hwnd, 1, 15, None)
            self._timer = True

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_APP:
                self._start_timer()
                return 0
            if msg == WM_TIMER:
                self._tick()
                return 0
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE
            if msg == WM_MOUSEMOVE:
                if not self._tracking:
                    tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0)
                    user32.TrackMouseEvent(ctypes.byref(tme))
                    self._tracking = True
                self._mouse_move(ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value)
                return 0
            if msg == WM_MOUSELEAVE:
                self._tracking = False
                self._mouse_leave()
                return 0
            if msg == WM_SETCURSOR and self._clickable():
                user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND)))
                return 1
            if msg == WM_LBUTTONUP:
                self._mouse_up(ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value)
                return 0
        except Exception:
            log.exception("pill wndproc")
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _blit(self, im, x, y):
        w, h = im.size
        a = np.asarray(im, dtype=np.uint16)
        alpha = a[..., 3:4]
        rgb = (a[..., :3] * alpha + 127) // 255  # UpdateLayeredWindow wants premultiplied BGRA
        bgra = np.empty((h, w, 4), np.uint8)
        bgra[..., 0], bgra[..., 1], bgra[..., 2] = rgb[..., 2], rgb[..., 1], rgb[..., 0]
        bgra[..., 3] = alpha[..., 0]
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, bgra.tobytes(), w * h * 4)
        old = gdi32.SelectObject(mem, hbmp)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, per-pixel alpha
        user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(W.POINT(x, y)), ctypes.byref(W.SIZE(w, h)), mem,
                                   ctypes.byref(W.POINT(0, 0)), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
        user32.ShowWindow(self.hwnd, 4)
        user32.SetWindowPos(self.hwnd, W.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)  # stay topmost, never activate

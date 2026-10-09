"""Recording pill host for macOS (drawing lives in pill_core).

A borderless, transparent, non-activating NSPanel at status-window level: it floats over every app and
full-screen space, and clicking it never takes focus from the app being dictated into. AppKit must only be
touched on the main thread (pywebview runs the Cocoa event loop there), so every window operation is
scheduled with AppHelper.callAfter and the animation runs on an NSTimer.
"""
import io
import logging
import threading
import time

import objc
from AppKit import (NSBackingStoreBuffered, NSColor, NSCursor, NSImage, NSPanel, NSScreen, NSStatusWindowLevel,
                    NSTrackingActiveAlways, NSTrackingArea, NSTrackingInVisibleRect, NSTrackingMouseEnteredAndExited,
                    NSTrackingMouseMoved, NSView, NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowCollectionBehaviorStationary,
                    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel)
from Foundation import NSData, NSMakeRect, NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer
from PyObjCTools import AppHelper

from .pill_core import PillCore

log = logging.getLogger("flow.pill")


class _PillView(NSView):
    """Draws the latest frame and forwards mouse events (in pixels from the top-left) to the pill."""

    def initWithPill_(self, pill):
        self = objc.super(_PillView, self).initWithFrame_(NSMakeRect(0, 0, 10, 10))
        if self is None:
            return None
        self.pill = pill
        self.image = None
        return self

    def isFlipped(self):
        return True  # y grows downward, like the core's coordinates

    def acceptsFirstMouse_(self, event):
        return True

    def updateTrackingAreas(self):
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        opts = NSTrackingMouseMoved | NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(self.bounds(), opts, self, None))
        objc.super(_PillView, self).updateTrackingAreas()

    def drawRect_(self, rect):
        if self.image is not None:
            self.image.drawInRect_(self.bounds())

    @objc.python_method  # a plain Python helper, not an Objective-C method (PyObjC would reject its signature)
    def _px(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        s = self.pill._scale
        return p.x * s, p.y * s

    def mouseMoved_(self, event):
        try:
            self.pill._mouse_move(*self._px(event))
            (NSCursor.pointingHandCursor() if self.pill._clickable() else NSCursor.arrowCursor()).set()
        except Exception:
            log.exception("pill mouse")

    def mouseExited_(self, event):
        self.pill._mouse_leave()
        NSCursor.arrowCursor().set()

    def mouseUp_(self, event):
        try:
            self.pill._mouse_up(*self._px(event))
        except Exception:
            log.exception("pill click")


class _Ticker(NSObject):
    def initWithPill_(self, pill):
        self = objc.super(_Ticker, self).init()
        if self is None:
            return None
        self.pill = pill
        return self

    def tick_(self, timer):
        try:
            self.pill._tick()
        except Exception:
            log.exception("pill tick")


class Pill(PillCore):
    def __init__(self, on_mic, on_cancel, on_confirm, on_note):
        super().__init__(on_mic, on_cancel, on_confirm, on_note)
        self.panel = None
        self._view = None
        self._timer = None
        self._ticker = None
        self._ready = threading.Event()
        AppHelper.callAfter(self._setup)
        if not self._ready.wait(3):
            raise RuntimeError("macOS pill: the main thread didn't create the panel in time")

    # ---- main thread only ----
    def _setup(self):
        try:
            style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 10, 10), style, NSBackingStoreBuffered, False)
            panel.setOpaque_(False)
            panel.setBackgroundColor_(NSColor.clearColor())
            panel.setHasShadow_(False)  # the pill draws its own soft shadow
            panel.setLevel_(NSStatusWindowLevel)
            panel.setFloatingPanel_(True)
            panel.setBecomesKeyOnlyIfNeeded_(True)
            panel.setHidesOnDeactivate_(False)
            panel.setAcceptsMouseMovedEvents_(True)
            panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
                                         | NSWindowCollectionBehaviorFullScreenAuxiliary)
            self._view = _PillView.alloc().initWithPill_(self)
            panel.setContentView_(self._view)
            self.panel = panel
            self._ticker = _Ticker.alloc().initWithPill_(self)
            self._do_refresh_monitor()
            self._render()
            panel.orderFrontRegardless()
        except Exception:
            log.exception("macOS pill setup failed")
            return
        self._ready.set()

    def _start_timer(self):
        if self._timer is None and self._ticker is not None:
            self._t_last = time.time()
            self._timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                1 / 60, self._ticker, "tick:", None, True)
            NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)

    def _do_refresh_monitor(self):
        screen = NSScreen.mainScreen() or NSScreen.screens()[0]  # the screen the user is working on
        self._scale = float(screen.backingScaleFactor())
        vf = screen.visibleFrame()  # excludes the menu bar and the Dock
        top = NSScreen.screens()[0].frame().size.height  # Cocoa's y=0 is the bottom of the primary screen
        self._top = top
        cx = vf.origin.x + vf.size.width / 2
        bottom_from_top = top - (vf.origin.y + 10)
        self._anchor = (cx * self._scale, bottom_from_top * self._scale)

    # ---- host hooks (any thread -> main thread) ----
    def _wake(self):
        AppHelper.callAfter(self._start_timer)

    def _stop_timer(self):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def _hide(self):
        if self.panel is not None:
            self.panel.orderOut_(None)

    def _refresh_monitor(self):
        AppHelper.callAfter(self._do_refresh_monitor)

    def _blit(self, im, x, y):
        """Called from _render on the main thread: put the frame in the panel, sized and placed in points."""
        s = self._scale
        w_pt, h_pt = im.size[0] / s, im.size[1] / s
        buf = io.BytesIO()
        im.save(buf, format="PNG", compress_level=1)
        data = buf.getvalue()
        img = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
        img.setSize_((w_pt, h_pt))
        frame = NSMakeRect(x / s, self._top - y / s - h_pt, w_pt, h_pt)
        if not self.panel.isVisible():
            self.panel.orderFrontRegardless()
        self.panel.setFrame_display_(frame, False)
        self._view.setFrame_(NSMakeRect(0, 0, w_pt, h_pt))
        self._view.image = img
        self._view.setNeedsDisplay_(True)

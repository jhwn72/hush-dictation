"""Reads what's around the cursor in the focused app (Windows UI Automation), so cleanup can "read the room":
the text right before/after the caret, any selection, and the visible text nearby (e.g. the chat you're replying to).

Runs while you're still speaking, so it adds no latency. Everything here is best-effort: apps that don't
expose UI Automation text just yield empty fields.
"""
import logging
import threading
import time

log = logging.getLogger("flow.context")

MAX_BEFORE, MAX_AFTER, MAX_NEARBY = 1500, 300, 2500


def _text(rng, limit):
    try:
        return rng.GetText(limit) or ""
    except Exception:
        return ""


def _capture():
    import uiautomation as auto

    out = {"before": "", "after": "", "selected": "", "nearby": "", "field": "", "editable": False}
    with auto.UIAutomationInitializerInThread(debug=False):
        ctrl = auto.GetFocusedControl()
        if not ctrl:
            return out
        out["field"] = (ctrl.Name or "")[:120]
        tp = None
        try:
            tp = ctrl.GetPattern(auto.PatternId.TextPattern)
        except Exception:
            tp = None
        if tp:
            out["editable"] = True
            try:
                sel = tp.GetSelection()
                doc = tp.DocumentRange
                if sel:
                    s = sel[0]
                    out["selected"] = _text(s, 2000)
                    before = doc.Clone()
                    before.MoveEndpointByRange(auto.TextPatternRangeEndpoint.End, s, auto.TextPatternRangeEndpoint.Start)
                    out["before"] = _text(before, 20000)[-MAX_BEFORE:]
                    after = doc.Clone()
                    after.MoveEndpointByRange(auto.TextPatternRangeEndpoint.Start, s, auto.TextPatternRangeEndpoint.End)
                    out["after"] = _text(after, MAX_AFTER)
            except Exception:
                log.debug("text pattern read failed", exc_info=True)
        else:
            try:
                vp = ctrl.GetPattern(auto.PatternId.ValuePattern)
                if vp and not vp.IsReadOnly:
                    out["editable"] = True
                    out["before"] = (vp.Value or "")[-MAX_BEFORE:]  # no caret info: assume typing at the end
            except Exception:
                pass

        # Visible text of the surrounding page/document (the email or chat being replied to)
        node, hops = ctrl, 0
        while node and hops < 25:
            try:
                if node.ControlTypeName == "DocumentControl" and node is not ctrl:
                    dp = node.GetPattern(auto.PatternId.TextPattern)
                    if dp:
                        parts = [_text(r, MAX_NEARBY) for r in (dp.GetVisibleRanges() or [])]
                        out["nearby"] = " ".join(p.strip() for p in parts if p.strip())[-MAX_NEARBY:]
                    break
                node = node.GetParentControl()
            except Exception:
                break
            hops += 1
    return out


def read_field():
    """Full text of the focused text box right now (for learning from corrections). None if unreadable."""
    import uiautomation as auto

    try:
        with auto.UIAutomationInitializerInThread(debug=False):
            ctrl = auto.GetFocusedControl()
            if not ctrl:
                return None
            tp = ctrl.GetPattern(auto.PatternId.TextPattern)
            if tp:
                return _text(tp.DocumentRange, 20000)
            vp = ctrl.GetPattern(auto.PatternId.ValuePattern)
            if vp and not vp.IsReadOnly:
                return vp.Value or ""
    except Exception:
        log.debug("read_field failed", exc_info=True)
    return None


class ContextGrab:
    """Starts a capture in the background; .result(wait) returns whatever finished in time."""

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

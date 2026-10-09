"""Learn from corrections: after a dictation is pasted, watch the text box for a short while. If the user fixes a
word Hush typed, and the fix looks like vocabulary (a name, brand, acronym) that sounds like what was typed,
add it to the dictionary so Whisper spells it right next time.

Detection is deliberately conservative: rewording ("big" -> "huge"), swapping in a different name, and plain
lowercase words are ignored. Everything runs locally; nothing here calls an LLM.
"""
import difflib
import logging
import re
import threading
import time

log = logging.getLogger("flow.learn")

WATCH_SECONDS = 60
POLL_SECONDS = 1.0
TOKEN = re.compile(r"\S+")
_PUNCT = ".,!?;:\"'()[]{}“”‘’…"


def _bare(w: str) -> str:
    return w.strip(_PUNCT)


def _region(snapshot: str, before: str, after: str):
    """The part of the box that holds what we typed, located by the text around it. None if it can't be found."""
    start, end = 0, len(snapshot)
    b = before[-60:]
    if b.strip():
        i = snapshot.find(b)
        if i < 0:
            return None
        start = i + len(b)
    a = after[:40]
    if a.strip():
        j = snapshot.find(a, start)
        if j < 0:
            return None
        end = j
    return snapshot[start:end]


def _namey(word: str, sentence_start: bool) -> bool:
    """Looks like vocabulary rather than an ordinary word."""
    w = _bare(word)
    if len(w) < 2:
        return False
    if any(c.isdigit() for c in w) or any(c in w for c in "@_#/+&"):
        return True
    if w.isupper():                       # PIXELFORGE, API
        return True
    if any(c.isupper() for c in w[1:]):   # iPhone, Hush, McDonald
        return True
    return w[0].isupper() and not sentence_start  # Capitalized mid-sentence: a name


def find_corrections(inserted: str, region: str, known: set[str]) -> list[tuple[str, str]]:
    """(what Hush typed, what the user changed it to) pairs worth learning."""
    old = TOKEN.findall(inserted)
    new = TOKEN.findall(region)
    if not old or not new:
        return []
    sm = difflib.SequenceMatcher(a=[_bare(w).lower() for w in old], b=[_bare(w).lower() for w in new], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            # same letters, different capitalization: "vercel" -> "Vercel", "pixelforge" -> "PIXELFORGE"
            for k in range(i2 - i1):
                o, n = _bare(old[i1 + k]), _bare(new[j1 + k])
                start = (j1 + k) == 0 or new[j1 + k - 1][-1:] in ".!?"
                if o != n and n.lower() not in known and _namey(n, start):
                    out.append((o, n))
            continue
        if op != "replace" or i2 - i1 > 3 or j2 - j1 > 3:
            continue  # only small, local word swaps; big rewrites are edits, not spelling fixes
        heard = " ".join(_bare(w) for w in old[i1:i2]).strip()
        fixed = " ".join(_bare(w) for w in new[j1:j2]).strip()
        if not heard or not fixed or heard == fixed:
            continue
        if fixed.lower() in known:
            continue
        sentence_start = j1 == 0 or new[j1 - 1][-1:] in ".!?"
        if not any(_namey(w, sentence_start and k == 0) for k, w in enumerate(fixed.split())):
            continue
        # must sound/look like what was typed: "zen tricks" -> "Zentrix" yes, "the client" -> "Acme" no
        similarity = difflib.SequenceMatcher(a=heard.lower().replace(" ", ""), b=fixed.lower().replace(" ", "")).ratio()
        same_letters = heard.lower().replace(" ", "") == fixed.lower().replace(" ", "")  # pixelforge -> PIXELFORGE
        if not same_letters and similarity < 0.4:
            continue
        out.append((heard, fixed))
    return out


class CorrectionWatcher:
    """Watches one dictation at a time. A new dictation (or leaving the window) ends the watch."""

    def __init__(self, read_field, foreground, known_words, on_learn):
        self._read_field = read_field      # () -> current text of the focused box, or None
        self._foreground = foreground      # () -> foreground window handle
        self._known = known_words          # () -> set of lowercased dictionary phrases
        self._on_learn = on_learn          # (heard, fixed) -> None
        self._job = None
        self._lock = threading.Lock()

    def watch(self, hwnd, before, inserted, after):
        self.finish_now()
        job = {"hwnd": hwnd, "before": before, "inserted": inserted.strip(), "after": after,
               "last": None, "stop": threading.Event()}
        with self._lock:
            self._job = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def finish_now(self):
        """Called when a new dictation starts: judge the previous one with the last good snapshot."""
        with self._lock:
            job = self._job
        if job:
            job["stop"].set()

    def _run(self, job):
        end = time.time() + WATCH_SECONDS
        while time.time() < end and not job["stop"].wait(POLL_SECONDS):
            if self._foreground() != job["hwnd"]:
                break  # user moved on; judge what we saw last
            text = self._read_field()
            if text is None:
                continue
            region = _region(text, job["before"], job["after"])
            if region is not None:  # ignore states where the box was cleared (message sent) or rewritten
                job["last"] = region
        with self._lock:
            if self._job is job:
                self._job = None
        if job["last"] is None:
            return
        try:
            for heard, fixed in find_corrections(job["inserted"], job["last"], self._known()):
                log.info("learned correction: %r -> %r", heard, fixed)
                self._on_learn(heard, fixed)
        except Exception:
            log.exception("learning from corrections failed")

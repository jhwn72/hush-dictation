"""Runs the cleanup model on tricky transcripts and flags words it added that the speaker never said."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hush import textproc
from hush.config import DEFAULTS
from hush.llm import LLM

CASES = [
    "I just tested with like filler words I think the main features that separate hush from normal dictation we haven't implemented those yet right",
    "so like the dashboard is kind of slow and I want it to like load faster",
    "it's like a really good idea honestly",
    "I was like no way that's not gonna work",
    "can you like make the button like bigger and like blue",
    "we have like three options basically",
    "um I feel like we should probably just ship it",
    "so yeah um basically I was gonna say, well, anyway the deck is due friday",
]
llm = LLM(dict(DEFAULTS))
words = lambda s: {w.lower().strip(".,!?;:'\"") for w in re.findall(r"[\w'’]+", s)}
bad = 0
for raw in CASES:
    out = llm.cleanup(textproc.remove_fillers(raw), "formal", "ai", "Claude", ["Hush"])
    added = words(out) - words(raw) - {"hush", "haven't"}
    flag = "  ADDED: " + ", ".join(sorted(added)) if added else ""
    bad += bool(added)
    print(f"- {raw}\n  -> {out}{flag}")
print(f"\n{len(CASES) - bad}/{len(CASES)} with no invented words")

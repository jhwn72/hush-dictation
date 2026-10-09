"""Context-aware cleanup cases: real past failures plus traps (over-correction, copying screen text)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hush import textproc
from hush.config import DEFAULTS
from hush.llm import LLM, format_context

VOCAB = ["Hush", "PIXELFORGE", "Zentrix"]
CASES = [
    ("sound-alike mishearing: 'share a device'",
     {"name": "Edge", "title": "Contact support - Streamly"}, {}, [],
     "Hi, my roommate and I shared advice, so he was still logged into my account when he bought the movie. Could you refund it?",
     ["a device"]),
    ("mishearing fixed from the chat on screen",
     {"name": "Claude", "title": "Claude"},
     {"nearby": "Still open: the 'like' to 'some' quirk, where cleanup turned 'like filler words' into 'some filler words'. Which of these do you want next?"},
     [], "And then fix the like sum quirk.", ["some quirk"]),
    ("continue a half-typed sentence",
     {"name": "Slack", "title": "dm - Sam - Slack"},
     {"before": "Hey Sam, I looked at the deck and I think we"}, [],
     "Should probably cut the pricing slide", ["should probably cut the pricing slide"]),
    ("name from the screen",
     {"name": "Outlook", "title": "Inbox - Outlook"},
     {"nearby": "From: Siobhan Gallagher  Subject: Thursday standup  Hi Alex, can we move standup to 10?"}, [],
     "tell shivawn I'll be there at ten works for me", ["Siobhan"]),
    ("trap: don't copy or answer the screen",
     {"name": "Chrome", "title": "Gmail"},
     {"nearby": "Hi Alex, quick question: can you send over the Q3 revenue breakdown and the churn numbers by Friday? Thanks, Priya"}, [],
     "sounds good I'll send it tomorrow", None),
    ("trap: unusual but correct word, nothing on screen",
     {"name": "Messages", "title": "Messages"}, {}, [],
     "I'm gonna bake a quiche for the potluck on saturday", ["gonna", "quiche", "potluck"]),
    ("previous dictation as context",
     {"name": "ChatGPT", "title": "ChatGPT"}, {}, ["Set up the Next.js app with Supabase auth."],
     "and then add row level security to the super base tables", ["Supabase"]),
]

settings = dict(DEFAULTS)
if "--cloud" in sys.argv:  # uses the API key saved in Hush's Settings
    settings["llm_provider"] = "cloud"
llm = LLM(settings)
llm.warm()
ok = 0
for name, app, ctx, recent, raw, must in CASES:
    block = format_context(ctx, app, recent)
    t0 = time.time()
    out = llm.cleanup(textproc.remove_fillers(raw), "formal", "other", app["name"], VOCAB, context=block)
    dt = time.time() - t0
    if must is None:  # trap: must stay short, no copied screen words
        good = len(out.split()) <= 10 and "revenue" not in out.lower() and "churn" not in out.lower()
    else:
        good = all(m.lower() in out.lower() for m in must)
    ok += good
    print(f"{'PASS' if good else 'FAIL'} {name} ({dt:.2f}s, {llm.last_backend})\n     {raw}\n  -> {out}\n")
print(f"{ok}/{len(CASES)} passed")

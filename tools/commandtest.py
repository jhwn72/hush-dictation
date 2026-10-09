"""Command mode cases: rewrite a selection, or write from scratch. --cloud uses the key saved in Hush's Settings."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hush.config import DEFAULTS
from hush.llm import LLM, format_context

EMAIL = ("Hi Alex, thanks for jumping on the call yesterday. Could you send over the revised pricing by Thursday? "
         "Also, are you free next Tuesday at 2pm to walk the team through it? Best, Priya")
CASES = [
    ("make this shorter",
     "So basically I wanted to reach out and let you know that we have decided, after a lot of back and forth, "
     "to push the launch of the new collection to next Friday instead of this Friday, because the photos aren't ready.",
     "", lambda o: len(o.split()) < 25 and "friday" in o.lower()),
    ("turn this into bullet points",
     "We need to restock the black hoodies, fix the broken link on the about page, and email the three wholesale "
     "buyers about the new minimums.", "", lambda o: o.count("\n") >= 2 and "hoodie" in o.lower()),
    ("make it more professional",
     "yo can u send me the invoice asap, the client is bugging me lol", "",
     lambda o: "lol" not in o.lower() and "invoice" in o.lower()),
    ("translate to spanish", "See you tomorrow at the warehouse.", "",
     lambda o: "mañana" in o.lower() or "manana" in o.lower()),
    ("write a reply saying thursday works but i'm busy tuesday, suggest wednesday instead", "",
     format_context({"nearby": EMAIL}, {"name": "Gmail", "title": "Re: pricing - Gmail"}, []),
     lambda o: "wednesday" in o.lower() and "thursday" in o.lower() and "priya" in o.lower()),
]

settings = dict(DEFAULTS)
if "--cloud" in sys.argv:
    settings["llm_provider"] = "cloud"
llm = LLM(settings)
ok = 0
for instruction, selected, ctx, check in CASES:
    t0 = time.time()
    out = llm.command(instruction, selected, ctx)
    good = bool(check(out))
    ok += good
    print(f"{'PASS' if good else 'FAIL'} \"{instruction}\" ({time.time() - t0:.2f}s, {llm.last_backend})\n{out}\n")
print(f"{ok}/{len(CASES)} passed")

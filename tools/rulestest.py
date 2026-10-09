"""App rules: does cleanup follow the user's per-app instructions? --cloud uses the key saved in Hush's Settings."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hush import textproc
from hush.config import DEFAULTS
from hush.llm import LLM

CASES = [
    ("Slack", ["Never end a message with a period."], "um yeah I can take a look at it after lunch",
     lambda o: not o.rstrip().endswith(".") and "lunch" in o.lower()),
    ("Gmail", ["Always end with the sign-off 'Best, Alex' on its own line."],
     "hi priya thanks for sending the pricing I'll review it tonight and get back to you tomorrow",
     lambda o: o.rstrip().endswith("Best, Alex") and "priya" in o.lower()),
    ("Cursor", ["Wrap code names like function, file and variable names in backticks."],
     "rename the get user function in auth dot py to fetch user",
     lambda o: o.count("`") >= 4),
    ("Messages", ["Write everything in lowercase."], "Hey are you coming to the game on Saturday",
     lambda o: o == o.lower() and "saturday" in o),
    ("Slack", [], "um yeah I can take a look at it after lunch",  # no rule: normal formal ending
     lambda o: o.rstrip().endswith(".")),
]

settings = dict(DEFAULTS)
if "--cloud" in sys.argv:
    settings["llm_provider"] = "cloud"
llm = LLM(settings)
ok = 0
for app, rules, raw, check in CASES:
    t0 = time.time()
    out = llm.cleanup(textproc.remove_fillers(raw), "formal", "other", app, [], rules=rules)
    if not rules:
        out = textproc.apply_style(out, "formal")
    good = bool(check(out))
    ok += good
    print(f"{'PASS' if good else 'FAIL'} {app} {rules or '(no rule)'} ({time.time() - t0:.2f}s, {llm.last_backend})\n  -> {out!r}")
print(f"\n{ok}/{len(CASES)} passed")

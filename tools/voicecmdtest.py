"""Voice command parsing: real commands are acted on, look-alike sentences are left alone."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hush.textproc import voice_commands

CASES = [
    # (what Whisper heard, expected text, expected actions)
    ("Scratch that.", "", {"undo"}),
    ("Delete that", "", {"undo"}),
    ("Sounds good, see you at 3. Press enter.", "Sounds good, see you at 3.", {"send"}),
    ("On my way, send it", "On my way", {"send"}),
    ("Can you review the PR? And then hit enter.", "Can you review the PR?", {"send"}),
    ("Let's do Tuesday. Actually, no, Wednesday works better. Scratch that.", "Let's do Tuesday.", set()),
    ("I'll be there at five, scratch that.", "", set()),
    ("Hi Sam, new line, thanks for the update. New paragraph. Best, Alex",
     "Hi Sam,\nthanks for the update.\n\nBest, Alex", set()),
    # look-alikes that must NOT trigger
    ("I'll press enter on the form later.", "I'll press enter on the form later.", set()),
    ("We're launching a new line of hoodies.", "We're launching a new line of hoodies.", set()),
    ("Did you send it to Priya yet?", "Did you send it to Priya yet?", set()),
    ("Don't scratch that table when you move it.", "Don't scratch that table when you move it.", set()),
    ("Can you send that over when you get a chance?", "Can you send that over when you get a chance?", set()),
]

ok = 0
for heard, want_text, want_actions in CASES:
    text, actions = voice_commands(heard)
    good = text == want_text and actions == want_actions
    ok += good
    print(f"{'PASS' if good else 'FAIL'} {heard!r}\n     -> {text!r} {sorted(actions)}"
          + ("" if good else f"   (wanted {want_text!r} {sorted(want_actions)})"))
print(f"\n{ok}/{len(CASES)} passed")

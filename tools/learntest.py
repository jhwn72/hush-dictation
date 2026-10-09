"""Cases for learning from corrections: real vocabulary fixes must be learned, everything else ignored."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hush.learn import _region, find_corrections

KNOWN = {"pixelforge", "hush"}
CASES = [
    # (description, typed, box afterwards, expected learned pairs)
    ("brand split into two words", "Can you check the zen tricks logs?", "Can you check the Zentrix logs?", [("zen tricks", "Zentrix")]),
    ("name misheard", "Tell Shivawn I'll be there at ten.", "Tell Siobhan I'll be there at ten.", [("Shivawn", "Siobhan")]),
    ("mid-sentence name", "I talked to shaun about it.", "I talked to Sean about it.", [("shaun", "Sean")]),
    ("acronym", "We need the s o p by Friday.", "We need the SOP by Friday.", [("s o p", "SOP")]),
    ("casing only", "Push it to vercel tonight.", "Push it to Vercel tonight.", [("vercel", "Vercel")]),
    ("already in dictionary", "Ship the pixel forge drop.", "Ship the PIXELFORGE drop.", []),
    ("rewording, not spelling", "That's a big problem.", "That's a huge problem.", []),
    ("different name entirely", "Send it to the client today.", "Send it to Acme today.", []),
    ("plain lowercase fix", "I think their right.", "I think they're right.", []),
    ("user kept typing after", "Sounds good.", "Sounds good. See you at 3 with Priya.", []),
    ("big rewrite", "Can we maybe push the launch.", "Let's delay launch until the Q4 planning is done.", []),
    ("sentence-start ordinary word", "Their going to love it.", "They're going to love it.", []),
]

ok = 0
for desc, typed, after, want in CASES:
    got = find_corrections(typed, after, KNOWN)
    good = got == want
    ok += good
    print(f"{'PASS' if good else 'FAIL'} {desc}: {got}" + ("" if good else f"   (wanted {want})"))

# region finding: the typed text sits between existing text; user edits only inside it
box = "Hey team, quick update. Can you check the Zentrix logs? Thanks!"
r = _region(box, before="Hey team, quick update. ", after=" Thanks!")
print("PASS region" if r == "Can you check the Zentrix logs?" else f"FAIL region: {r!r}")
print("PASS region gone" if _region("", "Hey team, quick update. ", " Thanks!") is None else "FAIL region gone")
print(f"\n{ok}/{len(CASES)} correction cases passed")

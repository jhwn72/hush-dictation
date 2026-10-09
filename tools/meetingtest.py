"""Me/Them merging: interleaving by time, joining turns, and dropping speaker echo picked up by the mic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hush.meeting import format_transcript, merge_speakers

me = [(0.5, 3.0, "Can we push the launch to Friday?"),
      (3.2, 5.0, "I think that gives us enough time."),
      (6.4, 9.8, "Friday works, but the photos need"),       # echo of Them through the speakers
      (12.0, 14.0, "Okay, I'll ping the photographer today.")]
them = [(6.0, 10.5, "Friday works, but the photos need to be done by Wednesday."),
        (10.6, 11.8, "And send me the shot list.")]

turns = merge_speakers(me, them)
out = format_transcript(turns)
print(out, "\n")
expect = ["Me: Can we push the launch to Friday? I think that gives us enough time.",
          "Them: Friday works, but the photos need to be done by Wednesday. And send me the shot list.",
          "Me: Okay, I'll ping the photographer today."]
print("PASS" if out.split("\n\n") == expect else "FAIL\nwanted:\n" +"\n\n".join(expect))

# headphones: nothing to dedupe, a genuine overlap with different words stays
me2 = [(6.5, 8.0, "Sorry, go ahead.")]
print("PASS overlap kept" if any(l == "Me" for l, _, _ in merge_speakers(me2, them)) else "FAIL overlap dropped")

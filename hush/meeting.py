"""Merge the mic transcript (Me) and the system-audio transcript (Them) into one labeled conversation.

On speakers instead of headphones the mic also hears the other side; those echo segments are recognised
(overlapping in time with a Them segment and mostly the same words) and dropped from Me.
"""
import difflib
import re

ECHO_SIMILARITY = 0.5
WORD = re.compile(r"[\w']+")


def _words(t):
    return [w.lower() for w in WORD.findall(t)]


def _is_echo(me_seg, them_segs):
    s, e, text = me_seg
    mine = _words(text)
    if not mine:
        return True
    for ts, te, ttext in them_segs:
        overlap = min(e, te + 1.0) - max(s, ts - 1.0)  # a little slack: the echo arrives a beat later
        if overlap <= 0:
            continue
        theirs = _words(ttext)
        # the echo is usually a fragment of what they said, so compare against their segment as a whole
        ratio = difflib.SequenceMatcher(a=mine, b=theirs, autojunk=False).find_longest_match(
            0, len(mine), 0, len(theirs)).size / len(mine)
        if ratio >= ECHO_SIMILARITY:
            return True
    return False


def merge_speakers(me, them, me_label="Me", them_label="Them"):
    """me/them: [(start, end, text)]. Returns [(label, start, text)] in time order, consecutive turns joined."""
    me = [m for m in me if not _is_echo(m, them)]
    events = sorted([(s, e, me_label, t) for s, e, t in me] + [(s, e, them_label, t) for s, e, t in them])
    turns = []
    for s, e, label, text in events:
        if turns and turns[-1][0] == label and s - turns[-1][3] < 4.0:
            turns[-1][2] += " " + text
            turns[-1][3] = e
        else:
            turns.append([label, s, text, e])
    return [(label, start, text) for label, start, text, _ in turns]


def format_transcript(turns) -> str:
    return "\n\n".join(f"{label}: {text}" for label, _, text in turns)

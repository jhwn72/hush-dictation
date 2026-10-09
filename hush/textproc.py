"""Deterministic text steps: dictionary, snippets, filler removal, style rules, fix counting."""
import difflib
import re

FILLERS = re.compile(r"\b(?:um+|uh+|erm+|uhm+|hmm+|mm+)\b[,.]?\s*", re.IGNORECASE)
WORD = re.compile(r"[\w'’@.-]+")

# Whisper's favourite hallucinations on silence / noise
HALLUCINATIONS = {
    "thank you", "thank you.", "thanks for watching!", "thanks for watching.", "you", "you.",
    "bye.", "bye", "thank you so much.", ".", "okay.", "so",
}


def count_words(text: str) -> int:
    return len(WORD.findall(text))


# ---- voice commands (deterministic: instant, free, same on every brain) ----
_TAIL = r"[\s,.!?;:]*$"
_SEND = re.compile(r"(?:^|(?<=[\s,.!?;:]))\s*(?:(?:and|then|and then|okay|ok)\s+)?"
                   r"(?:press enter|hit enter|press return|hit return|send it|send that|send this|hit send|press send)" + _TAIL,
                   re.IGNORECASE)
_SCRATCH = r"(?:scratch that|delete that|undo that|strike that)"
_SCRATCH_ALL = re.compile(r"^\W*" + _SCRATCH + _TAIL, re.IGNORECASE)
_SCRATCH_END = re.compile(r"(?:^|(?<=[\s,.!?;:]))\s*(?:(?:actually|no|wait|okay|ok)[\s,]+)?" + _SCRATCH + _TAIL, re.IGNORECASE)
# "new line" only when it starts a clause (after punctuation or at the start), so "a new line of products" is safe
_NEWPARA = re.compile(r"(^|[,.!?;:]\s*)new paragraph(?=[\s,.!?;:]*(?:$|\w))[,.]?\s*", re.IGNORECASE)
_NEWLINE = re.compile(r"(^|[,.!?;:]\s*)new line(?=[\s,.!?;:]*(?:$|\w))[,.]?\s*", re.IGNORECASE)


def voice_commands(text: str) -> tuple[str, set[str]]:
    """Strip spoken commands from a transcript. Returns (text to type, actions) where actions can contain
    "undo" (scratch the previous dictation), "send" (press Enter after pasting)."""
    actions = set()
    t = text.strip()
    if _SCRATCH_ALL.match(t):
        return "", {"undo"}
    m = _SEND.search(t)
    if m:
        actions.add("send")
        t = t[:m.start()].rstrip(" ,;:")
    m = _SCRATCH_END.search(t)
    if m:
        # "..., see you Friday. Actually, scratch that" -> drop the sentence just before the command
        head = t[:m.start()].rstrip(" ,;:")
        sentences = re.split(r"(?<=[.!?])\s+", head)
        t = " ".join(sentences[:-1]).strip()
    t = _NEWPARA.sub(lambda m: m.group(1).rstrip() + "\n\n", t)
    t = _NEWLINE.sub(lambda m: m.group(1).rstrip() + "\n", t)
    t = re.sub(r"[ \t]+\n", "\n", t).strip(" ")
    return t, actions


STUTTER = re.compile(r"\b(\w+)(?:[,.]?\s+\1\b)+", re.IGNORECASE)


def remove_fillers(text: str) -> str:
    """Deterministic pass that always runs: um/uh/erm, stutters ("I I I"), and the punctuation they leave behind."""
    out = FILLERS.sub("", text)
    out = STUTTER.sub(r"\1", out)
    out = re.sub(r"\s+([,.!?])", r"\1", out)          # space before punctuation
    out = re.sub(r"([,.!?])(?:\s*,)+", r"\1", out)     # ", ," and ". ,"
    out = re.sub(r"^[\s,.]+", "", out)                 # leading comma/dots
    out = re.sub(r",\s*(\.{2,}|…)\s*$", ".", out)      # "So, I, ..." trailing off
    out = re.sub(r"(\.{3}|…)\s*$", ".", out)           # trailing ellipsis from hesitation
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out[:1].upper() + out[1:] if out else out


def _phrase_re(phrase: str) -> re.Pattern:
    # Word boundaries that also work for phrases starting/ending with symbols (emails, C++)
    return re.compile(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", re.IGNORECASE)


def apply_dictionary(text: str, entries: list[dict]) -> tuple[str, int]:
    """Replacement entries (btw -> by the way) and canonical spellings (pixelforge -> PIXELFORGE)."""
    fixes = 0
    for e in sorted(entries, key=lambda e: -len(e["phrase"])):
        phrase = e["phrase"].strip()
        if not phrase:
            continue
        target = (e.get("replacement") or phrase).strip()
        pat = _phrase_re(phrase)

        def sub(m, target=target):
            nonlocal fixes
            if m.group(0) != target:
                fixes += 1
            return target

        text = pat.sub(sub, text)
        # Split-up variants Whisper produces for joined words ("Pixel Forge" for PIXELFORGE)
        if " " not in phrase and len(phrase) >= 6 and not e.get("replacement"):
            spaced = re.compile(r"(?<![\w])" + r"[\s-]?".join(map(re.escape, phrase)) + r"(?![\w])", re.IGNORECASE)

            def sub2(m, target=target):
                nonlocal fixes
                if m.group(0) != target:
                    fixes += 1
                return target

            text = spaced.sub(sub2, text)
    return text, fixes


def apply_snippets(text: str, snippets: list[dict]) -> tuple[str, int]:
    hits = 0
    stripped = text.strip().rstrip(".!?,").strip()
    for s in sorted(snippets, key=lambda s: -len(s["trigger"])):
        trig = s["trigger"].strip()
        if not trig:
            continue
        if stripped.lower() == trig.lower():
            return s["expansion"], 1
        pat = re.compile(r"(?<![\w])" + re.escape(trig) + r"(?![\w])[.]?", re.IGNORECASE)
        text, n = pat.subn(lambda m, e=s["expansion"]: e, text)
        hits += n
    return text, hits


_GREETING_COMMA = re.compile(r"^(hey|hi|hello|yo|yeah|yep|ok|okay|so|well|oh|alright)(\s+\w+)?,\s", re.IGNORECASE)


def apply_style(text: str, style: str, continuing: bool = False) -> str:
    text = text.strip()
    if not text or "\n" in text and style == "formal":
        return text
    if continuing and style != "very_casual":
        # mid-sentence: casing is decided by fit_to_cursor, only fix the ending
        if style == "formal" and text[-1].isalnum():
            text += "."
        if style == "casual" and text.endswith(".") and not text.endswith(".."):
            text = text[:-1]
        return text
    if style == "formal":
        text = text[0].upper() + text[1:]
        if text[-1].isalnum():
            text += "."
        return text
    # casual / very_casual: light punctuation, no trailing period
    if "\n" not in text:
        text = _GREETING_COMMA.sub(lambda m: m.group(0)[:-2] + " ", text)
        if text.endswith(".") and not text.endswith(".."):
            text = text[:-1]
    if style == "very_casual":
        # Lowercase sentence starts (and a lone "I'm"-style start), keep names and acronyms elsewhere
        text = re.sub(r"(^|[.!?]\s+|\n)([A-Z])(?=[a-z'’\s])", lambda m: m.group(1) + m.group(2).lower(), text)
    else:
        text = text[0].upper() + text[1:]
    return text


_SENTENCE_END = re.compile(r"([.!?:;]|[.!?][\"')\]]|\n)\s*$")
_KEEP_CAPS = {"I", "I'm", "I'll", "I've", "I'd", "I’m", "I’ll", "I’ve", "I’d"}
# Words safe to lowercase when continuing a sentence. Anything else (likely a name) keeps its capital.
_COMMON_STARTS = set("""
a an the and but or so then also just maybe probably actually really still even only now yet because if when while
that this these those there here it its it's we we're we'll they they're you you're your he she his her our my me
us them should would could can can't will won't might must need want wanna gonna have has had do does did don't
doesn't didn't is are was were be been being am not no yes yeah yep ok okay sure fine well like as to for from with
without about after before since until into onto over under on in at by of up down out off all any some every each
more most less much many few other another such what which who whom whose why how where let let's make get got go
going see look think know feel try use put take give keep tell ask say said thanks thank please sorry hey hi hello
right left too very quite pretty kind sort lot lots one two three first second next last
""".split())


def continues_sentence(before: str) -> bool:
    """True when the text before the cursor stops mid-sentence ("I think we|")."""
    b = before.rstrip(" \t")
    return bool(b.strip()) and not _SENTENCE_END.search(b)


def fit_to_cursor(text: str, before: str, after: str, vocab: list[str]) -> str:
    """Make inserted text sit naturally where the cursor is: spacing, and casing when mid-sentence."""
    if not text:
        return text
    if continues_sentence(before):
        first = text.split(maxsplit=1)[0]
        bare = first.strip(".,!?;:\"'")
        common = bare.lower() in _COMMON_STARTS and bare not in _KEEP_CAPS
        in_vocab = any(v.split()[0] == bare for v in vocab if v)
        if common and not in_vocab and bare[:1].isupper() and bare[1:].islower():
            text = text[0].lower() + text[1:]
    if before and not before[-1].isspace() and before[-1] not in "([{\"'“‘/@#" and text[0] not in ".,!?;:)]}":
        text = " " + text
    if after and after[0].isalnum():
        if after[0].islower() and text.endswith("."):
            text = text[:-1]  # we're inserting into the middle of a sentence
        text += " "
    return text


def diff_fixes(raw: str, final: str) -> int:
    """How many words of the raw transcript were changed or dropped."""
    norm = lambda s: [w.lower().strip(".,!?;:'\"") for w in WORD.findall(s)]
    a, b = norm(raw), norm(final)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    changed = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("replace", "delete"):
            changed += i2 - i1
    return changed


def looks_like_hallucination(text: str, duration: float) -> bool:
    t = text.strip().lower()
    return not t or (t in HALLUCINATIONS and duration < 2.5)

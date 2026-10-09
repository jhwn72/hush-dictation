"""Static check for PyObjC subclasses (runs on Windows too): every method of an NSObject/NSView/... subclass becomes
an Objective-C selector, so the number of underscores (after any leading ones) must equal its argument count,
unless it's marked @objc.python_method. This is the mistake that crashed the first Mac pill ('_px')."""
import ast
import sys
from pathlib import Path

FILES = sys.argv[1:] or [str(p) for p in (Path(__file__).resolve().parent.parent / "hush").glob("*_mac.py")]
OBJC_BASES = {"NSObject", "NSView", "NSPanel", "NSWindow", "NSImageView", "NSWindowController"}
bad = 0
for f in FILES:
    tree = ast.parse(Path(f).read_text(encoding="utf-8"))
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        if not any(isinstance(b, ast.Name) and b.id in OBJC_BASES for b in cls.bases):
            continue
        for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
            decos = {ast.unparse(d) for d in fn.decorator_list}
            if "objc.python_method" in decos or (fn.name.startswith("__") and fn.name.endswith("__")):
                continue
            want = fn.name.lstrip("_").count("_")
            have = len(fn.args.args) - 1  # minus self
            if want != have:
                bad += 1
                print(f"{f}:{fn.lineno}: {cls.name}.{fn.name} takes {have} args but its selector expects {want}"
                      " (rename it, or add @objc.python_method)")
print("objc lint:", "OK" if not bad else f"{bad} problem(s)")
sys.exit(1 if bad else 0)

"""Renders the Hush app icon (silver tile + sound-wave mark, like the sidebar logo) to ui/icon.png and ui/icon.ico."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

UI = Path(__file__).resolve().parent.parent / "ui"
S = 1024
SS = 2  # supersample
N = S * SS

# macOS-style rounded square with a soft shadow, inset so the shadow fits
inset, radius = int(N * 0.09), int(N * 0.2)
box = (inset, inset, N - inset, N - inset)
mask = Image.new("L", (N, N), 0)
ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)

# silver gradient, top-left bright to bottom-right darker (the .mark gradient in app.css)
yy, xx = np.mgrid[0:N, 0:N] / N
t = np.clip((xx * 0.35 + yy * 0.9) / 1.1, 0, 1)[..., None]
top, bot = np.array([237, 237, 240]), np.array([122, 122, 132])
tile = (top + (bot - top) * t).astype(np.uint8)
img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
shadow = mask.filter(ImageFilter.GaussianBlur(N * 0.025)).point(lambda v: int(v * 0.45))
sh = Image.new("RGBA", (N, N), (0, 0, 0, 255))
sh.putalpha(shadow)
img.alpha_composite(sh, dest=(0, int(N * 0.012)))
body = Image.fromarray(np.dstack([tile, np.asarray(mask)]), "RGBA")
img.alpha_composite(body)

# inner highlight along the top edge
hl = Image.new("L", (N, N), 0)
ImageDraw.Draw(hl).rounded_rectangle((box[0] + 6, box[1] + 6, box[2] - 6, box[3] - 6), radius=radius - 6,
                                     outline=255, width=int(N * 0.006))
hl = Image.composite(hl, Image.new("L", (N, N), 0), mask).point(lambda v: int(v * 0.55))
glow = Image.new("RGBA", (N, N), (255, 255, 255, 0))
glow.putalpha(hl)
img.alpha_composite(glow)

# the sound-wave mark: five rounded bars, near-black
d = ImageDraw.Draw(img)
heights = [0.18, 0.42, 0.26, 0.56, 0.18]
bar_w, gap = N * 0.062, N * 0.058
total = len(heights) * bar_w + (len(heights) - 1) * gap
x = N / 2 - total / 2
for h in heights:
    hh = N * h
    d.rounded_rectangle((x, N / 2 - hh / 2, x + bar_w, N / 2 + hh / 2), radius=bar_w / 2, fill=(14, 14, 18, 255))
    x += bar_w + gap

icon = img.resize((S, S), Image.LANCZOS)
icon.save(UI / "icon.png")
icon.save(UI / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", UI / "icon.png", "and icon.ico")

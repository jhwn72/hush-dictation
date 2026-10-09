"""Platform-neutral recording pill: state, animation and drawing (Pillow). A host subclass for each OS
(pill_win.Pill, pill_mac.Pill) owns the transparent window, the animation timer, mouse input and getting each
rendered frame on screen.

States: idle (tiny bar; hover expands into mic + notetaker buttons with a tooltip), recording (waveform),
command (sparkle + waveform), locked (hands-free: cancel, waveform, confirm), processing (dot shimmer),
msg (dot + text), hint (tap warning).
"""
import logging
import math
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger("flow.pill")

# ---- design tokens (same as ui/app.css) ----
FG = (244, 244, 246)
MUTED = (140, 140, 150)
SILVER = (200, 201, 208)
BAD = (255, 97, 102)
GOOD = (74, 222, 128)
WARN = (246, 184, 75)
BODY_TOP, BODY_BOT = (27, 27, 33), (9, 9, 12)

BARS = 13
SS = 2  # supersampling for anti-aliased edges


def _ease(cur, target, dt, speed):
    return cur + (target - cur) * (1 - math.exp(-dt * speed))

# font files per OS, first one that exists wins
FONT_REG, FONT_SEMI, FONT_MONO = "reg", "semi", "mono"
FONT_FILES = {
    "win32": {"reg": ["C:/Windows/Fonts/segoeui.ttf"], "semi": ["C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/segoeui.ttf"],
              "mono": ["C:/Windows/Fonts/consola.ttf"]},
    "darwin": {"reg": ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc"],
               "semi": ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc"],
               "mono": ["/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc"]},
}.get(sys.platform, {"reg": [], "semi": [], "mono": []})


class PillCore:
    def __init__(self, on_mic, on_cancel, on_confirm, on_note):
        self._cb = {"mic": on_mic, "cancel": on_cancel, "confirm": on_confirm, "note": on_note}
        self.state, self.text, self.kind = "idle", "", ""
        self.note_on = False
        self.enabled = True
        self.hotkey = ["Ctrl", "Win"]
        self._hist = deque([0.0] * BARS, maxlen=BARS)
        self._disp = [0.0] * BARS
        self._cur = [38.0, 8.0]
        self._content = 1.0
        self._tip_a = 0.0
        self._hint_a = 0.0
        self._menu = False
        self._hover = None
        self._left_at = 0.0
        self._hint_until = 0.0
        self._t_last = time.time()
        self._t0 = time.time()
        self._regions = []
        self._bbox = (0, 0, 1, 1)
        self._scale = 1.0
        self._anchor = (0, 0)  # bottom-centre of the pill, in screen pixels from the top-left of the screen
        self._fonts = {}

    # ---------------- host hooks (implemented per OS) ----------------
    def _wake(self):
        """Make sure the animation timer runs (called from any thread)."""
        raise NotImplementedError

    def _stop_timer(self):
        raise NotImplementedError

    def _hide(self):
        raise NotImplementedError

    def _refresh_monitor(self):
        """Set self._scale and self._anchor for the screen the user is working on."""
        raise NotImplementedError

    def _blit(self, im, x, y):
        """Show the rendered RGBA frame with its top-left at screen pixel (x, y)."""
        raise NotImplementedError

    # ---------------- mouse (hosts forward events here, in window pixels from the top-left) ----------------
    def _mouse_move(self, px, py):
        hit = self._hit(px, py)
        if hit != self._hover:
            self._hover = hit
            if hit and self.state == "idle":
                self._menu = True
            self._wake()

    def _mouse_leave(self):
        self._hover = None
        self._left_at = time.time()
        self._wake()

    def _mouse_up(self, px, py):
        hit = self._hit(px, py)
        if hit == "close":
            self.set_state("idle")
        elif hit in self._cb:
            if hit == "mic":
                self._menu = False
            threading.Thread(target=self._cb[hit], daemon=True).start()

    def _clickable(self):
        return self._hover in ("mic", "note", "cancel", "confirm", "close")

    # ---------------- public (any thread) ----------------
    def set_state(self, state, text="", kind=""):
        if state == "hint":
            self._hint_until = time.time() + 4.5
        if state != self.state:
            self._content = 0.0
            if state != "idle" and self.state in ("idle", "hint"):
                self._refresh_monitor()
        self.state, self.text, self.kind = state, text, kind
        self._wake()

    def set_level(self, lvl):
        self._hist.append(float(lvl))

    def set_note(self, on):
        self.note_on = bool(on)
        self._wake()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self._wake()

    def set_hotkey(self, labels):
        self.hotkey = list(labels)

    def _hit(self, px, py):
        bx0, by0 = self._bbox[0], self._bbox[1]
        x, y = px / self._scale + bx0, py / self._scale + by0
        for name, (x0, y0, x1, y1) in self._regions:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    # ---------------- animation ----------------
    def _target(self):
        st = self.state
        if st in ("idle", "hint"):
            if self._menu and st == "idle":
                return 94.0, 34.0
            return (46.0, 14.0) if self.note_on else (38.0, 8.0)
        if st in ("recording", "processing"):
            return 74.0, 28.0
        if st == "command":
            return 96.0, 28.0
        if st == "locked":
            return 128.0, 34.0
        if st == "msg":
            return self._text_w(self.text, FONT_REG, 12.5) + 44, 30.0
        return 38.0, 8.0

    def _tick(self):
        now = time.time()
        dt = min(0.05, now - self._t_last)
        self._t_last = now
        if self.state == "hint" and now > self._hint_until:
            self.state = "idle"
        if self._menu and not self._hover and now - self._left_at > 0.35:
            self._menu = False
        if self._menu and self.state != "idle":
            self._menu = False

        tw, th = self._target()
        self._cur[0] = _ease(self._cur[0], tw, dt, 22)
        self._cur[1] = _ease(self._cur[1], th, dt, 22)
        near = abs(self._cur[0] - tw) < max(2.0, tw * 0.12) and abs(self._cur[1] - th) < 2.0
        self._content = min(1.0, self._content + dt * 7) if near else self._content
        tip_t = 1.0 if (self._menu and self._hover in ("mic", "note")) else 0.0
        self._tip_a = _ease(self._tip_a, tip_t, dt, 20)
        self._hint_a = _ease(self._hint_a, 1.0 if self.state == "hint" else 0.0, dt, 16)

        # waveform: each bar follows recent level history, centre-weighted like a voice envelope
        mid = (BARS - 1) / 2
        hist = list(self._hist)
        for i in range(BARS):
            shape = 1 - (abs(i - mid) / (mid + 1.5)) ** 1.5
            lvl = hist[(i * 5) % BARS] * 0.45 + hist[-1] * 0.55
            wob = 0.06 * math.sin(now * 9 + i * 1.7) if lvl > 0.04 else 0
            target = max(0.0, min(1.0, lvl * 1.5 * shape + wob))
            self._disp[i] = _ease(self._disp[i], target, dt, 28)

        if self.enabled:
            self._render()
        else:
            self._hide()

        moving = (abs(self._cur[0] - tw) > 0.2 or abs(self._cur[1] - th) > 0.2 or self._content < 1
                  or abs(self._tip_a - tip_t) > 0.01 or abs(self._hint_a - (1.0 if self.state == "hint" else 0.0)) > 0.01)
        live = self.state in ("recording", "locked", "processing", "hint", "command") or self._menu or self.note_on
        if not moving and not live:
            self._stop_timer()

    # ---------------- drawing ----------------
    def _font(self, name, size_logical, k):
        px = max(6, int(round(size_logical * k)))
        key = (name, px)
        if key not in self._fonts:
            try:
                path = next((p for p in FONT_FILES.get(name, []) if Path(p).exists()), None)
                self._fonts[key] = ImageFont.truetype(path, px) if path else ImageFont.load_default(px)
            except OSError:
                self._fonts[key] = ImageFont.load_default()
        return self._fonts[key]

    def _text_w(self, text, font, size):
        f = self._font(font, size, 4)
        return f.getlength(text or "") / 4

    def _render(self):
        sc = self._scale
        k = sc * SS
        cw, ch = self._cur
        pill = (-cw / 2, -ch, cw / 2, 0.0)
        rects = [pill]
        regions = []

        tip = None
        if self._tip_a > 0.02:
            if self._hover == "note" or (self._hover != "mic" and getattr(self, "_last_tip", "mic") == "note"):
                label, keys, self._last_tip = ("Stop notetaker" if self.note_on else "Start notetaker"), [], "note"
            else:
                label, keys, self._last_tip = "Dictate", self.hotkey, "mic"
            tw = self._text_w(label, FONT_REG, 12.5) + sum(self._text_w(kk, FONT_MONO, 10.5) + 14 for kk in keys) + 4 * len(keys) + 28
            tip = (-tw / 2, -ch - 10 - 30, tw / 2, -ch - 10, label, keys)
            rects.append(tip[:4])

        hint = None
        if self._hint_a > 0.02:
            combo = " + ".join(self.hotkey)
            title, sub = f"Don't tap. Hold down {combo}.", "Hold while you speak, or double-tap for hands-free."
            hw = max(self._text_w(title, FONT_SEMI, 13), self._text_w(sub, FONT_REG, 12)) + 92
            hint = (-hw / 2, -ch - 12 - 62, hw / 2, -ch - 12, title, sub)
            rects.append(hint[:4])

        idle_small = self.state in ("idle", "hint") and not self._menu
        catcher = (-cw / 2 - 14, -ch - 9, cw / 2 + 14, 4.0) if idle_small else None
        if catcher:
            rects.append(catcher)

        pad = 16
        bx0 = min(r[0] for r in rects) - pad
        by0 = min(r[1] for r in rects) - pad
        bx1 = max(r[2] for r in rects) + pad
        by1 = max(r[3] for r in rects) + pad / 2
        self._bbox = (bx0, by0, bx1, by1)
        dev_w, dev_h = max(1, math.ceil((bx1 - bx0) * sc)), max(1, math.ceil((by1 - by0) * sc))
        img = Image.new("RGBA", (dev_w * SS, dev_h * SS), (0, 0, 0, 0))

        def P(x, y):
            return ((x - bx0) * k, (y - by0) * k)

        def R(r):
            (a, b), (c, d) = P(r[0], r[1]), P(r[2], r[3])
            return (a, b, c, d)

        if catcher:  # near-invisible halo so the tiny idle bar is easy to hover (alpha 0 would be click-through)
            ImageDraw.Draw(img).rounded_rectangle(R(catcher), radius=12 * k, fill=(0, 0, 0, 2))
            regions.append(("pill", catcher))

        self._panel(img, R(pill), (ch / 2) * k, k, strong=not idle_small)
        content = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(content)
        cy = -ch / 2
        st = self.state

        if idle_small and self.note_on:
            pulse = 0.55 + 0.45 * math.sin((time.time() - self._t0) * 4)
            self._dot(content, d, P(0, cy), 2.8 * k, BAD, glow=pulse)
        elif st == "idle" and self._menu:
            mic_r = (-cw / 2 + 4, -ch + 4, cw / 2 - 38, -4)
            note_c = (cw / 2 - 21, cy)
            regions += [("mic", mic_r), ("note", (note_c[0] - 15, -ch, note_c[0] + 15, 0))]
            if self._hover == "mic":
                d.rounded_rectangle(R(mic_r), radius=13 * k, fill=(255, 255, 255, 28))
            if self._hover == "note":
                x, y = P(*note_c)
                d.ellipse((x - 13 * k, y - 13 * k, x + 13 * k, y + 13 * k), fill=(255, 255, 255, 28))
            self._mic_icon(d, P((mic_r[0] + mic_r[2]) / 2, cy), k)
            self._note_icon(d, P(*note_c), k)
        elif st in ("recording", "locked"):
            if st == "locked":
                cx_c, cx_ok = -cw / 2 + 18, cw / 2 - 18
                regions += [("cancel", (cx_c - 14, -ch, cx_c + 14, 0)), ("confirm", (cx_ok - 14, -ch, cx_ok + 14, 0))]
                x, y = P(cx_c, cy)
                r = 12 * k
                d.ellipse((x - r, y - r, x + r, y + r), fill=(84, 82, 88, 255) if self._hover == "cancel" else (62, 61, 66, 255))
                self._x_icon(d, (x, y), 3.6 * k, 1.5 * k, FG)
                x, y = P(cx_ok, cy)
                self._silver_disc(content, (x, y), r, bright=self._hover == "confirm")
                d.line([(x - 4.4 * k, y + 0.2 * k), (x - 1.3 * k, y + 3.2 * k), (x + 4.6 * k, y - 3.4 * k)],
                       fill=(10, 10, 13, 255), width=max(1, round(1.8 * k)), joint="curve")
                span = cw - 70
            else:
                span = cw - 22
            self._bars(content, d, P, cy, span, ch)
        elif st == "command":
            # ✦ marks "speaking an instruction"; the waveform sits to its right
            self._spark_icon(content, P(-cw / 2 + 17, cy), k)
            self._bars(content, d, P, cy, cw - 50, ch, x_offset=10)
        elif st == "processing":
            n = 9
            gap = 5.2
            phase = ((time.time() - self._t0) * 9) % (n + 6) - 3
            for i in range(n):
                x = (i - (n - 1) / 2) * gap
                glow = math.exp(-((i - phase) ** 2) / 2.2)
                a = int(70 + 185 * glow)
                px, py = P(x, cy)
                r = (1.25 + 0.5 * glow) * k
                d.ellipse((px - r, py - r, px + r, py + r), fill=(*FG, a))
        elif st == "msg":
            color = {"bad": BAD, "good": GOOD}.get(self.kind, SILVER)
            self._dot(content, d, P(-cw / 2 + 16, cy), 3 * k, color, glow=0.8 if self.kind else 0)
            f = self._font(FONT_REG, 12.5, k)
            d.text(P(-cw / 2 + 27, cy), self.text, font=f, fill=(*FG, 255), anchor="lm")

        if self._content < 1:
            content.putalpha(content.getchannel("A").point(lambda v, a=self._content: int(v * a)))
        img.alpha_composite(content)

        if tip:
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            self._panel(layer, R(tip[:4]), 15 * k, k, strong=True)
            dl = ImageDraw.Draw(layer)
            x = tip[0] + 14
            ty = (tip[1] + tip[3]) / 2
            dl.text(P(x, ty), tip[4], font=self._font(FONT_REG, 12.5, k), fill=(*FG, 255), anchor="lm")
            x += self._text_w(tip[4], FONT_REG, 12.5) + 8
            for kk in tip[5]:
                w = self._text_w(kk, FONT_MONO, 10.5) + 12
                dl.rounded_rectangle(R((x, ty - 9.5, x + w, ty + 9.5)), radius=5 * k, fill=(255, 255, 255, 22),
                                     outline=(255, 255, 255, 45), width=max(1, round(0.8 * k)))
                dl.text(P(x + w / 2, ty), kk, font=self._font(FONT_MONO, 10.5, k), fill=(*FG, 255), anchor="mm")
                x += w + 4
            layer.putalpha(layer.getchannel("A").point(lambda v, a=self._tip_a: int(v * a)))
            img.alpha_composite(layer)

        if hint:
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            self._panel(layer, R(hint[:4]), 16 * k, k, strong=True)
            dl = ImageDraw.Draw(layer)
            x0, y0, x1, y1 = hint[:4]
            self._warn_icon(dl, P(x0 + 22, y0 + 22), k)
            dl.text(P(x0 + 40, y0 + 22), hint[4], font=self._font(FONT_SEMI, 13, k), fill=(*FG, 255), anchor="lm")
            dl.text(P(x0 + 40, y0 + 42), hint[5], font=self._font(FONT_REG, 12, k), fill=(*MUTED, 255), anchor="lm")
            cx, cyy = x1 - 22, y0 + 22
            px, py = P(cx, cyy)
            r = 11 * k
            dl.ellipse((px - r, py - r, px + r, py + r), fill=(255, 255, 255, 40 if self._hover == "close" else 18),
                       outline=(255, 255, 255, 50), width=max(1, round(0.8 * k)))
            self._x_icon(dl, (px, py), 3.3 * k, 1.4 * k, FG)
            regions.append(("close", (cx - 13, cyy - 13, cx + 13, cyy + 13)))
            layer.putalpha(layer.getchannel("A").point(lambda v, a=self._hint_a: int(v * a)))
            img.alpha_composite(layer)

        self._regions = regions
        out = img.resize((dev_w, dev_h), Image.LANCZOS)
        ax, ay = self._anchor
        self._blit(out, int(round(ax + bx0 * sc)), int(round(ay + by0 * sc)))

    def _panel(self, img, rect, radius, k, strong=True):
        """Glass capsule: soft drop shadow, dark vertical gradient, hairline border, bright top highlight."""
        w, h = img.size
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(rect, radius=radius, fill=255)
        if strong:
            sh = mask.filter(ImageFilter.GaussianBlur(7 * k)).point(lambda v: int(v * 0.6))
            shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            shadow.putalpha(sh)
            img.alpha_composite(shadow, dest=(0, int(3 * k)) if 3 * k < h else (0, 0))
        y0, y1 = int(rect[1]), max(int(rect[1]) + 1, int(rect[3]))
        t = np.clip((np.arange(h) - y0) / (y1 - y0), 0, 1)[:, None]
        grad = np.empty((h, w, 4), np.uint8)
        for c in range(3):
            grad[..., c] = (BODY_TOP[c] + (BODY_BOT[c] - BODY_TOP[c]) * t).astype(np.uint8)
        grad[..., 3] = (np.asarray(mask, np.float32) * 0.97).astype(np.uint8)
        img.alpha_composite(Image.fromarray(grad, "RGBA"))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(rect, radius=radius, outline=(255, 255, 255, 34 if strong else 60), width=max(1, round(0.8 * k)))
        # highlight along the top edge, brightest in the middle (the dashboard cards' ::after line)
        x0, x1 = rect[0] + radius * 0.6, rect[2] - radius * 0.6
        if x1 - x0 > 4:
            hl = Image.new("L", (w, h), 0)
            ImageDraw.Draw(hl).line([(x0, rect[1] + 0.6 * k), (x1, rect[1] + 0.6 * k)], fill=255, width=max(1, round(0.9 * k)))
            xs = np.clip((np.arange(w) - x0) / max(1, x1 - x0), 0, 1)
            fade = (np.sin(np.pi * xs) ** 1.5 * (110 if strong else 70))[None, :]
            a = (np.asarray(hl, np.float32) / 255 * fade).astype(np.uint8)
            line = np.zeros((h, w, 4), np.uint8)
            line[..., :3] = 255
            line[..., 3] = a
            img.alpha_composite(Image.fromarray(line, "RGBA"))

    def _spark_icon(self, layer, c, k):
        """Four-point sparkle with a soft glow (command mode)."""
        x, y = c
        s, w = 6.2 * k, 1.5 * k
        pts = [(x, y - s), (x + w, y - w), (x + s, y), (x + w, y + w), (x, y + s), (x - w, y + w), (x - s, y), (x - w, y - w)]
        g = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(g).polygon(pts, fill=(255, 255, 255, 140))
        layer.alpha_composite(g.filter(ImageFilter.GaussianBlur(2.4 * k)))
        ImageDraw.Draw(layer).polygon(pts, fill=(*FG, 255))

    def _bars(self, layer, d, P, cy, span, ch, x_offset=0.0):
        n = BARS
        bw = 2.4
        gap = max(1.6, (span - n * bw) / (n - 1))
        total = n * bw + (n - 1) * gap
        k = self._scale * SS
        maxh = ch - 11
        bars = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        bd = ImageDraw.Draw(bars)
        for i in range(n):
            x = x_offset - total / 2 + i * (bw + gap)
            h = 3 + self._disp[i] * (maxh - 3)
            bd.rounded_rectangle((*P(x, cy - h / 2), *P(x + bw, cy + h / 2)), radius=bw / 2 * k, fill=(250, 250, 252, 255))
        # soft white glow under the bars, like the dashboard chart lines
        glow = bars.filter(ImageFilter.GaussianBlur(2.2 * k)).getchannel("A").point(lambda v: int(v * 0.45))
        halo = Image.new("RGBA", layer.size, (255, 255, 255, 0))
        halo.putalpha(glow)
        layer.alpha_composite(halo)
        layer.alpha_composite(bars)

    def _dot(self, layer, d, c, r, color, glow=0.0):
        x, y = c
        if glow:
            g = Image.new("RGBA", layer.size, (0, 0, 0, 0))
            ImageDraw.Draw(g).ellipse((x - r * 2.4, y - r * 2.4, x + r * 2.4, y + r * 2.4), fill=(*color, int(120 * glow)))
            layer.alpha_composite(g.filter(ImageFilter.GaussianBlur(r * 1.2)))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*color, 255))

    def _silver_disc(self, layer, c, r, bright=False):
        x, y = c
        size = int(r * 2) + 2
        t = np.linspace(0, 1, size)[:, None]
        top, bot = np.array([255, 255, 255]), np.array([214, 214, 221] if bright else [196, 196, 205])
        col = (top + (bot - top) * t)[:, None, :].repeat(size, axis=1).reshape(size, size, 3)
        disc = np.zeros((size, size, 4), np.uint8)
        disc[..., :3] = col.astype(np.uint8)
        m = Image.new("L", (size, size), 0)
        ImageDraw.Draw(m).ellipse((1, 1, size - 2, size - 2), fill=255)
        disc[..., 3] = np.asarray(m)
        layer.alpha_composite(Image.fromarray(disc, "RGBA"), dest=(int(x - size / 2), int(y - size / 2)))

    def _x_icon(self, d, c, s, wdt, color):
        x, y = c
        wd = max(1, round(wdt))
        d.line([(x - s, y - s), (x + s, y + s)], fill=(*color, 255), width=wd)
        d.line([(x - s, y + s), (x + s, y - s)], fill=(*color, 255), width=wd)

    def _mic_icon(self, d, c, k):
        x, y = c
        wd = max(1, round(1.5 * k))
        d.rounded_rectangle((x - 2.7 * k, y - 7 * k, x + 2.7 * k, y + 1.6 * k), radius=2.7 * k, fill=(*FG, 255))
        d.arc((x - 5.2 * k, y - 4.2 * k, x + 5.2 * k, y + 4.2 * k), start=20, end=160, fill=(*FG, 255), width=wd)
        d.line([(x, y + 4.2 * k), (x, y + 6.8 * k)], fill=(*FG, 255), width=wd)

    def _note_icon(self, d, c, k):
        x, y = c
        if self.note_on:
            s = 3.6 * k
            d.rounded_rectangle((x - s, y - s, x + s, y + s), radius=1.4 * k, fill=(*BAD, 255))
            return
        r = 6.2 * k
        d.ellipse((x - r, y - r, x + r, y + r), outline=(*FG, 255), width=max(1, round(1.5 * k)))
        r = 2.7 * k
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*FG, 255))

    def _warn_icon(self, d, c, k):
        x, y = c
        s = 7 * k
        d.polygon([(x, y - s), (x + s * 1.05, y + s * 0.8), (x - s * 1.05, y + s * 0.8)], outline=(*WARN, 255),
                  width=max(1, round(1.4 * k)))
        d.line([(x, y - s * 0.3), (x, y + s * 0.25)], fill=(*WARN, 255), width=max(1, round(1.4 * k)))
        r = 0.9 * k
        d.ellipse((x - r, y + s * 0.45 - r, x + r, y + s * 0.45 + r), fill=(*WARN, 255))

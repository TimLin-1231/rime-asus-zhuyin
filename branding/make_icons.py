# -*- coding: utf-8 -*-
"""
島輝 DaoHui -- icon generator (concepts, mode icons, Weasel .ico export).

    python make_icons.py all [D E F]       # everything below, in order (default: all concepts A-F)
    python make_icons.py concepts [X..]    # concepts/<X>.svg + concepts/<X>-512.png + preview sheet(s)
    python make_icons.py modes [X..]       # mode_icons_preview.png (A-C) / inkwash_mode_icons_preview.png (D-F)
    python make_icons.py export A [B ..]   # out/<X>/*.ico + app-256.png / app-512.png
    python make_icons.py verify            # open every out/*/*.ico with Pillow and list sizes
    python make_icons.py final [F]         # chosen design: concepts/F.*, out/F/*, out/F/tauri/icons/*,
                                           # final_F_preview.png, then verify

Concepts: A-C colour (sunrise island / gold 島 / wave+arc); D-F 水墨 ink-wash,
black and greys only (wet ink with bleed / dry brush 飛白 / white-on-black).
The ink look is procedural (noise, blur, thresholds, stroke-direction streaks)
on the DFKai-SB (kaiu.ttf) glyph; 16-20 px use hinted JhengHei Bold for legibility.

How it draws
------------
Every icon is a list of drawing ops (clip / fill shape with paint) whose
coordinates are in *target pixels* for the size being rendered.  The same op
list drives two back ends:

* raster: shapes are rasterised at SS x supersampling (numpy masks + Pillow
  polygons), composited with premultiplied alpha and box-filtered down to the
  target size.  Because geometry is in target pixels, the concept/mode
  functions can snap edges to the pixel grid at small sizes ("hinting") and
  drop details that turn to noise below ~32 px.
* svg: the 512 px op list is written out as a vector SVG (the 島 glyph is
  converted to a path with fontTools, so no font is needed at runtime).

Fonts (Microsoft JhengHei Bold) are only read while generating.
"""
import io
import math
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_CJK = r"C:\Windows\Fonts\msjhbd.ttc"      # Microsoft JhengHei Bold (index 0)
# Brush glyph for the ink-wash concepts: Yuji Syuku (SIL OFL 1.1, Kinuta Font Factory,
# https://github.com/Kinutafontfactory/Yuji), downloaded to branding/fonts/ with its OFL.txt.
# It replaced DFKai-SB (kaiu.ttf, commercial licence) -- note D/E in inkwash_preview.png and
# out/D, out/E were rendered with DFKai-SB before the switch.
FONT_DFKAI = r"C:\Windows\Fonts\kaiu.ttf"
FONT_KAI = os.path.join(HERE, "fonts", "YujiSyuku-Regular.ttf")
FONT_TXT = r"C:\Windows\Fonts\msjh.ttc"        # labels on preview sheets only
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
ICON_KINDS = ["weasel", "zh", "en", "full", "half", "reload", "installer"]


# --------------------------------------------------------------------------
# colour helpers
# --------------------------------------------------------------------------
def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def stops(*items):
    """stops((pos, '#hex'[, alpha]), ...) -> normalised list."""
    out = []
    for it in items:
        pos, col = it[0], it[1]
        a = it[2] if len(it) > 2 else 1.0
        out.append((float(pos), col, float(a)))
    return out


def solid(h, a=1.0):
    return ("solid", h, a)


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------
def tile_box(s):
    """(margin, side) of the app tile inside an s x s icon."""
    m = 0 if s <= 32 else round(s / 32)
    return m, s - 2 * m


def corner(L):
    return max(2, round(L * 0.2)) if L <= 64 else L * 0.2


def nearest_parity(x, parity, lo=1):
    n = math.floor(x)
    cands = [c for c in range(n - 2, n + 3) if c % 2 == parity and c >= lo]
    return min(cands, key=lambda c: (abs(c - x), -c))


def catmull(pts):
    """Catmull-Rom through pts -> list of cubic segments (p0, c1, c2, p1)."""
    P = [pts[0]] + list(pts) + [pts[-1]]
    segs = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        segs.append((p1, c1, c2, p2))
    return segs


def flatten(segs, n=32):
    pts = [segs[0][0]]
    for p0, c1, c2, p1 in segs:
        for i in range(1, n + 1):
            t = i / n
            mt = 1 - t
            x = mt ** 3 * p0[0] + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t ** 3 * p1[0]
            y = mt ** 3 * p0[1] + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t ** 3 * p1[1]
            pts.append((x, y))
    return pts


def polar(cx, cy, r, deg):
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


# --------------------------------------------------------------------------
# glyph helpers (font read at generation time only)
# --------------------------------------------------------------------------
_font_cache = {}


def _font(path, size, index=0):
    """path may carry a variable-font weight: 'NotoSansTC-VF.ttf@700'."""
    key = (path, size, index)
    if key not in _font_cache:
        file, _, wght = path.partition("@")
        f = ImageFont.truetype(file, size, index=index)
        if wght:
            f.set_variation_by_axes([int(wght)])
        _font_cache[key] = f
    return _font_cache[key]


def glyph_bitmap(ch, fs, path=FONT_CJK, index=0):
    """Ink-tight L bitmap of ch rendered by FreeType at font size fs (hinted)."""
    f = _font(path, fs, index)
    im = Image.new("L", (fs * 3, fs * 3), 0)
    ImageDraw.Draw(im).text((fs, fs), ch, font=f, fill=255)
    return im.crop(im.getbbox())


_ink_ratio = {}


def ink_ratio(ch, path=FONT_CJK, index=0):
    key = (ch, path, index)
    if key not in _ink_ratio:
        bm = glyph_bitmap(ch, 1000, path, index)
        _ink_ratio[key] = bm.size[1] / 1000.0
    return _ink_ratio[key]


_tt_cache = {}
# DynaLab fonts (標楷體 kaiu, 細明體) assemble glyphs from components positioned by
# hinting bytecode ("tricky fonts"): raw outlines read with fontTools come out
# broken, so for these the FreeType-rendered bitmap is traced into a path instead.
TRACE_FONTS = {FONT_DFKAI.lower()}


def glyph_traced_path(ch, cx, cy, ink_h, path, res=1400):
    from skimage import measure
    a = np.pad(np.asarray(glyph_bitmap(ch, res, path), np.float32) / 255.0, 2)
    k = ink_h / (a.shape[0] - 4)
    ox, oy = cx - a.shape[1] / 2 * k, cy - a.shape[0] / 2 * k
    parts = []
    for c in measure.find_contours(a, 0.5):
        c = measure.approximate_polygon(c, tolerance=0.7)
        if len(c) >= 3:
            parts.append("M" + " L".join("%s %s" % (f2(ox + x * k), f2(oy + y * k)) for y, x in c) + "Z")
    return " ".join(parts)


def glyph_svg_path(ch, cx, cy, ink_h, path=FONT_CJK, index=0):
    if path.lower() in TRACE_FONTS:
        return glyph_traced_path(ch, cx, cy, ink_h, path)
    from fontTools.ttLib import TTCollection, TTFont
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.pens.boundsPen import BoundsPen
    key = (path, index)
    if key not in _tt_cache:
        _tt_cache[key] = (TTCollection(path).fonts[index] if path.lower().endswith(".ttc")
                          else TTFont(path))
    tt = _tt_cache[key]
    gs = tt.getGlyphSet()
    name = tt.getBestCmap()[ord(ch)]
    bp = BoundsPen(gs)
    gs[name].draw(bp)
    x0, y0, x1, y1 = bp.bounds
    k = ink_h / (y1 - y0)
    pen = SVGPathPen(gs)
    tp = TransformPen(pen, (k, 0, 0, -k, cx - (x0 + x1) / 2 * k, cy + (y0 + y1) / 2 * k))
    gs[name].draw(tp)
    import re
    return re.sub(r"-?\d+\.\d+", lambda mo: f2(float(mo.group())), pen.getCommands())


# --------------------------------------------------------------------------
# procedural ink helpers (D/E/F)
# --------------------------------------------------------------------------
def smoothstep(a, lo, hi):
    t = np.clip((a - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def gblur(a, r):
    """Gaussian blur of a 0..1 float array (Pillow, 8-bit; plenty for thresholds)."""
    if r < 0.3:
        return a
    im = Image.fromarray(np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8), "L")
    return np.asarray(im.filter(ImageFilter.GaussianBlur(r)), np.float32) / 255.0


_noise_cache = {}


def noise2(W, cells, seed, octaves=1):
    """Smooth value noise on a W x W canvas, ~uniform in [0,1].  `cells` counts
    lattice cells across the whole canvas, so the pattern is the same at every
    render size (only resampled)."""
    key = (W, round(cells, 3), seed, octaves)
    if key in _noise_cache:
        return _noise_cache[key]
    if len(_noise_cache) > 24:
        _noise_cache.clear()
    rng = np.random.default_rng(seed)
    acc = np.zeros((W, W), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        n = max(2, int(round(cells * 2 ** o))) + 1
        small = rng.random((n, n)).astype(np.float32)
        acc += amp * np.asarray(Image.fromarray(small, "F").resize((W, W), Image.BICUBIC), np.float32)
        tot += amp
        amp *= 0.5
    acc /= tot
    lo, hi = np.percentile(acc[::5, ::5], [1, 99])
    out = np.clip((acc - lo) / (hi - lo + 1e-9), 0, 1)
    _noise_cache[key] = out
    return out


_n1d = {}


def noise1d(t, seed, N=8192):
    """1-D value noise sampled at t (cell units), equalised to ~uniform [0,1]."""
    if seed not in _n1d:
        arr = np.random.default_rng(seed).random(N).astype(np.float32)
        fine = np.interp(np.linspace(0, N - 1, N * 4), np.arange(N), arr)
        _n1d[seed] = (arr, np.sort(fine))
    arr, srt = _n1d[seed]
    v = np.interp(np.mod(t, N - 1), np.arange(N), arr)
    return np.interp(v, srt, np.linspace(0, 1, len(srt))).astype(np.float32)


# --------------------------------------------------------------------------
# raster back end
# --------------------------------------------------------------------------
def pick_ss(s):
    return max(4, (1024 if s <= 64 else 2048) // s)


class Raster:
    def __init__(self, s, ss=None):
        self.s = s
        self.ss = ss or pick_ss(s)
        self.W = s * self.ss
        self.buf = np.zeros((self.W, self.W, 4), np.float32)   # premultiplied RGBA
        self.clip = None
        self.cache = {}
        c = (np.arange(self.W, dtype=np.float32) + 0.5) / self.ss  # pixel centres, target px
        self.X = c[None, :]
        self.Y = c[:, None]

    # ---- shapes -> coverage masks (W x W float32) ----
    def _pil(self):
        im = Image.new("L", (self.W, self.W), 0)
        return im, ImageDraw.Draw(im)

    def _k(self, x, y):
        # target px -> Pillow canvas coords (Pillow puts pixel centres on integers)
        return (x * self.ss - 0.5, y * self.ss - 0.5)

    def mask(self, shp):
        X, Y = self.X, self.Y
        kind = shp[0]
        if kind == "rect":
            _, x0, y0, x1, y1 = shp
            return ((X >= x0) & (X < x1) & (Y >= y0) & (Y < y1)).astype(np.float32)
        if kind == "rrect":
            _, x0, y0, x1, y1, r = shp
            dx = np.maximum(np.maximum(x0 + r - X, X - (x1 - r)), 0)
            dy = np.maximum(np.maximum(y0 + r - Y, Y - (y1 - r)), 0)
            return ((X >= x0) & (X < x1) & (Y >= y0) & (Y < y1)
                    & (dx * dx + dy * dy <= r * r)).astype(np.float32)
        if kind == "circle":
            _, cx, cy, r = shp
            return (((X - cx) ** 2 + (Y - cy) ** 2) <= r * r).astype(np.float32)
        if kind == "sector":
            # annulus sector, angles in degrees, clockwise on screen from +x
            _, cx, cy, ri, ro, a0, a1, cap = shp
            d2 = (X - cx) ** 2 + (Y - cy) ** 2
            m = (d2 <= ro * ro) & (d2 >= ri * ri)
            if a1 - a0 < 360:
                ang = np.degrees(np.arctan2(Y - cy, X - cx)) % 360
                m &= ((ang - a0) % 360) <= (a1 - a0)
                if cap == "round":
                    rc, rr = (ri + ro) / 2, (ro - ri) / 2
                    for a in (a0, a1):
                        px, py = polar(cx, cy, rc, a)
                        m |= ((X - px) ** 2 + (Y - py) ** 2) <= rr * rr
            return m.astype(np.float32)
        if kind in ("poly", "path"):
            pts = shp[1] if kind == "poly" else flatten(shp[1])
            im, d = self._pil()
            d.polygon([self._k(x, y) for x, y in pts], fill=255)
            return np.asarray(im, np.float32) / 255.0
        if kind == "stroke":
            # exact distance-to-polyline stroke (round joins, round caps)
            _, pts, w, cap = shp
            r, ss, W = w / 2, self.ss, self.W
            m = np.zeros((W, W), bool)
            for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
                i0 = max(0, int((min(y0, y1) - r) * ss) - 1)
                i1 = min(W, int((max(y0, y1) + r) * ss) + 2)
                j0 = max(0, int((min(x0, x1) - r) * ss) - 1)
                j1 = min(W, int((max(x0, x1) + r) * ss) + 2)
                if i0 >= i1 or j0 >= j1:
                    continue
                Xs = self.X[:, j0:j1]
                Ys = self.Y[i0:i1, :]
                dx, dy = x1 - x0, y1 - y0
                L2 = dx * dx + dy * dy or 1e-12
                t = np.clip(((Xs - x0) * dx + (Ys - y0) * dy) / L2, 0, 1)
                m[i0:i1, j0:j1] |= ((Xs - x0 - t * dx) ** 2 + (Ys - y0 - t * dy) ** 2) <= r * r
            return m.astype(np.float32)
        if kind == "glyph":
            _, ch, cx, cy, ink_h, hinted = shp[:6]
            font = shp[6] if len(shp) > 6 else FONT_CJK
            if hinted:
                # FreeType-hinted bitmap at the real pixel size, placed on whole pixels,
                # then blown up so the box filter returns exactly these pixels.
                bm = glyph_bitmap(ch, max(6, int(round(ink_h / ink_ratio(ch, font)))), font)
                small = Image.new("L", (self.s, self.s), 0)
                small.paste(bm, (int(round(cx - bm.size[0] / 2)), int(round(cy - bm.size[1] / 2))))
                a = np.asarray(small, np.float32) / 255.0
                return np.repeat(np.repeat(a, self.ss, 0), self.ss, 1)
            return self._gcanvas(ch, cx, cy, ink_h, font)
        if kind == "rring":   # rounded-rect border of width w (inside the edge)
            _, x0, y0, x1, y1, r, w = shp
            return np.clip(self.mask(("rrect", x0, y0, x1, y1, r))
                           - self.mask(("rrect", x0 + w, y0 + w, x1 - w, y1 - w, max(0.5, r - w))), 0, 1)
        if kind == "rough":   # ragged ink edge: blur + noise + threshold
            _, inner, amp, cells, seed = shp
            b = gblur(self.mask(inner), 0.3 * self.W / cells)
            return smoothstep(b + (noise2(self.W, cells, seed, 3) - 0.5) * amp, 0.47, 0.53)
        if kind == "ink":
            return self.ink(*shp[1:])
        if kind == "swash":
            return self.swash(*shp[1:])
        if kind == "enso":
            return self.enso(*shp[1:])
        raise ValueError(kind)

    # ---- procedural ink ----
    def _gcanvas(self, ch, cx, cy, ink_h, font):
        key = ("g", ch, cx, cy, ink_h, font)
        if key not in self.cache:
            bm = glyph_bitmap(ch, max(8, int(round(ink_h * self.ss / ink_ratio(ch, font)))), font)
            im, _ = self._pil()
            im.paste(bm, (int(round(cx * self.ss - bm.size[0] / 2)),
                          int(round(cy * self.ss - bm.size[1] / 2))))
            self.cache[key] = np.asarray(im, np.float32) / 255.0
        return self.cache[key]

    def _nz(self, per_tile, L, seed, octaves=1):
        return noise2(self.W, per_tile * self.s / L, seed, octaves)

    def _orient(self, g, u):
        """Per-pixel direction *across* the brush stroke (structure tensor of the glyph)."""
        from scipy.ndimage import gaussian_filter
        R = min(self.W, 512)
        f = self.W / R
        gl = np.asarray(Image.fromarray((g * 255).astype(np.uint8), "L").resize((R, R), Image.BILINEAR),
                        np.float32) / 255.0
        gs = gaussian_filter(gl, 0.010 * u / f)
        gy, gx = np.gradient(gs)
        s2 = 0.03 * u / f
        jxx, jyy, jxy = (gaussian_filter(a, s2) for a in (gx * gx, gy * gy, gx * gy))
        up = lambda a: np.asarray(Image.fromarray(a.astype(np.float32), "F").resize((self.W, self.W),
                                                                                    Image.BILINEAR), np.float32)
        th = 0.5 * np.arctan2(up(2 * jxy), up(jxx - jyy))
        return np.where(th < -np.pi / 4, th + np.pi, th)

    def ink(self, ch, cx, cy, ink_h, style, seed, font, L):
        """Glyph coverage with an ink treatment.
        wet_core: bold, ragged, slightly uneven tone with darker pooled edges
        wet_halo: the grey bleed (暈染) around a wet_core glyph
        dry:      飛白 dry brush -- white streaks running along each stroke
        neg:      lightly ragged edge (for white-on-black)
        bold:     emboldened, no texture (24-32 px)"""
        key = ("ink", ch, cx, cy, ink_h, style, seed, font, L)
        if key in self.cache:
            return self.cache[key]
        g = self._gcanvas(ch, cx, cy, ink_h, font)
        u = L * self.ss
        if style in ("wet_core", "wet_halo"):
            core = smoothstep(gblur(g, 0.010 * u) + (self._nz(70, L, seed, 3) - 0.5) * 0.30, 0.34, 0.40)
            if style == "wet_halo":
                # bleed stays attached to the strokes: noise only modulates the blurred ink
                n3 = self._nz(22, L, seed + 2, 3)
                out = smoothstep(gblur(core, 0.03 * u) * (0.55 + 0.9 * n3), 0.10, 0.26) * (0.55 + 0.45 * n3)
            else:
                tone = 0.80 + 0.20 * self._nz(5, L, seed + 1, 2)
                pooled = np.clip(core - smoothstep(gblur(core, 0.012 * u), 0.55, 0.95), 0, 1)
                out = np.clip(core * tone + pooled * 0.3, 0, 1)
        elif style == "dry":
            body = smoothstep(gblur(g, 0.004 * u) + (self._nz(90, L, seed, 3) - 0.5) * 0.32, 0.46, 0.54)
            # snap the across-stroke direction to 45 deg steps: a continuously varying
            # direction makes the streaks swirl like marble instead of running straight
            th = np.round(self._orient(g, u) / (np.pi / 4)) * (np.pi / 4)
            ss = self.ss
            across = (self.X * ss) * np.cos(th) + (self.Y * ss) * np.sin(th)
            streak = noise1d(across / (0.0065 * u) + 3000, seed + 5)
            dry = smoothstep(self._nz(3.5, L, seed + 6, 2), 0.38, 0.88)
            gaps = smoothstep(streak - (1 - 0.50 * dry), 0.0, 0.05)
            tone = 0.88 + 0.12 * self._nz(6, L, seed + 7, 2)
            out = body * (1 - 0.97 * gaps) * tone
        elif style == "neg":
            out = smoothstep(gblur(g, 0.004 * u) + (self._nz(80, L, seed, 3) - 0.5) * 0.30, 0.47, 0.53)
        elif style == "bold":
            out = smoothstep(gblur(g, 0.018 * u), 0.24, 0.34)
        elif style == "plain":
            out = g
        else:
            raise ValueError(style)
        self.cache[key] = out
        return out

    def swash(self, x0, x1, yc, h, seed, L):
        """A horizontal wash stroke (ink-wash sea/mist) that dries out to the right."""
        X, Y = self.X, self.Y
        t = (X - x0) / (x1 - x0)
        yl = yc + 0.015 * L * np.sin(2 * np.pi * (t * 1.3 + 0.2))
        prof = h / 2 * np.sqrt(np.clip(t / 0.05, 0, 1)) * (1 - 0.35 * np.clip(t, 0, 1))
        prof = prof * (1 + 0.25 * (self._nz(30, L, seed, 3) - 0.5))
        inside = ((np.abs(Y - yl) <= prof) & (t >= 0) & (t <= 1)).astype(np.float32)
        streak = noise1d((Y - yl + 0.01 * X) / (0.010 * L) + 3000, seed + 1)
        dry = np.clip(smoothstep(t, 0.50, 1.1) + 0.3 * (self._nz(4, L, seed + 2, 2) - 0.5), 0, 1)
        gaps = smoothstep(streak - (1 - 0.5 * dry), 0.0, 0.05)
        return inside * (1 - gaps) * (0.75 + 0.25 * self._nz(8, L, seed + 3, 2))

    def enso(self, cx, cy, r, w, a0, span, seed, L):
        """Brush circle (円相): swells mid-stroke, thins and goes dry towards its end."""
        X, Y = self.X, self.Y
        d = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
        ang = (np.degrees(np.arctan2(Y - cy, X - cx)) - a0) % 360
        p = ang / span
        rr = r * (1 + 0.02 * (noise1d(ang / 25.0 + 100, seed) - 0.5))
        wp = w * (0.8 + 0.3 * np.sin(np.pi * np.clip(p, 0, 1))) * (1 - 0.55 * smoothstep(p, 0.75, 1.0))
        body = ((np.abs(d - rr) <= wp / 2) & (p <= 1)).astype(np.float32)
        sx, sy = polar(cx, cy, r, a0)
        body = np.maximum(body, (((X - sx) ** 2 + (Y - sy) ** 2) <= (0.5 * w) ** 2).astype(np.float32))
        streak = noise1d((d - rr) / (0.007 * L) + 3000, seed + 1)
        dry = smoothstep(np.clip(p, 0, 1), 0.45, 1.0)
        gaps = smoothstep(streak - (1 - 0.85 * dry), 0.0, 0.05)
        return body * (1 - gaps)

    # ---- paints -> (rgb, alpha) ----
    def _ramp(self, t, st):
        pos = [p for p, _, _ in st]
        cols = [rgb(c) for _, c, _ in st]
        out = np.empty(t.shape + (3,), np.float32)
        for i in range(3):
            out[..., i] = np.interp(t, pos, [c[i] for c in cols])
        a = np.interp(t, pos, [x for _, _, x in st]).astype(np.float32)
        return out, a

    def paint(self, p):
        kind = p[0]
        if kind == "solid":
            return np.array(rgb(p[1]), np.float32), p[2]
        if kind == "lin":
            _, (x0, y0), (x1, y1), st = p
            dx, dy = x1 - x0, y1 - y0
            L2 = dx * dx + dy * dy or 1e-9
            t = np.clip(((self.X - x0) * dx + (self.Y - y0) * dy) / L2, 0, 1)
            return self._ramp(t, st)
        if kind == "rad":
            _, (cx, cy), r, st = p
            t = np.clip(np.sqrt((self.X - cx) ** 2 + (self.Y - cy) ** 2) / r, 0, 1)
            return self._ramp(t, st)
        if kind == "paper":   # flat colour + fine grain + soft mottling (rice paper / ink stone)
            _, base, seed, amt = p
            f = 1 + amt * ((noise2(self.W, 150, seed, 2) - 0.5) + 0.8 * (noise2(self.W, 5, seed + 1, 2) - 0.5))
            return np.clip(np.array(rgb(base), np.float32) * f[..., None], 0, 1), 1.0
        raise ValueError(kind)

    def run(self, ops):
        for op in ops:
            if op[0] == "clip":
                self.clip = self.mask(op[1])
            elif op[0] == "fill":
                shp, pnt = op[1], op[2]
                opacity = op[3] if len(op) > 3 else 1.0
                m = self.mask(shp)
                col, a = self.paint(pnt)
                a = m * a * opacity
                if self.clip is not None:
                    a = a * self.clip
                a = np.broadcast_to(a, (self.W, self.W))[..., None]
                self.buf[..., :3] = col * a + self.buf[..., :3] * (1 - a)
                self.buf[..., 3:] = a + self.buf[..., 3:] * (1 - a)
            else:
                raise ValueError(op[0])
        return self

    def image(self):
        s, ss = self.s, self.ss
        b = self.buf.reshape(s, ss, s, ss, 4).mean(axis=(1, 3))
        a = b[..., 3:4]
        col = np.where(a > 1e-6, b[..., :3] / np.maximum(a, 1e-6), 0)
        out = np.concatenate([col, a], -1)
        return Image.fromarray(np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA")


def render(fn, s):
    return Raster(s).run(fn(s)).image()


# --------------------------------------------------------------------------
# SVG back end
# --------------------------------------------------------------------------
def f2(v):
    return ("%.2f" % v).rstrip("0").rstrip(".")


class SVG:
    def __init__(self, s):
        self.s = s
        self.defs = []
        self.body = []
        self.n = 0
        self.open_groups = 0

    def _id(self, p):
        self.n += 1
        return "%s%d" % (p, self.n)

    def _filt(self, kind, seed=1, L=480.0, **k):
        """SVG filter approximating a raster ink effect; returns its id."""
        fid = self._id("f")
        disp = ('<feTurbulence type="fractalNoise" baseFrequency="%s" numOctaves="3" seed="%d" result="rt"/>'
                '<feDisplacementMap in="%%s" in2="rt" scale="%s" xChannelSelector="R" yChannelSelector="G"/>'
                % (f2(k.get("freq", 22.0 / L)), seed, f2(k.get("scale", 0.007 * L))))
        if kind == "rough":
            pre = ""
            src = "SourceGraphic"
            if k.get("dilate"):
                pre = '<feMorphology operator="dilate" radius="%s" result="dl"/>' % f2(k["dilate"])
                src = "dl"
            body = pre + disp % src
        elif kind == "bleed":
            body = ('<feMorphology operator="dilate" radius="%s" result="dl"/>'
                    '<feGaussianBlur in="dl" stdDeviation="%s" result="bl"/>' % (f2(0.012 * L), f2(0.03 * L))
                    + ('<feTurbulence type="fractalNoise" baseFrequency="%s" numOctaves="3" seed="%d" result="rt"/>'
                       '<feDisplacementMap in="bl" in2="rt" scale="%s" xChannelSelector="R" yChannelSelector="G"/>'
                       % (f2(9.0 / L), seed, f2(0.05 * L))))
        elif kind == "dry":
            # streak gaps (anisotropic turbulence) where a low-frequency "dryness" field is high
            fx, fy = k.get("streak", (1.5 / L, 75.0 / L))
            body = ('<feTurbulence type="fractalNoise" baseFrequency="%s %s" numOctaves="2" seed="%d" result="st"/>'
                    '<feColorMatrix in="st" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 9 0 0 0 -5.1" result="gp"/>'
                    '<feTurbulence type="fractalNoise" baseFrequency="%s" numOctaves="2" seed="%d" result="dn"/>'
                    '<feColorMatrix in="dn" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 6 0 0 0 -2.7" result="dr"/>'
                    '<feComposite in="gp" in2="dr" operator="in" result="g2"/>'
                    '<feComposite in="SourceGraphic" in2="g2" operator="out" result="cut"/>'
                    % (f2(fx), f2(fy), seed, f2(4.0 / L), seed + 1)) + disp % "cut"
        elif kind == "grain":
            body = ('<feTurbulence type="fractalNoise" baseFrequency="%s" numOctaves="2" seed="%d" result="n"/>'
                    '<feColorMatrix in="n" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 %s 0 0 0 %s" result="g"/>'
                    '<feComposite in="g" in2="SourceGraphic" operator="in" result="gi"/>'
                    '<feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="gi"/></feMerge>'
                    % (f2(300.0 / L), seed, f2(0.18 * k.get("amt", 1)), f2(-0.08 * k.get("amt", 1))))
        else:
            raise ValueError(kind)
        self.defs.append('<filter id="%s" x="-10%%" y="-10%%" width="120%%" height="120%%" '
                         'color-interpolation-filters="sRGB">%s</filter>' % (fid, body))
        return fid

    def _paint(self, p):
        """returns (paint-ref, extra-opacity, filter-id-or-None)."""
        if p[0] == "solid":
            return p[1], p[2], None
        if p[0] == "paper":
            _, base, seed, amt = p
            return base, 1.0, self._filt("grain", seed, amt=amt / 0.06)
        st = "".join('<stop offset="%s" stop-color="%s"%s/>'
                     % (f2(pos), col, "" if a >= 1 else ' stop-opacity="%s"' % f2(a))
                     for pos, col, a in p[-1])
        gid = self._id("g")
        if p[0] == "lin":
            (x0, y0), (x1, y1) = p[1], p[2]
            self.defs.append('<linearGradient id="%s" gradientUnits="userSpaceOnUse" x1="%s" y1="%s" '
                             'x2="%s" y2="%s">%s</linearGradient>'
                             % (gid, f2(x0), f2(y0), f2(x1), f2(y1), st))
        else:
            (cx, cy), r = p[1], p[2]
            self.defs.append('<radialGradient id="%s" gradientUnits="userSpaceOnUse" cx="%s" cy="%s" '
                             'r="%s">%s</radialGradient>' % (gid, f2(cx), f2(cy), f2(r), st))
        return "url(#%s)" % gid, 1.0, None

    def _shape(self, shp):
        """returns (element-without-paint-attrs, mode) mode in fill|stroke."""
        k = shp[0]
        if k == "rect":
            _, x0, y0, x1, y1 = shp
            return '<rect x="%s" y="%s" width="%s" height="%s"' % (f2(x0), f2(y0), f2(x1 - x0), f2(y1 - y0)), "fill", None
        if k == "rrect":
            _, x0, y0, x1, y1, r = shp
            return ('<rect x="%s" y="%s" width="%s" height="%s" rx="%s"'
                    % (f2(x0), f2(y0), f2(x1 - x0), f2(y1 - y0), f2(r))), "fill", None
        if k == "circle":
            _, cx, cy, r = shp
            return '<circle cx="%s" cy="%s" r="%s"' % (f2(cx), f2(cy), f2(r)), "fill", None
        if k == "poly":
            return '<path d="M%sZ"' % " L".join("%s %s" % (f2(x), f2(y)) for x, y in shp[1]), "fill", None
        if k == "path":
            segs = shp[1]
            d = "M%s %s" % (f2(segs[0][0][0]), f2(segs[0][0][1]))
            for _, c1, c2, p1 in segs:
                d += " C%s %s %s %s %s %s" % tuple(f2(v) for v in (*c1, *c2, *p1))
            return '<path d="%sZ"' % d, "fill", None
        if k == "stroke":
            _, pts, w, cap = shp
            d = "M" + " L".join("%s %s" % (f2(x), f2(y)) for x, y in pts)
            return ('<path d="%s" fill="none" stroke-width="%s" stroke-linecap="%s" stroke-linejoin="round"'
                    % (d, f2(w), cap)), "stroke", None
        if k == "sector":
            _, cx, cy, ri, ro, a0, a1, cap = shp
            if ri <= 0:  # pie
                x0, y0 = polar(cx, cy, ro, a0)
                x1, y1 = polar(cx, cy, ro, a1)
                large = 1 if a1 - a0 > 180 else 0
                return ('<path d="M%s %s L%s %s A%s %s 0 %d 1 %s %s Z"'
                        % (f2(cx), f2(cy), f2(x0), f2(y0), f2(ro), f2(ro), large, f2(x1), f2(y1))), "fill", None
            rc, w = (ri + ro) / 2, ro - ri
            if a1 - a0 >= 360:
                return ('<circle cx="%s" cy="%s" r="%s" fill="none" stroke-width="%s"'
                        % (f2(cx), f2(cy), f2(rc), f2(w))), "stroke", None
            x0, y0 = polar(cx, cy, rc, a0)
            x1, y1 = polar(cx, cy, rc, a1)
            large = 1 if a1 - a0 > 180 else 0
            return ('<path d="M%s %s A%s %s 0 %d 1 %s %s" fill="none" stroke-width="%s" stroke-linecap="%s"'
                    % (f2(x0), f2(y0), f2(rc), f2(rc), large, f2(x1), f2(y1), f2(w), cap)), "stroke", None
        if k == "glyph":
            _, ch, cx, cy, ink_h = shp[:5]
            font = shp[6] if len(shp) > 6 else FONT_CJK
            return '<path d="%s"' % glyph_svg_path(ch, cx, cy, ink_h, font), "fill", None
        if k == "rring":
            _, x0, y0, x1, y1, r, w = shp
            return ('<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="none" stroke-width="%s"'
                    % (f2(x0 + w / 2), f2(y0 + w / 2), f2(x1 - x0 - w), f2(y1 - y0 - w), f2(r - w / 2), f2(w))), \
                "stroke", None
        if k == "rough":
            _, inner, amp, cells, seed = shp
            el, mode, _ = self._shape(inner)
            return el, mode, self._filt("rough", seed, L=self.s, freq=cells / self.s, scale=0.006 * self.s)
        if k == "ink":
            _, ch, cx, cy, ink_h, style, seed, font, L = shp
            el = '<path fill-rule="evenodd" d="%s"' % glyph_svg_path(ch, cx, cy, ink_h, font)
            if style == "wet_core":
                f = self._filt("rough", seed, L, dilate=0.008 * L)
            elif style == "wet_halo":
                f = self._filt("bleed", seed, L)
            elif style == "dry":
                f = self._filt("dry", seed, L)
            elif style == "bold":
                f = self._filt("rough", seed, L, dilate=0.012 * L, scale=0.001 * L)
            else:
                f = self._filt("rough", seed, L, scale=0.005 * L)
            return el, "fill", f
        if k == "swash":
            _, x0, x1, yc, h, seed, L = shp
            n = 48
            top, bot = [], []
            for i in range(n + 1):
                t = i / n
                x = x0 + (x1 - x0) * t
                yl = yc + 0.015 * L * math.sin(2 * math.pi * (t * 1.3 + 0.2))
                hh = h / 2 * math.sqrt(min(1, t / 0.05)) * (1 - 0.35 * t)
                top.append((x, yl - hh))
                bot.append((x, yl + hh))
            el, _, _ = self._shape(("poly", top + bot[::-1]))
            return el, "fill", self._filt("dry", seed, L, streak=(0.8 / L, 55.0 / L))
        if k == "enso":
            _, cx, cy, r, w, a0, span, seed, L = shp
            n = 96
            outer, inner = [], []
            for i in range(n + 1):
                p = i / n
                wp = w * (0.8 + 0.3 * math.sin(math.pi * p)) * (1 - 0.55 * (min(1, max(0, (p - 0.75) / 0.25)) ** 2))
                outer.append(polar(cx, cy, r + wp / 2, a0 + span * p))
                inner.append(polar(cx, cy, r - wp / 2, a0 + span * p))
            el, _, _ = self._shape(("poly", outer + inner[::-1]))
            return el, "fill", self._filt("dry", seed, L, streak=(30.0 / L, 30.0 / L))
        raise ValueError(k)

    def run(self, ops):
        for op in ops:
            if op[0] == "clip":
                cid = self._id("clip")
                el, _, _ = self._shape(op[1])
                self.defs.append('<clipPath id="%s">%s/></clipPath>' % (cid, el))
                self.body.append('<g clip-path="url(#%s)">' % cid)
                self.open_groups += 1
            elif op[0] == "fill":
                el, mode, sf = self._shape(op[1])
                ref, a, pf = self._paint(op[2])
                a *= op[3] if len(op) > 3 else 1.0
                attr = ' %s="%s"' % (mode, ref)
                if a < 1:
                    attr += ' %s-opacity="%s"' % (mode, f2(a))
                if sf:
                    attr += ' filter="url(#%s)"' % sf
                item = el + attr + "/>"
                if pf:
                    item = '<g filter="url(#%s)">%s</g>' % (pf, item)
                self.body.append(item)
        self.body.extend(["</g>"] * self.open_groups)
        self.open_groups = 0
        return self

    def text(self):
        s = self.s
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">\n'
                '<defs>\n%s\n</defs>\n%s\n</svg>\n' % (s, s, s, s, "\n".join(self.defs), "\n".join(self.body)))


# ==========================================================================
# CONCEPTS
# ==========================================================================
# A -- 晨島 Dawn Isle: backlit island on a sea line, sun rising behind it.
A_SKY = stops((0, "#E5654F"), (0.55, "#F59C4D"), (1, "#FFD580"))
A_GLOW = stops((0, "#FFF2C2", 0.75), (0.4, "#FFE29A", 0.30), (1, "#FFE29A", 0))
A_SUN = "#FFF9E6"
A_ISLE = stops((0, "#175A6B"), (1, "#0B3644"))
A_SEA = stops((0, "#1C98A2"), (1, "#0A4152"))
A_GLINT = "#FFE09A"
# Taiwan-like profile: long gentle western plain rising to a central range,
# steep eastern face dropping into the sea (avoids the symmetric lone-volcano look)
A_ISLAND_PTS = [(0.04, None), (0.17, 0.607), (0.29, 0.565), (0.39, 0.515), (0.47, 0.462),
                (0.525, 0.437), (0.565, 0.448), (0.615, 0.405), (0.665, 0.448), (0.715, 0.522),
                (0.765, 0.588), (0.815, None)]
A_SUN_POS = (0.725, 0.455, 0.15)   # cx, cy, r (unit)


def concept_A(s):
    m, L = tile_box(s)
    U = lambda v: m + v * L
    hint = s <= 64
    tile = ("rrect", m, m, m + L, m + L, corner(L))
    ops = [("clip", tile)]
    H = round(U(0.64)) if hint else U(0.64)
    ops.append(("fill", tile, ("lin", (0, m), (0, H), A_SKY)))
    ux, uy, ur = A_SUN_POS
    if s <= 32:
        sr = round(0.18 * L)
        sx, sy = round(U(ux)), round(U(uy))
    else:
        sr, sx, sy = ur * L, U(ux), U(uy)
    ops.append(("fill", tile, ("rad", (sx, sy), 0.55 * L, A_GLOW)))
    ops.append(("fill", ("circle", sx, sy, sr), solid(A_SUN)))
    pts = [(U(x), H if y is None else U(y)) for x, y in A_ISLAND_PTS]
    segs = catmull(pts)
    segs.append((pts[-1], (pts[-1][0], H + 1), (pts[0][0], H + 1), pts[0]))  # close under the sea line
    ops.append(("fill", ("path", segs), ("lin", (0, U(0.38)), (0, H), A_ISLE)))
    ops.append(("fill", ("rect", m, H, m + L, m + L), ("lin", (0, H), (0, m + L), A_SEA)))
    # bright sea line, brightest under the sun
    hl = max(1, round(0.014 * L)) if hint else 0.014 * L
    fx = (sx - m) / L
    ops.append(("fill", ("rect", m, H, m + L, H + hl),
                ("lin", (m, 0), (m + L, 0), stops((0, A_GLINT, 0.35), (fx, A_GLINT, 1), (1, A_GLINT, 0.35)))))
    # sun reflection on the water
    if s >= 64:
        # a broken path of light on the water, narrowing towards the viewer
        bars = [(0.055, 0.30, 0.00, 0.95), (0.105, 0.21, 0.025, 0.8), (0.155, 0.15, -0.02, 0.62),
                (0.205, 0.09, 0.015, 0.45), (0.255, 0.05, -0.01, 0.3)]
        bh = 0.022 * L
        for dy, bw, dx, a in bars:
            y0 = H + dy * L if not hint else round(H + dy * L)
            h = bh if not hint else max(1, round(bh))
            x = sx + dx * L
            ops.append(("fill", ("rrect", x - bw * L / 2, y0, x + bw * L / 2, y0 + h, h / 2), solid(A_GLINT, a)))
    else:
        bars = [(2, 0.36, 0.9), (5, 0.20, 0.6)] if s >= 32 else [(2, 0.32, 0.85)]
        for dy, bw, a in bars:
            w = max(2, round(bw * L))
            x0 = round(sx - w / 2)
            ops.append(("fill", ("rect", x0, H + dy, x0 + w, H + dy + 1), solid(A_GLINT, a)))
    return ops


# B -- 島 glyph: gold 島 on deep indigo with a sunburst of light behind it.
B_BG = stops((0, "#3A41AE"), (1, "#141740"))
# light is a lighter indigo, not translucent gold (gold over indigo mixes to grey)
B_GLOW = stops((0, "#6C76F0", 0.70), (0.5, "#5560DA", 0.25), (1, "#5560DA", 0))
B_RAY = stops((0, "#8A93FF", 0), (0.28, "#8A93FF", 0.30), (1, "#8A93FF", 0))
B_GOLD = stops((0, "#FFF1BE"), (0.5, "#FFD064"), (1, "#F5992E"))


def concept_B(s):
    m, L = tile_box(s)
    U = lambda v: m + v * L
    tile = ("rrect", m, m, m + L, m + L, corner(L))
    ops = [("clip", tile), ("fill", tile, ("lin", (U(0.15), U(0)), (U(0.85), U(1)), B_BG))]
    cx, cy = U(0.5), U(0.52)
    ops.append(("fill", tile, ("rad", (cx, cy), 0.62 * L, B_GLOW)))
    if s >= 64:
        n, R = 16, 0.85 * L
        for i in range(n):
            a = i * 360.0 / n - 90
            half = 360.0 / n * 0.24
            p1 = polar(cx, cy, R, a - half)
            p2 = polar(cx, cy, R, a + half)
            ops.append(("fill", ("poly", [(cx, cy), p1, p2]), ("rad", (cx, cy), R, B_RAY)))
    if s <= 24:
        ink = 0.82 * L
    elif s <= 48:
        ink = 0.74 * L
    else:
        ink = 0.66 * L
    hinted = s <= 64
    gy = cy if not hinted else round(cy)
    ops.append(("fill", ("glyph", "島", cx, gy, ink, hinted),
                ("lin", (0, gy - ink / 2), (0, gy + ink / 2), B_GOLD)))
    return ops


# C -- 浪光 Wave & Arc: a white arc of light rising over a wave, on a sunrise disc.
C_BG = stops((0, "#FFB13D"), (0.5, "#FF6C4C"), (1, "#DC376D"))
C_GLOW = stops((0, "#FFF0C4", 0.45), (1, "#FFF0C4", 0))
C_MARK = "#FFFFFF"


def wave_pts(x0, x1, y, amp, periods, phase=0.0, n=64):
    return [(x0 + (x1 - x0) * i / n,
             y - amp * math.sin(phase + 2 * math.pi * periods * i / n)) for i in range(n + 1)]


def concept_C(s):
    m, L = tile_box(s)
    U = lambda v: m + v * L
    c = m + L / 2
    tile = ("circle", c, c, L / 2)
    ops = [("clip", tile), ("fill", tile, ("lin", (U(0.2), U(0.02)), (U(0.8), U(0.98)), C_BG))]
    ops.append(("fill", tile, ("rad", (U(0.5), U(0.52)), 0.45 * L, C_GLOW)))
    if s <= 32:
        w = max(2, round(0.12 * L))
        acy = round(U(0.53))
        rc = round(0.24 * L * 2) / 2
        ops.append(("fill", ("sector", c, acy, rc - w / 2, rc + w / 2, 180, 360, "butt"), solid(C_MARK)))
        wy = acy + round(0.19 * L)
        amp = max(0.75, 0.04 * L)
        ops.append(("fill", ("stroke", wave_pts(U(0.25), U(0.75), wy, amp, 1.5), w, "round"), solid(C_MARK)))
    else:
        acy, rc, w = U(0.565), 0.215 * L, 0.09 * L
        ops.append(("fill", ("sector", c, acy, rc - w / 2, rc + w / 2, 180, 360, "round"), solid(C_MARK)))
        ops.append(("fill", ("stroke", wave_pts(U(0.24), U(0.76), U(0.725), 0.035 * L, 1.5), 0.075 * L, "round"),
                    solid(C_MARK)))
    return ops


# ==========================================================================
# INK-WASH CONCEPTS (水墨) -- black ink and greys on paper, no colour
# ==========================================================================
INK = "#141414"
PAPER_D = "#F1EBDD"     # warm xuan paper
PAPER_E = "#F7F6F1"     # brighter, cooler paper
PAPER_F = "#F3EFE5"     # the "white" of the negative glyph
INK_TILE = "#171717"
# Small sizes (checked by eye on light and dark):
#   >= 40 px  full ink treatment of the brush glyph
#   32 px     brush glyph, plain (no texture)
#   24 px     brush glyph, FreeType-hinted at the real pixel size, a little larger
#   16-20 px  the brush 島 turns to mush, so these use hinted JhengHei Bold
# JhengHei is a Microsoft font.  For an all-OFL set use FONT_NOTO_TC_BOLD instead
# (Noto Sans TC, SIL OFL, ships with Windows) -- it is softer/greyer at 16 px
# because FreeType gets no usable hinting from it.
FONT_NOTO_TC_BOLD = r"C:\Windows\Fonts\NotoSansTC-VF.ttf@700"
SMALL_GLYPH_FONT = FONT_CJK


def ink_glyph_ops(ch, s, L, cx, cy, big_h, style, seed, colour):
    if s >= 40:
        return [("fill", ("ink", ch, cx, cy, big_h, style, seed, FONT_KAI, L), solid(colour))]
    if s >= 32:
        return [("fill", ("ink", ch, cx, cy, 0.80 * L, "plain", seed, FONT_KAI, L), solid(colour))]
    if s >= 24:
        return [("fill", ("glyph", ch, round(cx), round(cy), 0.86 * L, True, FONT_KAI), solid(colour))]
    return [("fill", ("glyph", ch, round(cx), round(cy), 0.80 * L, True, SMALL_GLYPH_FONT), solid(colour))]


def tile_border(s, m, L, r):
    w = 1 if s <= 32 else max(1.0, 0.012 * L)
    return ("rring", m, m, m + L, m + L, r, w)


def concept_D(s):
    """濃墨暈染 wet ink: bold brush 島 with a grey bleed on warm xuan paper."""
    m, L = tile_box(s)
    U = lambda v: m + v * L
    r = corner(L)
    tile = ("rrect", m, m, m + L, m + L, r)
    ops = [("clip", tile), ("fill", tile, ("paper", PAPER_D, 11, 0.06) if s >= 40 else solid(PAPER_D))]
    cx, cy = U(0.5), U(0.5)
    if s >= 40:
        ops.append(("fill", ("ink", "島", cx, cy, 0.74 * L, "wet_halo", 21, FONT_KAI, L), solid("#7A746B"), 0.55))
    ops += ink_glyph_ops("島", s, L, cx, cy, 0.74 * L, "wet_core", 21, INK)
    ops.append(("fill", tile_border(s, m, L, r), solid("#3A3A3A", 1.0 if s <= 32 else 0.45)))
    return ops


def concept_E(s):
    """飛白 dry brush: calligraphic 島 with dry streaks over a pale ink-wash sea stroke."""
    m, L = tile_box(s)
    U = lambda v: m + v * L
    r = max(2, round(0.12 * L)) if L <= 64 else 0.12 * L
    tile = ("rrect", m, m, m + L, m + L, r)
    ops = [("clip", tile), ("fill", tile, ("paper", PAPER_E, 12, 0.05) if s >= 40 else solid(PAPER_E))]
    if s >= 40:
        ops.append(("fill", ("swash", U(0.04), U(0.98), U(0.75), 0.21 * L, 41, L), solid("#6A6A6A"), 0.42))
    else:
        ops.append(("fill", ("rect", m, round(U(0.70)), m + L, round(U(0.88))), solid("#C9C9C6")))
    ops += ink_glyph_ops("島", s, L, U(0.5), U(0.47), 0.78 * L, "dry", 33, INK)
    ops.append(("fill", tile_border(s, m, L, r), solid("#5A5A5A", 1.0 if s <= 32 else 0.4)))
    return ops


def concept_F(s):
    """墨底反白 negative: paper-white 島 on an ink-stone tile, grey brush circle behind it."""
    m, L = tile_box(s)
    U = lambda v: m + v * L
    r = corner(L)
    tile = ("rrect", m, m, m + L, m + L, r)
    ops = [("clip", tile), ("fill", tile, ("paper", INK_TILE, 5, 0.12) if s >= 40 else solid(INK_TILE))]
    if s >= 40:
        ops.append(("fill", ("enso", U(0.5), U(0.5), 0.37 * L, 0.075 * L, -60, 320, 51, L), solid("#8E8E8E"), 0.9))
    ops += ink_glyph_ops("島", s, L, U(0.5), U(0.5), 0.60 * L, "neg", 61, PAPER_F)
    # light rim so the black tile does not vanish on a dark taskbar
    ops.append(("fill", tile_border(s, m, L, r), solid("#A6A6A6", 1.0 if s <= 32 else 0.7)))
    return ops


CONCEPTS = {"A": concept_A, "B": concept_B, "C": concept_C,
            "D": concept_D, "E": concept_E, "F": concept_F}
CONCEPT_INFO = {
    "A": ("晨島 Dawn Isle", "island silhouette backlit by a rising sun; coral-amber sky, teal sea"),
    "B": ("島字 Glyph", "gold 島 glyph on deep indigo with a sunburst behind it"),
    "C": ("浪光 Wave & Arc", "white arc of light over a wave on a sunrise-gradient disc"),
    "D": ("濃墨 Wet Ink", "bold wet-brush 島 with grey ink bleed (暈染) on warm xuan paper, thin ink border"),
    "E": ("飛白 Dry Brush", "dry-brush 島 with white streaks along the strokes over a pale ink-wash sea stroke"),
    "F": ("反白 Negative", "paper-white 島 on an ink-stone tile inside a grey brush circle, light rim"),
}


# ==========================================================================
# MODE ICONS (tray / language bar) -- pixel-hinted, one palette per concept
# ==========================================================================
MODE_PAL = {
    "A": dict(bg=stops((0, "#17818F"), (1, "#0B4859")), fg="#FFFFFF", accent="#FFC95C",
              light="#FFF8EC", ink="#0D5A6B", shape="rrect", install_bg="#FFFFFF", install_fg="#0D5A6B"),
    "B": dict(bg=stops((0, "#373D9E"), (1, "#181B4C")), fg="#FFD266", accent="#FFD266",
              light="#F3F2FF", ink="#272C7E", shape="rrect", install_bg="#FFD266", install_fg="#1B1F55"),
    "C": dict(bg=stops((0, "#FF9440"), (1, "#E0406A")), fg="#FFFFFF", accent="#FFFFFF",
              light="#FFFFFF", ink="#E0445E", shape="circle", install_bg="#FFFFFF", install_fg="#E0445E"),
    # ink-wash sets: Chinese/full/half/reload share the "on" badge, English is the inverse.
    # Every badge has a contrasting border/rim so it reads on light *and* dark taskbars.
    # From 40 px up glyph edges are roughened and 中 is drawn with the brush (Kai) glyph.
    "D": dict(bg=stops((0, PAPER_D), (1, PAPER_D)), on_border="#3A3A3A", fg=INK, accent=INK,
              en_fill="#1A1A1A", en_border="#A6A6A6", en_fg=PAPER_D, shape="rrect",
              install_bg=INK, install_fg=PAPER_D, install_rim="#A6A6A6", inkstyle="wet_core"),
    "E": dict(bg=stops((0, PAPER_E), (1, PAPER_E)), on_border="#6A6A6A", fg=INK, accent=INK,
              en_fill="#5F5F5F", en_border=None, en_fg="#FFFFFF", shape="rrect", badge_k=0.12,
              install_bg="#5F5F5F", install_fg="#FFFFFF", inkstyle="dry"),
    "F": dict(bg=stops((0, INK_TILE), (1, INK_TILE)), on_border="#A6A6A6", fg=PAPER_F, accent=PAPER_F,
              en_fill=PAPER_F, en_border=INK_TILE, en_fg=INK_TILE, shape="rrect",
              install_bg=PAPER_F, install_fg=INK_TILE, install_rim=INK_TILE, inkstyle="neg"),
}


def badge(s, shape, inset=0, k=0.22):
    m = 0 if s <= 32 else round(s / 32)
    if shape == "circle":
        c = s / 2
        return ("circle", c, c, s / 2 - m - inset)
    r = (max(2, round((s - 2 * m) * k)) if s <= 64 else (s - 2 * m) * k) - inset
    return ("rrect", m + inset, m + inset, s - m - inset, s - m - inset, max(1, r))


def inkify(ops, P, s, seed=7):
    """Ragged brush edges for mode-icon glyphs at >= 40 px (ink-wash sets only)."""
    if not P.get("inkstyle") or s < 40:
        return ops
    return [("fill", ("rough", o[1], 0.34, 60, seed)) + tuple(o[2:]) for o in ops]


def stroke_w(s, k=0.11):
    return max(1, round(k * s))


def glyph_zhong(s, fg):
    """中 built from pixel-aligned rectangles."""
    w = stroke_w(s, 0.11)
    c = s / 2 if (s - w) % 2 == 0 else s / 2 + 0.5
    t = round(0.115 * s)
    bw = nearest_parity(0.64 * s, 0 if c == int(c) else 1)
    bh = max(2 * w + 2, round(0.40 * s))
    bx0 = c - bw / 2
    by0 = round((s - bh) / 2)
    bx1, by1 = bx0 + bw, by0 + bh
    p = solid(fg)
    return [
        ("fill", ("rect", bx0, by0, bx1, by0 + w), p),
        ("fill", ("rect", bx0, by1 - w, bx1, by1), p),
        ("fill", ("rect", bx0, by0, bx0 + w, by1), p),
        ("fill", ("rect", bx1 - w, by0, bx1, by1), p),
        ("fill", ("rect", c - w / 2, t, c + w / 2, s - t), p),
    ]


def glyph_A(s, fg, compact=False):
    w = stroke_w(s, 0.12)
    c = s / 2
    T = round((0.21 if compact else 0.17) * s)
    B = s - round((0.19 if compact else 0.15) * s)
    half = round((0.29 if compact else 0.33) * s)
    Lx, Rx = c - half, c + half
    leg = w * 1.18                       # horizontal thickness of a slanted stem
    hw = leg / 2                         # half width of the flat apex
    # inner apex: where the inner edges of the two legs meet
    t_in = (c - (Lx + leg)) / ((c - hw) - Lx)
    yin = B + t_in * (T - B)
    poly = [(Lx, B), (c - hw, T), (c + hw, T), (Rx, B), (Rx - leg, B), (c, yin), (Lx + leg, B)]
    cb0 = round(T + 0.60 * (B - T))
    cb1 = cb0 + max(1, round(w * 0.9))

    def xin(y):  # x of left inner edge at y
        return Lx + leg + (y - B) / (T - B) * ((c - hw) - Lx)

    p = solid(fg)
    return [("fill", ("poly", poly), p),
            ("fill", ("rect", xin(cb1) - 0.5, cb0, 2 * c - xin(cb1) + 0.5, cb1), p)]


def disc_d(s, shape="rrect"):
    # inside a round badge a big disc reads as a ring ("O"), so keep it smaller there
    return nearest_parity((0.52 if shape == "circle" else 0.6) * s, s % 2, lo=4)


def mode_ops(X, kind, s):
    """Op list for one tray/language-bar icon (installer is composed in icon_image)."""
    P = MODE_PAL[X]
    c = s / 2
    if kind == "weasel":
        return CONCEPTS[X](s)
    k = P.get("badge_k", 0.22)
    bg = badge(s, P["shape"], 0, k)
    bw = 1 if s <= 32 else max(1, round(s / 24))
    if kind == "en":
        fill, brd, fg = P.get("en_fill", P.get("light")), P.get("en_border", P.get("ink")), P.get("en_fg", P.get("ink"))
        if brd:
            ops = [("fill", bg, solid(brd)), ("fill", badge(s, P["shape"], bw, k), solid(fill))]
        else:
            ops = [("fill", bg, solid(fill))]
        return ops + inkify(glyph_A(s, fg, compact=P["shape"] == "circle"), P, s)
    if P.get("on_border"):
        ops = [("fill", bg, solid(P["on_border"])),
               ("fill", badge(s, P["shape"], bw, k), ("lin", (0, 0), (0, s), P["bg"]))]
    else:
        ops = [("fill", bg, ("lin", (0, 0), (0, s), P["bg"]))]
    if kind == "zh":
        if P.get("inkstyle") and s >= 40:
            return ops + [("fill", ("ink", "中", c, c, 0.68 * s, P["inkstyle"], 71, FONT_KAI, s), solid(P["fg"]))]
        return ops + glyph_zhong(s, P["fg"])
    if kind == "full":
        d = disc_d(s, P["shape"])
        return ops + inkify([("fill", ("circle", c, c, d / 2), solid(P["accent"]))], P, s)
    if kind == "half":
        d = disc_d(s, P["shape"])
        rw = max(1, round(0.085 * s))
        return ops + inkify([("fill", ("sector", c, c, d / 2 - rw, d / 2, 0, 360, "butt"), solid(P["accent"])),
                             ("fill", ("sector", c, c, 0, d / 2, 90, 270, "butt"), solid(P["accent"]))], P, s)
    if kind == "reload":
        w = max(2, round(0.12 * s))
        r = round(0.28 * s * 2) / 2
        a0, a1 = -20, 270
        h = w * 1.3
        tip = 1.25 * h
        ty = c - r
        return ops + inkify([
            ("fill", ("sector", c, c, r - w / 2, r + w / 2, a0, a1, "butt"), solid(P["fg"])),
            ("fill", ("poly", [(c, ty - h), (c + tip, ty), (c, ty + h)]), solid(P["fg"])),
        ], P, s)
    raise ValueError(kind)


def icon_image(X, kind, s):
    if kind == "installer":
        return installer_image(X, s)
    return Raster(s).run(mode_ops(X, kind, s)).image()


def installer_image(X, s):
    """App tile shrunk into the top-left, 'install' badge in the free lower-right
    corner, with a transparent gap cut around the badge."""
    t = round(0.80 * s)
    base = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    base.paste(render(CONCEPTS[X], t), (0, 0))
    ops, (bx, by, bd, gap) = installer_badge_ops(X, s)
    cut = Raster(s).run([("fill", ("circle", bx, by, bd / 2 + gap), solid("#000000"))]).image().getchannel("A")
    a = np.asarray(base.getchannel("A"), np.float32) * (1 - np.asarray(cut, np.float32) / 255.0)
    base.putalpha(Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8)))
    return Image.alpha_composite(base, Raster(s).run(ops).image())


def installer_badge_ops(X, s):
    P = MODE_PAL[X]
    bd = max(8, round(0.48 * s)) if s <= 32 else 0.46 * s
    gap = 1 if s <= 32 else 0.03 * s
    bx = s - bd / 2
    by = s - bd / 2
    if P.get("install_rim"):   # contrasting rim so a black/white badge shows on any background
        rim = 1 if s <= 32 else 0.035 * s
        ops = [("fill", ("circle", bx, by, bd / 2), solid(P["install_rim"])),
               ("fill", ("circle", bx, by, bd / 2 - rim), solid(P["install_bg"]))]
    else:
        ops = [("fill", ("circle", bx, by, bd / 2), solid(P["install_bg"]))]
    fg = solid(P["install_fg"])
    if s <= 32:
        sw = 2 if bd < 12 else max(2, round(bd * 0.18))
        hx = bd * 0.30
        top = by - bd * 0.30
        hb = by - bd * 0.02
        tipy = by + bd * 0.28
        ops.append(("fill", ("rect", bx - sw / 2, round(top), bx + sw / 2, hb + 0.5), fg))
        ops.append(("fill", ("poly", [(bx - hx, hb), (bx + hx, hb), (bx, tipy)]), fg))
    else:
        sw = bd * 0.14
        ops.append(("fill", ("rect", bx - sw / 2, by - bd * 0.28, bx + sw / 2, by + bd * 0.02), fg))
        ops.append(("fill", ("poly", [(bx - bd * 0.24, by - bd * 0.04), (bx + bd * 0.24, by - bd * 0.04),
                                       (bx, by + bd * 0.22)]), fg))
        ops.append(("fill", ("rrect", bx - bd * 0.26, by + bd * 0.27, bx + bd * 0.26, by + bd * 0.27 + sw, sw / 2), fg))
    return ops, (bx, by, bd, gap)


# ==========================================================================
# ICO writer: 32-bit BMP (with AND mask) below 256 px, PNG at 256 px
# ==========================================================================
def _dib(im):
    s = im.size[0]
    arr = np.asarray(im.convert("RGBA"))
    hdr = struct.pack("<IiiHHIIiiII", 40, s, s * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pix = arr[::-1, :, [2, 1, 0, 3]].tobytes()
    row = ((s + 31) // 32) * 4
    andm = bytearray()
    for y in range(s - 1, -1, -1):
        bits = np.packbits(arr[y, :, 3] == 0).tobytes()
        andm += bits + b"\0" * (row - len(bits))
    return hdr + pix + bytes(andm)


def write_ico(path, images):
    entries = []
    for s in sorted(images):
        im = images[s]
        if s >= 256:
            bio = io.BytesIO()
            im.save(bio, "PNG", optimize=True)
            data = bio.getvalue()
        else:
            data = _dib(im)
        entries.append((s, data))
    out = struct.pack("<HHH", 0, 1, len(entries))
    off = 6 + 16 * len(entries)
    for s, data in entries:
        d = 0 if s >= 256 else s
        out += struct.pack("<BBBBHHII", d, d, 0, 0, 1, 32, len(data), off)
        off += len(data)
    with open(path, "wb") as f:
        f.write(out + b"".join(d for _, d in entries))


# ==========================================================================
# preview sheets
# ==========================================================================
def _txt(size, bold=False):
    return _font(FONT_CJK if bold else FONT_TXT, size, 0)


LIGHT_BG, DARK_BG = "#F3F3F3", "#1E1E1E"


def concepts_preview(path, letters="ABC", title="島輝 DaoHui — icon concepts"):
    sizes = [256, 48, 32, 24, 16]
    zooms = [(16, 4), (24, 4)]
    R = {X: {s: render(CONCEPTS[X], s) for s in set(sizes) | {z for z, _ in zooms}} for X in letters}
    pad, gap = 24, 22
    panel_w = pad + sum(sizes) + gap * (len(sizes) - 1) + 34 + sum(z * f for z, f in zooms) + gap + pad
    row_h = 256 + 2 * pad + 28
    label_w = 250
    W = label_w + 2 * panel_w + 16 + 24
    H = 96 + len(letters) * (row_h + 16) + 8
    sheet = Image.new("RGB", (W, H), "#E4E4E8")
    d = ImageDraw.Draw(sheet)
    d.text((24, 22), title, font=_txt(30, True), fill="#202024")
    d.text((24, 62), "each concept at 256 / 48 / 32 / 24 / 16 px (actual pixels) plus 16 and 24 px magnified x4, "
                     "on light and dark (taskbar) backgrounds", font=_txt(15), fill="#55555C")
    for r, X in enumerate(letters):
        y = 96 + r * (row_h + 16)
        name, desc = CONCEPT_INFO[X]
        d.text((24, y + 18), X, font=_txt(64, True), fill="#202024")
        d.text((24, y + 104), name, font=_txt(20, True), fill="#202024")
        words, line, ly = desc.split(" "), "", y + 136
        for wd in words:
            if d.textlength(line + wd, font=_txt(14)) > label_w - 40:
                d.text((24, ly), line, font=_txt(14), fill="#55555C")
                ly += 20
                line = ""
            line += wd + " "
        d.text((24, ly), line, font=_txt(14), fill="#55555C")
        for p, (bg, fg) in enumerate([(LIGHT_BG, "#444"), (DARK_BG, "#BBB")]):
            x0 = label_w + p * (panel_w + 16)
            d.rounded_rectangle([x0, y, x0 + panel_w, y + row_h], 14, fill=bg)
            base = y + pad + 256
            x = x0 + pad
            for s in sizes:
                im = R[X][s]
                sheet.paste(im, (x, base - s), im)
                d.text((x + s / 2, base + 8), "%d" % s, font=_txt(13), fill=fg, anchor="mt")
                x += s + gap
            x += 12
            for z, f in zooms:
                im = R[X][z].resize((z * f, z * f), Image.NEAREST)
                sheet.paste(im, (x, base - z * f), im)
                d.text((x + z * f / 2, base + 8), "%d px ×%d" % (z, f), font=_txt(13), fill=fg, anchor="mt")
                x += z * f + gap
    sheet.save(path)
    return path


def modes_preview(path, letters="ABC", title="島輝 DaoHui — mode icons (tray / language bar)"):
    kinds = ICON_KINDS
    labels = {"weasel": "app", "zh": "zh", "en": "en", "full": "full", "half": "half",
              "reload": "reload", "installer": "installer"}
    sizes = [16, 20, 24, 32]
    zf = 4
    R = {X: {k: {s: icon_image(X, k, s) for s in sizes} for k in kinds} for X in letters}
    pad, gap, lab = 16, 16, 78
    cell_h = 24 * zf + 14
    panel_w = pad + lab + sum(sizes) + gap * len(sizes) + 16 * zf + 24 * zf + gap + pad
    panel_h = pad + 26 + len(kinds) * cell_h + pad
    block_w = 2 * panel_w + 12
    W = 24 + len(letters) * (block_w + 24)
    H = 92 + 40 + panel_h + 24
    sheet = Image.new("RGB", (W, H), "#E4E4E8")
    d = ImageDraw.Draw(sheet)
    d.text((24, 20), title, font=_txt(30, True), fill="#202024")
    d.text((24, 60), "rows: app, Chinese, English (ASCII), full-width, half-width, reload/deploying, installer · "
                     "columns: 16 / 20 / 24 / 32 px actual size, then 16 and 24 px magnified x4",
           font=_txt(15), fill="#55555C")
    for b, X in enumerate(letters):
        bx = 24 + b * (block_w + 24)
        by = 92
        d.text((bx, by), "%s  %s" % (X, CONCEPT_INFO[X][0]), font=_txt(22, True), fill="#202024")
        for p, (bg, fg) in enumerate([(LIGHT_BG, "#444"), (DARK_BG, "#BBB")]):
            x0 = bx + p * (panel_w + 12)
            y0 = by + 40
            d.rounded_rectangle([x0, y0, x0 + panel_w, y0 + panel_h], 12, fill=bg)
            x = x0 + pad + lab
            for s in sizes:
                d.text((x + s / 2, y0 + pad), str(s), font=_txt(12), fill=fg, anchor="mt")
                x += s + gap
            d.text((x + 16 * zf / 2, y0 + pad), "16 ×4", font=_txt(12), fill=fg, anchor="mt")
            d.text((x + 16 * zf + gap + 24 * zf / 2, y0 + pad), "24 ×4", font=_txt(12), fill=fg, anchor="mt")
            for r, k in enumerate(kinds):
                cy = y0 + pad + 26 + r * cell_h + 24 * zf // 2
                d.text((x0 + pad, cy), labels[k], font=_txt(13), fill=fg, anchor="lm")
                x = x0 + pad + lab
                for s in sizes:
                    im = R[X][k][s]
                    sheet.paste(im, (x, int(cy - s / 2)), im)
                    x += s + gap
                im = R[X][k][16].resize((16 * zf, 16 * zf), Image.NEAREST)
                sheet.paste(im, (x, cy - 8 * zf), im)
                x += 16 * zf + gap
                im = R[X][k][24].resize((24 * zf, 24 * zf), Image.NEAREST)
                sheet.paste(im, (x, cy - 12 * zf), im)
    sheet.save(path)
    return path


def final_preview(path, X):
    """Chosen concept: every icon at 16/20/24/32/48/256 (actual pixels) + 16/24 x4, light and dark."""
    kinds = ICON_KINDS
    labels = {"weasel": "app\nweasel.ico", "zh": "Chinese\nzh.ico", "en": "English\nen.ico",
              "full": "full-width\nfull.ico", "half": "half-width\nhalf.ico", "reload": "deploying\nreload.ico",
              "installer": "installer\ninstaller.ico"}
    sizes = [16, 20, 24, 32, 48, 256]
    zooms = [(16, 4), (24, 4)]
    R = {k: {s: icon_image(X, k, s) for s in sorted(set(sizes) | {z for z, _ in zooms})} for k in kinds}
    pad, gap, lab = 20, 18, 110
    row_h = 256 + 26
    panel_w = pad + lab + sum(sizes) + gap * len(sizes) + sum(z * f for z, f in zooms) + gap + pad
    W = 24 + 2 * panel_w + 16 + 24
    H = 100 + 30 + len(kinds) * row_h + 2 * pad + 24
    sheet = Image.new("RGB", (W, H), "#E4E4E8")
    d = ImageDraw.Draw(sheet)
    d.text((24, 20), "島輝 DaoHui — final icon set: %s %s" % (X, CONCEPT_INFO[X][0]), font=_txt(30, True), fill="#202024")
    d.text((24, 62), "brush glyph: Yuji Syuku (SIL OFL 1.1); 16-20 px use hinted JhengHei Bold for legibility · "
                     "actual pixels, plus 16 and 24 px magnified x4", font=_txt(15), fill="#55555C")
    for p, (bg, fg) in enumerate([(LIGHT_BG, "#444"), (DARK_BG, "#BBB")]):
        x0 = 24 + p * (panel_w + 16)
        y0 = 100
        d.rounded_rectangle([x0, y0, x0 + panel_w, y0 + 30 + len(kinds) * row_h + 2 * pad], 14, fill=bg)
        x = x0 + pad + lab
        for s in sizes:
            d.text((x + s / 2, y0 + pad), str(s), font=_txt(13), fill=fg, anchor="mt")
            x += s + gap
        for z, f in zooms:
            d.text((x + z * f / 2, y0 + pad), "%d ×%d" % (z, f), font=_txt(13), fill=fg, anchor="mt")
            x += z * f + gap
        for r, k in enumerate(kinds):
            base = y0 + pad + 30 + r * row_h + 256    # bottom-aligned
            d.multiline_text((x0 + pad, base - 128), labels[k], font=_txt(13), fill=fg, anchor="lm", spacing=4)
            x = x0 + pad + lab
            for s in sizes:
                im = R[k][s]
                sheet.paste(im, (x, base - s), im)
                x += s + gap
            for z, f in zooms:
                im = R[k][z].resize((z * f, z * f), Image.NEAREST)
                sheet.paste(im, (x, base - z * f), im)
                x += z * f + gap
    sheet.save(path)
    return path


def tauri_icons(X, od):
    """Tauri settings-app icons: icons/32x32.png, 128x128.png, 128x128@2x.png, icon.ico, icon.icns."""
    os.makedirs(od, exist_ok=True)
    app = lambda s: render(CONCEPTS[X], s)
    app(32).save(os.path.join(od, "32x32.png"))
    app(128).save(os.path.join(od, "128x128.png"))
    app(256).save(os.path.join(od, "128x128@2x.png"))
    write_ico(os.path.join(od, "icon.ico"), {s: app(s) for s in ICO_SIZES})
    big = Raster(1024, ss=2).run(CONCEPTS[X](1024)).image()
    big.save(os.path.join(od, "icon.icns"), "ICNS", append_images=[app(s) for s in (32, 64, 128, 256, 512)])
    return od


# ==========================================================================
# commands
# ==========================================================================
# preview sheets: (concept sheet, mode sheet, letters, title)
SHEETS = [
    ("concepts_preview.png", "mode_icons_preview.png", "ABC", "島輝 DaoHui"),
    ("inkwash_preview.png", "inkwash_mode_icons_preview.png", "DEF", "島輝 DaoHui · 水墨 ink-wash"),
]


def cmd_concepts(letters):
    od = os.path.join(HERE, "concepts")
    os.makedirs(od, exist_ok=True)
    for X in letters:
        fn = CONCEPTS[X]
        with open(os.path.join(od, "%s.svg" % X), "w", encoding="utf-8") as f:
            f.write(SVG(512).run(fn(512)).text())
        render(fn, 512).save(os.path.join(od, "%s-512.png" % X))
        print("concepts/%s.svg, concepts/%s-512.png" % (X, X))
    for sheet, _, group, title in SHEETS:
        if set(group) & set(letters):
            print(concepts_preview(os.path.join(HERE, sheet), group, title + " — icon concepts"))


def cmd_modes(letters):
    for _, sheet, group, title in SHEETS:
        if set(group) & set(letters):
            print(modes_preview(os.path.join(HERE, sheet), group, title + " — mode icons (tray / language bar)"))


def cmd_export(letters):
    for X in letters:
        od = os.path.join(HERE, "out", X)
        os.makedirs(od, exist_ok=True)
        for k in ICON_KINDS:
            imgs = {s: icon_image(X, k, s) for s in ICO_SIZES}
            write_ico(os.path.join(od, "%s.ico" % k), imgs)
        for s in (256, 512):
            render(CONCEPTS[X], s).save(os.path.join(od, "app-%d.png" % s))
        print("out/%s: %s + app-256.png, app-512.png" % (X, ", ".join(k + ".ico" for k in ICON_KINDS)))


def cmd_final(X):
    """Everything for the chosen concept, without touching the other concepts' sheets."""
    od = os.path.join(HERE, "concepts")
    with open(os.path.join(od, "%s.svg" % X), "w", encoding="utf-8") as f:
        f.write(SVG(512).run(CONCEPTS[X](512)).text())
    render(CONCEPTS[X], 512).save(os.path.join(od, "%s-512.png" % X))
    print("concepts/%s.svg, concepts/%s-512.png" % (X, X))
    cmd_export([X])
    print(tauri_icons(X, os.path.join(HERE, "out", X, "tauri", "icons")))
    print(final_preview(os.path.join(HERE, "final_%s_preview.png" % X), X))


def cmd_verify():
    ok = True
    root = os.path.join(HERE, "out")
    icos = []
    for X in sorted(os.listdir(root)):
        for fn in sorted(os.listdir(os.path.join(root, X))):
            if fn.endswith(".ico"):
                icos.append((X, fn, os.path.join(root, X, fn)))
        tdir = os.path.join(root, X, "tauri", "icons")
        if os.path.isdir(tdir):
            icos.append((X, "tauri/icon.ico", os.path.join(tdir, "icon.ico")))
            ic = Image.open(os.path.join(tdir, "icon.icns"))
            print("ok   %s/tauri/icon.icns sizes %s" % (X, sorted(ic.info["sizes"])))
    for X, fn, p in icos:
        im = Image.open(p)
        sizes = sorted(s[0] for s in im.info["sizes"])
        bad = []
        for s in sizes:
            fr = im.ico.getimage((s, s))
            if fr.size != (s, s) or fr.mode != "RGBA" or fr.getextrema()[3][1] == 0:
                bad.append(s)
        good = sizes == ICO_SIZES and not bad
        ok &= good
        print("%-4s %-16s %s %s" % ("ok" if good else "FAIL", "%s/%s" % (X, fn), sizes, bad or ""))
    return ok


if __name__ == "__main__":
    args = sys.argv[1:] or ["all"]
    letters = [a.upper() for a in args[1:]] or list(CONCEPTS)
    if args[0] == "concepts":
        cmd_concepts(letters)
    elif args[0] == "modes":
        cmd_modes(letters)
    elif args[0] == "export":
        cmd_export(letters)
    elif args[0] == "verify":
        sys.exit(0 if cmd_verify() else 1)
    elif args[0] == "final":
        for X in letters if args[1:] else ["F"]:
            cmd_final(X)
        sys.exit(0 if cmd_verify() else 1)
    elif args[0] == "all":
        cmd_concepts(letters)
        cmd_modes(letters)
        cmd_export(letters)
        sys.exit(0 if cmd_verify() else 1)
    else:
        print(__doc__)
        sys.exit(2)

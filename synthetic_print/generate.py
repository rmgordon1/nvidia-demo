#!/usr/bin/env python3
"""Synthetic press-sheet generator for print-defect detection.

Renders full press sheets (a step-and-repeat grid of "notes") in two styles --
fictional engraved banknotes and generic press QA test targets -- injects labeled
print defects, simulates an inline inspection camera, and writes:

  * a YOLO detection dataset   (images/, labels/, data.yaml)
  * an MVTec-AD style layout    (mvtec/<design>/train/good, test/<defect>, ground_truth/)
  * annotations.json            (per-sheet design, split, defect class, bbox, parameters)

No real currency designs are used: every note is an invented design that carries
a "SYNTHETIC SPECIMEN" legend, and test-target sheets are generic press targets.

Rendering model: each ink is a coverage layer in [0, 1]; layers are composited
subtractively onto the substrate. Defects act on that model (missing coverage,
shifted layers, altered ink colour, extra ink, post-print damage), so the
pixel mask of every defect is known exactly.

    python3 generate.py --count 300                 # default sample
    python3 generate.py --count 3000 --defect-rate 0.3 --capture strong --overwrite
"""
import argparse
import colorsys
import json
import os
import shutil
import time
from multiprocessing import Pool

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
TAU = 2 * np.pi

CLASSES = ["ink_void", "streak", "smear", "misregistration", "color_shift", "crease", "tear", "contamination"]
DIFF_THR = 0.04  # min per-pixel RGB change (0..1) counted as part of a defect
MIN_DEFECT_PX = 15  # placements smaller than this are retried
CLOSE_K = {"misregistration": 9, "color_shift": 9, "smear": 5, "ink_void": 3, "streak": 3, "contamination": 3}

CAPTURE = {
    "none": dict(rot=0, persp=0, shift=0, scale=0, light=0, vignette=0, blur=(0, 0), noise=(0, 0), jpeg=(95, 95)),
    "mild": dict(rot=1.2, persp=0.003, shift=0.008, scale=0.01, light=0.05, vignette=0.06,
                 blur=(0.3, 0.8), noise=(1.0, 2.5), jpeg=(90, 96)),
    "strong": dict(rot=3.5, persp=0.01, shift=0.015, scale=0.03, light=0.15, vignette=0.15,
                   blur=(0.5, 1.5), noise=(2.0, 6.0), jpeg=(70, 90)),
}

SCREEN_ANGLE = {"cyan": 15, "magenta": 75, "yellow": 0, "black": 45}


def font(size, name="DejaVuSans.ttf"):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, name), max(4, int(round(size))))
    except OSError:
        return ImageFont.load_default()


def hsv(h, s, v):
    return np.array(colorsys.hsv_to_rgb(h % 1.0, min(max(s, 0), 1), min(max(v, 0), 1)), np.float32)


def _json_default(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(type(o))


# --------------------------------------------------------------------------- geometry


class Geometry:
    """Sheet layout: cols x rows notes with margins and gutters; sheet is ~93% of output width."""

    def __init__(self, width, cols, rows, note_aspect=2.25):
        self.out_w = width
        self.sw = int(round(width * 0.93))
        self.margin = int(round(self.sw * 0.03))
        self.gutter = int(round(self.sw * 0.012))
        self.cols, self.rows = cols, rows
        self.nw = (self.sw - 2 * self.margin - (cols - 1) * self.gutter) // cols
        self.nh = int(round(self.nw / note_aspect))
        self.sh = 2 * self.margin + rows * self.nh + (rows - 1) * self.gutter
        self.out_h = self.sh + (width - self.sw)
        self.origins = [(self.margin + c * (self.nw + self.gutter), self.margin + r * (self.nh + self.gutter))
                        for r in range(rows) for c in range(cols)]

    def cells_rect(self, r0, c0, nr, nc):
        x0, y0 = self.origins[r0 * self.cols + c0]
        x1 = x0 + nc * self.nw + (nc - 1) * self.gutter
        y1 = y0 + nr * self.nh + (nr - 1) * self.gutter
        g = self.gutter // 2
        return max(0, x0 - g), max(0, y0 - g), min(self.sw, x1 + g), min(self.sh, y1 + g)


# --------------------------------------------------------------------------- plate drawing


class Plate:
    """Supersampled single-ink drawing surface in note coordinates."""

    def __init__(self, w, h, ss=4):
        self.w, self.h, self.ss = w, h, ss
        self.img = np.zeros((h * ss, w * ss), np.uint8)

    def _pts(self, xs, ys):
        return np.round(np.stack([np.asarray(xs, float), np.asarray(ys, float)], -1) * self.ss * 16).astype(np.int32)

    def line(self, xs, ys, width=0.6, closed=False):
        cv2.polylines(self.img, [self._pts(xs, ys)], closed, 255,
                      thickness=max(1, int(round(width * self.ss))), lineType=cv2.LINE_AA, shift=4)

    def circle(self, cx, cy, r, width=0.6):
        t = np.linspace(0, TAU, max(64, int(r * 8)))
        self.line(cx + r * np.cos(t), cy + r * np.sin(t), width, closed=True)

    def fill(self, xs, ys):
        cv2.fillPoly(self.img, [self._pts(xs, ys)], 255, lineType=cv2.LINE_AA, shift=4)

    def rect(self, x0, y0, x1, y1):
        self.fill([x0, x1, x1, x0], [y0, y0, y1, y1])

    def text(self, x, y, s, size, name="DejaVuSans.ttf", anchor="mm", value=255):
        im = Image.fromarray(self.img)
        ImageDraw.Draw(im).text((x * self.ss, y * self.ss), s, fill=value, font=font(size * self.ss, name), anchor=anchor)
        self.img = np.array(im)

    def disc(self, cx, cy, r):
        t = np.linspace(0, TAU, max(64, int(r * 8)))
        self.fill(cx + r * np.cos(t), cy + r * np.sin(t))

    def ellipse(self, cx, cy, rx, ry, width=0.6):
        t = np.linspace(0, TAU, max(96, int((rx + ry) * 6)))
        self.line(cx + rx * np.cos(t), cy + ry * np.sin(t), width, closed=True)

    def clear_ellipse(self, cx, cy, rx, ry):
        s = self.ss
        yy, xx = np.mgrid[0:self.h * s, 0:self.w * s].astype(np.float32) / s
        self.img[((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 < 1] = 0

    def halftone(self, x0, y0, x1, y1, tone, angle, pitch=3.4):
        """Amplitude-modulated dot screen; tone is a scalar or callable(u in [0,1] across x)."""
        s = self.ss
        X0, Y0, X1, Y1 = int(x0 * s), int(y0 * s), int(x1 * s), int(y1 * s)
        yy, xx = np.mgrid[Y0:Y1, X0:X1].astype(np.float32)
        a, k = np.deg2rad(angle), TAU / (pitch * s)
        u = (xx * np.cos(a) + yy * np.sin(a)) * k
        v = (-xx * np.sin(a) + yy * np.cos(a)) * k
        scr = (np.cos(u) + np.cos(v)) / 2
        t = tone((xx - X0) / max(1, X1 - X0)) if callable(tone) else np.full_like(xx, tone)
        dots = np.where(scr > 1 - 2 * t, 255, 0).astype(np.uint8)
        region = self.img[Y0:Y1, X0:X1]
        np.maximum(region, dots, out=region)

    def clip_rect(self, x0, y0, x1, y1):
        s = self.ss
        keep = np.zeros_like(self.img)
        keep[int(y0 * s):int(y1 * s), int(x0 * s):int(x1 * s)] = 1
        self.img *= keep

    def coverage(self):
        return cv2.resize(self.img, (self.w, self.h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0


class Design:
    def __init__(self, name, kind, layers, serial_color, serial_spots, serial_size, substrate, side="front"):
        self.name, self.kind, self.side = name, kind, side
        self.layers = layers  # [(ink name, rgb colour, note-size coverage)]
        self.serial_color, self.serial_spots, self.serial_size = serial_color, serial_spots, serial_size
        self.substrate = substrate
        self.serial_prefix = "".join(w[0] for w in name.upper().split("_")[:1]) + chr(65 + sum(map(ord, name)) % 26)


ISSUERS = ["BANK OF NORTHMARCH", "REPUBLIC OF VALDORIA", "BANK OF ESTOVIA", "STATE OF KARVENNE",
           "BANK OF ARDENMOOR", "COMMONWEALTH OF ILSE"]
UNITS = ["SOLEN", "KRONAR", "DUCATS", "MARKEN", "VERAS"]
WORDS = {5: "FIVE", 10: "TEN", 20: "TWENTY", 50: "FIFTY", 100: "ONE HUNDRED"}
INTAGLIO = [(0.12, 0.16, 0.36), (0.28, 0.18, 0.11), (0.24, 0.13, 0.30), (0.16, 0.19, 0.27), (0.32, 0.09, 0.12)]
SEAL = [(0.75, 0.12, 0.15), (0.10, 0.35, 0.65), (0.10, 0.45, 0.35), (0.55, 0.20, 0.50)]
OVI = [(0.55, 0.40, 0.15), (0.20, 0.45, 0.35), (0.45, 0.25, 0.40)]


def engraved_portrait(P, cx, cy, rx, ry, rng, pitch=1.15):
    """Procedural line-engraved bust (generic, not a real person): line width follows a shaded tone map."""
    s = P.ss
    X0, Y0 = max(0, int((cx - rx) * s)), max(0, int((cy - ry) * s))
    X1, Y1 = min(P.w * s, int((cx + rx) * s) + 1), min(P.h * s, int((cy + ry) * s) + 1)
    yy, xx = np.mgrid[Y0:Y1, X0:X1].astype(np.float32) / s
    oval = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 < 1
    hcx, hcy = cx + rng.uniform(-0.05, 0.05) * rx, cy - ry * 0.16
    hx, hy = rx * 0.47, ry * 0.4
    u, v = (xx - hcx) / hx, (yy - hcy) / hy
    r2 = u * u + v * v

    T = 0.3 + 0.25 * np.clip((yy - cy) / ry + 1, 0, 2) / 2          # background, darker toward the bottom
    phase = yy.copy()
    coat = ((xx - hcx) / (rx * 1.05)) ** 2 + ((yy - (cy + ry * 0.95)) / (ry * 0.55)) ** 2 < 1
    T = np.where(coat, 0.68 + 0.06 * np.sin(xx * 0.4), T)
    phase = np.where(coat, xx * 0.55 + yy, phase)
    collar = hcy + hy * 1.2
    shirt = coat & (np.abs(xx - hcx) < (yy - collar) * 0.38)
    tie = shirt & (np.abs(xx - hcx) < hx * 0.1 + (yy - collar) * 0.06) & (yy > collar + hy * 0.15)
    T = np.where(shirt, 0.08, T)
    T = np.where(tie, 0.62, T)
    neck = (np.abs(xx - hcx) < hx * 0.48) & (yy > hcy) & (yy < hcy + hy * 1.75) & ~shirt
    T = np.where(neck, 0.42 + 0.25 * np.exp(-((yy - hcy - hy) / (hy * 0.25)) ** 2), T)
    phase = np.where(neck, yy + 0.4 * hx * ((xx - hcx) / hx) ** 2, phase)

    z = np.sqrt(np.clip(1 - r2, 0, 1))
    lam = np.clip(-0.45 * u - 0.35 * v + 0.82 * z, 0, 1)
    side = rng.choice([-1, 1])

    def g(du, dv, su, sv):
        return np.exp(-(((u - du) / su) ** 2 + ((v - dv) / sv) ** 2))

    face = (0.04 + 0.42 * (1 - lam) ** 1.3
            + 0.38 * (g(-0.36, -0.05, 0.14, 0.06) + g(0.36, -0.05, 0.14, 0.06))
            + 0.3 * (g(-0.36, -0.22, 0.18, 0.035) + g(0.36, -0.22, 0.18, 0.035))
            + 0.22 * g(0.09 * side, 0.2, 0.06, 0.2) + 0.2 * g(0, 0.4, 0.14, 0.035)
            + 0.3 * g(0, 0.62, 0.22, 0.035) + 0.15 * g(0, 0.95, 0.5, 0.08))
    head = r2 < 1
    T = np.where(head, face, T)
    phase = np.where(head, yy + 0.3 * hx * u ** 2, phase)
    ph = rng.uniform(0, TAU)
    hair = (r2 < 1.12 ** 2) & ((v < -0.5 + 0.1 * np.sin(u * 5 + ph)) | ((np.abs(u) > 0.9) & (v < -0.05)))
    T = np.where(hair, 0.52 + 0.15 * np.sin(u * 9 + v * 3), T)
    phase = np.where(hair, xx * 0.8 + yy * 0.35 + 0.8 * np.sin(u * 4), phase)

    T = np.clip(T, 0, 0.95)
    f = (phase / pitch) % 1.0
    ink = np.abs(f - 0.5) < T * 0.5
    f2 = ((xx * 0.87 - yy * 0.5) / pitch) % 1.0
    ink |= (T > 0.55) & (np.abs(f2 - 0.5) < (T - 0.55) * 0.9)
    region = P.img[Y0:Y1, X0:X1]
    np.maximum(region, np.where(ink & oval, 255, 0).astype(np.uint8), out=region)


def signature(P, x0, y0, w, h, rng):
    t = np.linspace(0, 1, 500)
    x = x0 - w / 2 + w * t + h * 0.5 * np.sin(TAU * t * rng.uniform(4, 7) + rng.uniform(0, TAU))
    y = y0 + h * (0.45 * np.sin(TAU * t * rng.uniform(5, 9)) * np.sin(np.pi * t) ** 0.5
                  + 0.2 * np.sin(TAU * t * rng.uniform(1, 2) + rng.uniform(0, TAU)))
    P.line(x, y, 0.5)
    P.line([x0 - w / 2, x0 + w / 2], [y0 + h * 0.85] * 2, 0.35)


def build_banknote(name, rng, nw, nh):
    """Fictional classic engraved banknote: split-fountain lathe underprint, intaglio portrait and frame,
    coloured seal and serials, colour-shifting numeral, watermark window and windowed security thread."""
    left = rng.uniform() < 0.5

    def X(x):  # layout is written portrait-left; mirrored for the other variant
        return x if left else nw - x

    issuer, unit = str(rng.choice(ISSUERS)), str(rng.choice(UNITS))
    denom = int(rng.choice(list(WORDS)))
    dark = np.array(INTAGLIO[rng.integers(len(INTAGLIO))], np.float32)
    seal_c = np.array(SEAL[rng.integers(len(SEAL))], np.float32)
    ovi_c = np.array(OVI[rng.integers(len(OVI))], np.float32)
    h_dark = colorsys.rgb_to_hsv(*dark)[0]
    h1, h2 = h_dark + rng.uniform(-0.1, 0.1), h_dark + rng.choice([-1, 1]) * rng.uniform(0.15, 0.35)
    h1, h2 = [(h % 1.0) + (0.3 if 0.18 < h % 1.0 < 0.48 else 0.0) for h in (h1, h2)]  # keep tints out of greens
    substrate = hsv(rng.uniform(0.08, 0.16), rng.uniform(0.04, 0.09), 0.95)

    inset, bw = nh * 0.03, nh * 0.075
    b = inset + bw
    pcx, pcy, prx, pry = X(nw * 0.4), nh * 0.5, nh * 0.24, nh * 0.33
    wcx, wcy, wrx, wry = X(nw * 0.87), nh * 0.5, nh * 0.11, nh * 0.22
    tx = X(nw * 0.645)  # centre of the text panel

    # Underprint (offset): crossing lathe waves, big rosette, ghost numeral; split-fountain colour.
    U = Plate(nw, nh)
    xs = np.linspace(-2, nw + 2, 700)
    for sp, A, lam, k in ((rng.uniform(1.9, 2.4), rng.uniform(1.5, 3), nw * rng.uniform(0.12, 0.2), 0.12),
                          (rng.uniform(2.6, 3.2), rng.uniform(2, 4), nw * rng.uniform(0.25, 0.4), -0.2)):
        for j, y0 in enumerate(np.arange(-2 * A, nh + 2 * A, sp)):
            U.line(xs, y0 + A * np.sin(TAU * xs / lam + j * k), 0.4)
    th = np.linspace(0, TAU, 2400)
    R, lobes, m = nh * 0.42, int(rng.integers(10, 20)), int(rng.integers(5, 9))
    for k in range(m):
        r = R * (0.65 + 0.35 * np.sin(lobes * th + k * TAU / m))
        U.line(tx + r * np.cos(th), nh * 0.55 + r * np.sin(th), 0.4, closed=True)
    U.text(tx, nh * 0.56, str(denom), nh * 0.55, "DejaVuSerif-Bold.ttf")
    U.clear_ellipse(wcx, wcy, wrx, wry)
    xg = np.linspace(0, 1, nw, dtype=np.float32)[None, :, None]
    c1, c2 = hsv(h1, rng.uniform(0.25, 0.4), 0.95), hsv(h2, rng.uniform(0.25, 0.4), 0.95)
    if not left:
        c1, c2 = c2, c1
    fountain = np.broadcast_to(c1 * (1 - xg) + c2 * xg, (nh, nw, 3)).astype(np.float32)

    # Watermark: faint ghost of the denomination inside the unprinted window.
    Wm = Plate(nw, nh)
    Wm.text(wcx, wcy, str(denom), wry * 0.9, "DejaVuSerif-Bold.ttf")
    wm = cv2.GaussianBlur(Wm.coverage(), (0, 0), 1.6) * 0.55

    # Windowed security thread.
    Th = Plate(nw, nh)
    thx, thw = X(nw * 0.27), nw * 0.012
    for y in np.arange(0, nh, nh * 0.12):
        Th.rect(thx - thw / 2, y, thx + thw / 2, y + nh * 0.06)
    thread = np.maximum(Th.coverage(), 0.0)
    thread[:, int(thx - thw / 2):int(np.ceil(thx + thw / 2))] = np.maximum(
        thread[:, int(thx - thw / 2):int(np.ceil(thx + thw / 2))], 0.3)

    n, glam, amp = int(rng.integers(4, 7)), nw * rng.uniform(0.03, 0.05), bw * 0.42
    cr = nh * 0.075
    cx0, cx1, cy0, cy1 = b + cr + 2, nw - b - cr - 2, b + cr + 2, nh - b - cr - 2
    micro = (issuer.replace(" ", "") + str(denom)) * 12

    def frame(P, corners):
        """Guilloche border, frame rules, microtext and white-numeral corner counters."""
        xs = np.linspace(inset, nw - inset, 1400)
        ys = np.linspace(inset, nh - inset, 700)
        for k in range(n):
            P.line(xs, inset + bw / 2 + amp * np.sin(TAU * xs / glam + k * TAU / n), 0.45)
            P.line(xs, nh - inset - bw / 2 + amp * np.sin(TAU * xs / glam + k * TAU / n), 0.45)
            P.line(inset + bw / 2 + amp * np.sin(TAU * ys / glam + k * TAU / n), ys, 0.45)
            P.line(nw - inset - bw / 2 + amp * np.sin(TAU * ys / glam + k * TAU / n), ys, 0.45)
        for o, wdt in ((inset, 1.0), (b, 0.9), (b + 1.6, 0.4)):
            P.line([o, nw - o, nw - o, o], [o, o, nh - o, nh - o], wdt, closed=True)
        P.text(nw / 2, b + 2.2, micro, nh * 0.022, anchor="mt")
        P.text(nw / 2, nh - b - 2.2, micro, nh * 0.022, anchor="mb")
        for qx, qy in corners:
            P.disc(qx, qy, cr)
            for k in range(8):
                r = cr * 1.25 * (0.85 + 0.15 * np.sin(8 * th + k * TAU / 8))
                P.line(qx + r * np.cos(th), qy + r * np.sin(th), 0.35, closed=True)
            P.text(qx, qy, str(denom), cr * (1.1 if denom < 100 else 0.85), "DejaVuSerif-Bold.ttf", value=0)

    # Intaglio face: border, corner counters, portrait, oval frame, legends, signatures.
    D = Plate(nw, nh)
    frame(D, [(X(cx0), cy0), (X(cx1), cy0), (X(cx0), cy1)])
    engraved_portrait(D, pcx, pcy, prx, pry, rng)
    D.ellipse(pcx, pcy, prx + 1.5, pry + 1.5, 0.9)
    D.ellipse(pcx, pcy, prx + 6.5, pry + 6.5, 0.7)
    tt = np.linspace(0, TAU, 110, endpoint=False)
    for a in tt:
        D.disc(pcx + (prx + 4) * np.cos(a), pcy + (pry + 4) * np.sin(a), 0.75)
    D.text(tx, nh * 0.19, issuer, nh * 0.058 * min(1.0, 17 / len(issuer)), "DejaVuSerif-Bold.ttf")
    word = f"{WORDS[denom]} {unit}"
    D.text(X(nw * 0.62), nh * 0.84, word, nh * 0.07 * min(1.0, 12 / len(word)), "DejaVuSerif-Bold.ttf")
    for sx, cap in ((X(nw * 0.57), "GOVERNOR"), (X(nw * 0.73), "TREASURER")):
        signature(D, sx, nh * 0.6, nw * 0.12, nh * 0.07, rng)
        D.text(sx, nh * 0.7, cap, nh * 0.032)

    # Seal (second colour) + small specimen mark.
    S = Plate(nw, nh)
    scx, scy, sr = X(nw * 0.17), nh * 0.5, nh * 0.12
    teeth = 44
    ang = np.linspace(0, TAU, 2 * teeth, endpoint=False)
    rad = np.where(np.arange(2 * teeth) % 2 == 0, sr, sr * 0.9)
    S.line(scx + rad * np.cos(ang), scy + rad * np.sin(ang), 0.6, closed=True)
    S.circle(scx, scy, sr * 0.8, 0.5)
    S.circle(scx, scy, sr * 0.55, 0.6)
    ring = "".join(w[0] for w in issuer.split()) * 8
    for i, ch in enumerate(ring[:18]):
        a = i * TAU / 18 - np.pi / 2
        S.text(scx + sr * 0.68 * np.cos(a), scy + sr * 0.68 * np.sin(a), ch, sr * 0.17, "DejaVuSans-Bold.ttf")
    pts = int(rng.integers(6, 11))
    ang = np.linspace(0, TAU, 2 * pts, endpoint=False) - np.pi / 2
    rad = np.where(np.arange(2 * pts) % 2 == 0, sr * 0.5, sr * 0.22)
    S.fill(scx + rad * np.cos(ang), scy + rad * np.sin(ang))
    S.text(tx, nh * 0.43, "SPECIMEN", nh * 0.045, "DejaVuSans-Bold.ttf")

    # Colour-shifting numeral.
    O = Plate(nw, nh)
    O.text(wcx, nh * 0.83, str(denom), nh * 0.17, "DejaVuSans-Bold.ttf")

    layers = [("underprint", fountain, U.coverage() * 0.8), ("watermark", substrate * 0.9, wm),
              ("thread", np.array([0.45, 0.47, 0.5], np.float32), thread), ("intaglio", dark, D.coverage()),
              ("seal", seal_c, S.coverage()), ("ovi", ovi_c, O.coverage())]
    spots = [(tx, nh * 0.31, "mm"), (X(nw * 0.23), nh * 0.82, "mm")]
    front = Design(f"{name}_front", "banknote", layers, seal_c, spots, nh * 0.055, substrate, side="front")

    # Back: the sheet is flipped, so window, thread and fountain are mirrored. Central engraved vignette,
    # large numeral, no serials. Layout is written window-left (X() mirrors it like the front).
    BU = Plate(nw, nh)
    for sp, A, lam, k in ((rng.uniform(1.9, 2.4), rng.uniform(1.5, 3), nw * rng.uniform(0.1, 0.18), -0.15),
                          (rng.uniform(2.6, 3.2), rng.uniform(2, 4), nw * rng.uniform(0.2, 0.35), 0.18)):
        for j, y0 in enumerate(np.arange(-2 * A, nh + 2 * A, sp)):
            BU.line(xs, y0 + A * np.sin(TAU * xs / lam + j * k), 0.4)
    vx0, vx1 = sorted((X(nw * 0.27), X(nw * 0.69)))
    vy0, vy1 = nh * 0.27, nh * 0.73
    vcx = (vx0 + vx1) / 2
    for k in range(m):
        r = nh * 0.46 * (0.7 + 0.3 * np.sin((lobes + 3) * th + k * TAU / m))
        BU.line(vcx + r * 1.6 * np.cos(th), nh * 0.5 + r * np.sin(th), 0.4, closed=True)
    BU.clear_ellipse(nw - wcx, wcy, wrx, wry)
    bthread = np.zeros((nh, nw), np.float32)
    bx = nw - thx
    bthread[:, int(bx - thw / 2):int(np.ceil(bx + thw / 2))] = 0.3

    BD = Plate(nw, nh)
    frame(BD, [(X(cx0), cy0), (X(cx1), cy0), (X(cx0), cy1), (X(cx1), cy1)])
    engraved_building(BD, vx0, vy0, vx1, vy1, rng)
    ah = (vy1 - vy0) * 0.25
    for o, wdt in ((1.5, 0.9), (4.5, 0.6)):
        t = np.linspace(np.pi, TAU, 200)
        ex = np.concatenate([[vx0 - o, vx0 - o], vcx + ((vx1 - vx0) / 2 + o) * np.cos(t), [vx1 + o, vx1 + o]])
        ey = np.concatenate([[vy1 + o, vy0 + ah], vy0 + ah + (ah + o) * np.sin(t), [vy0 + ah, vy1 + o]])
        BD.line(ex, ey, wdt, closed=True)
    BD.text(X(nw * 0.48), nh * 0.17, issuer, nh * 0.058 * min(1.0, 17 / len(issuer)), "DejaVuSerif-Bold.ttf")
    BD.text(X(nw * 0.48), nh * 0.825, word, nh * 0.06 * min(1.0, 12 / len(word)), "DejaVuSerif-Bold.ttf")
    big = str(denom)
    BD.text(X(nw * 0.84), nh * 0.5, big, nh * 0.34 * min(1.0, 2.2 / len(big)), "DejaVuSerif-Bold.ttf")
    BD.text(X(nw * 0.84), nh * 0.7, "SERIES 2026", nh * 0.035)

    BS = Plate(nw, nh)
    BS.text(X(nw * 0.84), nh * 0.3, "SPECIMEN", nh * 0.04, "DejaVuSans-Bold.ttf")
    wx = np.linspace(X(nw * 0.75), X(nw * 0.93), 200)
    for k in range(3):
        BS.line(wx, nh * 0.77 + 1.5 * np.sin(TAU * wx / 9 + k * TAU / 3), 0.45)

    back_layers = [("underprint", np.ascontiguousarray(fountain[:, ::-1]), BU.coverage() * 0.8),
                   ("watermark", substrate * 0.9, np.ascontiguousarray(wm[:, ::-1])),
                   ("thread", np.array([0.45, 0.47, 0.5], np.float32), bthread),
                   ("intaglio", dark, BD.coverage()), ("seal", seal_c, BS.coverage())]
    back = Design(f"{name}_back", "banknote", back_layers, seal_c, [], nh * 0.055, substrate, side="back")
    return front, back


def engraved_building(P, x0, y0, x1, y1, rng, pitch=1.15):
    """Procedural line-engraved vignette of a generic columned building under an arch-topped frame."""
    s = P.ss
    X0, Y0, X1, Y1 = int(x0 * s), int(y0 * s), int(x1 * s) + 1, int(y1 * s) + 1
    yy, xx = np.mgrid[Y0:Y1, X0:X1].astype(np.float32) / s
    u, v = (xx - x0) / (x1 - x0), (yy - y0) / (y1 - y0)
    ah = 0.25
    mask = (v >= ah) | (((u - 0.5) / 0.5) ** 2 + ((v - ah) / ah) ** 2 < 1)

    T = 0.3 - 0.16 * v                                   # sky: darker toward the top
    phase = yy.copy()
    ground = v > 0.84
    T = np.where(ground, 0.5 + 0.1 * np.sin(xx * 1.7), T)
    phase = np.where(ground, xx * 0.4 + yy, phase)
    for tu in (0.1, 0.9):                                 # trees
        tree = ((u - tu) / 0.1) ** 2 + ((v - 0.68) / 0.24) ** 2 < 1
        T = np.where(tree, 0.62 + 0.22 * np.sin(xx * 1.3) * np.sin(yy * 1.1), T)
        phase = np.where(tree, (xx + yy) * 0.7, phase)

    bx, bwid = 0.5 + rng.uniform(-0.03, 0.03), rng.uniform(0.5, 0.62)
    bl, br = bx - bwid / 2, bx + bwid / 2
    if rng.uniform() < 0.6:                               # dome + lantern
        du, dv = (u - bx) / (bwid * 0.24), (v - 0.3) / 0.16
        dome = (du ** 2 + dv ** 2 < 1) & (v < 0.3)
        z = np.sqrt(np.clip(1 - du ** 2 - dv ** 2, 0, 1))
        T = np.where(dome, 0.3 + 0.4 * (1 - np.clip(-0.5 * du + 0.85 * z, 0, 1)), T)
        phase = np.where(dome, xx + 2 * du ** 2, phase)
        lantern = (np.abs(u - bx) < bwid * 0.035) & (v > 0.08) & (v < 0.15)
        T = np.where(lantern, 0.55, T)
    ped = (v > 0.2) & (v <= 0.39) & (np.abs(u - bx) < (v - 0.2) / 0.19 * bwid * 0.55)
    T = np.where(ped, 0.45, T)
    entab = (v > 0.39) & (v <= 0.46) & (u > bl - 0.02) & (u < br + 0.02)
    T = np.where(entab, 0.42 + 0.3 * ((v > 0.43) & (v < 0.44)), T)
    facade = (v > 0.46) & (v <= 0.84) & (u > bl) & (u < br)
    ncol = int(rng.integers(6, 11))
    cp = ((u - bl) / bwid * ncol) % 1.0
    col = facade & (cp > 0.25) & (cp < 0.75)
    T = np.where(facade, 0.78, T)
    T = np.where(col, 0.08 + 0.5 * np.abs(cp - 0.5) * 2, T)
    phase = np.where(facade, xx, phase)
    steps = (v > 0.84) & (v < 0.94) & (np.abs(u - bx) < bwid * 0.58)
    T = np.where(steps, np.where(((v - 0.84) / 0.025).astype(int) % 2 == 0, 0.2, 0.6), T)
    phase = np.where(steps, yy, phase)

    T = np.clip(T, 0, 0.95)
    ink = np.abs((phase / pitch) % 1.0 - 0.5) < T * 0.5
    ink |= (T > 0.55) & (np.abs(((xx * 0.87 - yy * 0.5) / pitch) % 1.0 - 0.5) < (T - 0.55) * 0.9)
    region = P.img[Y0:Y1, X0:X1]
    np.maximum(region, np.where(ink & mask, 255, 0).astype(np.uint8), out=region)


def build_testsheet(name, rng, nw, nh):
    """Generic press QA target: CMYK colour bar, halftone ramps, Siemens star, slur lines, register marks."""
    base = {"yellow": [1.0, 0.93, 0.05], "magenta": [0.9, 0.05, 0.52], "cyan": [0.0, 0.66, 0.92],
            "black": [0.13, 0.13, 0.14]}
    inks = {k: np.clip(np.array(v, np.float32) + rng.normal(0, 0.02, 3), 0, 1).astype(np.float32)
            for k, v in base.items()}
    P = {k: Plate(nw, nh) for k in inks}
    pad = nh * 0.05

    items = [("cyan",), ("magenta",), ("yellow",), ("black",), ("cyan", "magenta"), ("cyan", "yellow"),
             ("magenta", "yellow"), ("cyan", "magenta", "yellow"),
             ("cyan", 0.5), ("magenta", 0.5), ("yellow", 0.5), ("black", 0.5), ("black", 0.25)]
    items = [items[i] for i in rng.permutation(len(items))]
    by0, by1 = pad, pad + nh * 0.16
    pw = (nw - 2 * pad) / len(items)
    for i, it in enumerate(items):
        x0, x1 = pad + i * pw, pad + (i + 1) * pw - 1
        if isinstance(it[-1], float):
            P[it[0]].halftone(x0, by0, x1, by1, it[1], SCREEN_ANGLE[it[0]])
        else:
            for ink in it:
                P[ink].rect(x0, by0, x1, by1)

    ramp_left = rng.uniform() < 0.5
    rx0 = pad if ramp_left else nw * 0.52
    ry, rh = by1 + pad * 0.8, nh * 0.095
    for i, ink in enumerate(rng.permutation(list(inks))):
        y0 = ry + i * (rh + 2)
        P[ink].halftone(rx0, y0, rx0 + nw * 0.44, y0 + rh, lambda u: u, SCREEN_ANGLE[ink])

    sx, sy, sr = (nw * 0.72 if ramp_left else nw * 0.28), ry + 2 * (rh + 2), nh * 0.2
    spokes = int(rng.integers(18, 40))
    for i in range(spokes):
        a0 = i * TAU / spokes
        a1 = a0 + TAU / spokes / 2
        P["black"].fill([sx, sx + sr * np.cos(a0), sx + sr * np.cos(a1)], [sy, sy + sr * np.sin(a0), sy + sr * np.sin(a1)])
    lx0, lw = sx + sr + pad, nw * 0.1
    for y in np.arange(sy - sr, sy - 1, 1.6):
        P["black"].line([lx0, lx0 + lw], [y, y], 0.5)
    for x in np.arange(lx0, lx0 + lw, 1.6):
        P["cyan"].line([x, x], [sy + 1, sy + sr], 0.5)

    ty = ry + 4 * (rh + 2) + pad * 0.5
    rr = nh * 0.055
    for mx in (pad + rr + 1, nw - pad - rr - 1):
        my = ty + nh * 0.09
        for p in P.values():
            p.circle(mx, my, rr * 0.7, 0.5)
            p.line([mx - rr, mx + rr], [my, my], 0.5)
            p.line([mx, mx], [my - rr, my + rr], 0.5)
    P["black"].text(nw / 2, ty + nh * 0.05, "SYNTHETIC TEST PRINT · PRESS QA TARGET", nh * 0.058, "DejaVuSans-Bold.ttf")
    P["black"].text(nw / 2, ty + nh * 0.115, "NOT CURRENCY · MACHINE-VISION TRAINING ONLY", nh * 0.04)
    P["black"].text(nw / 2, ty + nh * 0.16, "SYNTHETICTESTPRINT" * 20, nh * 0.026)

    layers = [(k, inks[k], P[k].coverage()) for k in ("yellow", "magenta", "cyan", "black")]
    sub = np.clip(np.array([0.95, 0.95, 0.93]) + rng.normal(0, 0.01, 3), 0, 1).astype(np.float32)
    return Design(name, "testsheet", layers, inks["black"], [(nw / 2, nh - pad * 0.9, "mm")], nh * 0.045, sub)


# --------------------------------------------------------------------------- sheet state


class State:
    """Ink layers + post-print operations + sheet alpha (for tears/holes)."""

    def __init__(self, layers, substrate, texture, alpha):
        self.layers, self.substrate, self.texture, self.alpha = layers, substrate, texture, alpha
        self.post = []

    def copy(self):
        # Defects always assign new arrays, so shallow copies are enough to roll back.
        s = State([dict(L) for L in self.layers], self.substrate, self.texture, self.alpha)
        s.post = list(self.post)
        return s

    def render(self):
        rgb = np.empty(self.texture.shape + (3,), np.float32)
        rgb[:] = self.substrate
        for L in self.layers:
            rgb *= 1.0 - L["cov"][..., None] * (1.0 - L["color"])
        for op in self.post:
            rgb = op(rgb)
        return rgb * self.texture[..., None]


def tile_color(field, geo):
    """Tile a note-size (nh, nw, 3) ink colour field; margins/gutters take the mean colour."""
    out = np.empty((geo.sh, geo.sw, 3), np.float32)
    out[:] = field.reshape(-1, 3).mean(0)
    for x, y in geo.origins:
        out[y:y + geo.nh, x:x + geo.nw] = field
    return out


def tile(plate, geo):
    out = np.zeros((geo.sh, geo.sw), np.float32)
    for x, y in geo.origins:
        out[y:y + geo.nh, x:x + geo.nw] = plate
    return out


def serial_layer(design, geo, sheet_idx, seed):
    im = Image.new("L", (geo.sw, geo.sh), 0)
    d = ImageDraw.Draw(im)
    f = font(design.serial_size, "DejaVuSansMono-Bold.ttf")
    for i, (x0, y0) in enumerate(geo.origins):
        r, c = divmod(i, geo.cols)
        if design.kind == "banknote":
            s = f"{design.serial_prefix} {sheet_idx * len(geo.origins) + i + 1:08d} {chr(65 + seed % 26)}"
        else:
            s = f"CELL {r + 1:02d}-{c + 1:02d}  #{sheet_idx:06d}"
        for sx, sy, anchor in design.serial_spots:
            d.text((x0 + sx, y0 + sy), s, fill=255, font=f, anchor=anchor)
    d.text((geo.margin, geo.margin * 0.5), f"SHEET {sheet_idx:06d}  ·  {design.name}  ·  SYNTHETIC SPECIMEN - NOT CURRENCY",
           fill=255, font=font(geo.margin * 0.32), anchor="lm")
    return np.asarray(im, np.float32) / 255.0


def add_sheet_marks(layers, geo):
    """Corner register marks in every ink and a colour-control strip in the bottom margin."""
    m, W, H = geo.margin, geo.sw, geo.sh
    canvas = np.zeros((H, W), np.uint8)
    rr = int(m * 0.3)
    for cx, cy in ((m // 2, m // 2), (W - m // 2, m // 2), (m // 2, H - m // 2), (W - m // 2, H - m // 2)):
        cv2.circle(canvas, (cx, cy), int(rr * 0.65), 255, 1, cv2.LINE_AA)
        cv2.line(canvas, (cx - rr, cy), (cx + rr, cy), 255, 1, cv2.LINE_AA)
        cv2.line(canvas, (cx, cy - rr), (cx, cy + rr), 255, 1, cv2.LINE_AA)
    marks = canvas.astype(np.float32) / 255.0
    y0, y1 = H - int(m * 0.42), H - int(m * 0.12)
    pw = max(6, int(m * 0.6))
    for i, x0 in enumerate(range(m * 2, W - m * 2 - pw, pw + 2)):
        L = layers[(i // 2) % len(layers)]
        L["cov"][y0:y1, x0:x0 + pw] = np.maximum(L["cov"][y0:y1, x0:x0 + pw], 1.0 if i % 2 == 0 else 0.5)
    for L in layers:
        L["cov"] = np.maximum(L["cov"], marks)


def paper_texture(rng, h, w):
    fine = cv2.resize(rng.normal(0, 1, (h // 3 + 1, w // 3 + 1)).astype(np.float32), (w, h))
    mottle = cv2.resize(rng.normal(0, 1, (h // 40 + 2, w // 40 + 2)).astype(np.float32), (w, h),
                        interpolation=cv2.INTER_CUBIC)
    return (1.0 + 0.01 * fine + 0.01 * mottle).astype(np.float32)


def make_state(design, geo, idx, rng, seed):
    layers = [dict(name=n, color=tile_color(c, geo) if c.ndim == 3 else c.copy(), cov=tile(p, geo))
              for n, c, p in design.layers]
    layers.append(dict(name="serial", color=design.serial_color.copy(), cov=serial_layer(design, geo, idx, seed)))
    add_sheet_marks(layers, geo)
    for L in layers:  # natural press variation that still counts as "good"
        dx, dy = rng.uniform(-0.35, 0.35, 2)
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        L["cov"] = np.clip(cv2.warpAffine(L["cov"], M, (geo.sw, geo.sh)) * rng.normal(1.0, 0.02), 0, 1)
        L["color"] = np.clip(L["color"] + rng.normal(0, 0.008, 3), 0, 1).astype(np.float32)
    substrate = np.clip(design.substrate + rng.normal(0, 0.006, 3), 0, 1).astype(np.float32)
    return State(layers, substrate, paper_texture(rng, geo.sh, geo.sw), np.ones((geo.sh, geo.sw), np.float32))


# --------------------------------------------------------------------------- defects


def blob_mask(rng, shape, cx, cy, rx, ry, angle=0.0, rough=0.3, soft=1.0):
    """Irregular soft-edged blob as a full-size float32 mask."""
    H, W = shape
    m = np.zeros(shape, np.float32)
    R = int(max(rx, ry) * (1 + rough) + 4 + 3 * soft)
    x0, x1, y0, y1 = max(0, int(cx) - R), min(W, int(cx) + R + 1), max(0, int(cy) - R), min(H, int(cy) + R + 1)
    if x1 <= x0 or y1 <= y0:
        return m
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    ca, sa = np.cos(angle), np.sin(angle)
    u = ((xx - cx) * ca + (yy - cy) * sa) / rx
    v = (-(xx - cx) * sa + (yy - cy) * ca) / ry
    r, th = np.sqrt(u * u + v * v), np.arctan2(v, u)
    edge = np.ones_like(r)
    for k, ph, a in zip(rng.integers(2, 6, 3), rng.uniform(0, TAU, 3), rng.uniform(0, rough, 3)):
        edge += a / 2 * np.sin(k * th + ph)
    m[y0:y1, x0:x1] = np.clip((edge - r) * max(rx, ry) / soft + 0.5, 0, 1)
    return m


def sample_point(rng, weight, step=8):
    w = weight[::step, ::step].astype(np.float64).ravel()
    if w.sum() <= 0:
        w = np.ones_like(w)
    i = rng.choice(w.size, p=w / w.sum())
    y, x = divmod(i, weight[::step, ::step].shape[1])
    return x * step + rng.uniform(0, step), y * step + rng.uniform(0, step)


def pick_layer(rng, st, dark=True, exclude=()):
    idx = [i for i, L in enumerate(st.layers) if L["name"] not in exclude]
    if dark:
        w = np.maximum(np.array([1.0 - float(np.mean(st.layers[i]["color"])) for i in idx]), 0.02) ** 2
    else:
        w = np.ones(len(idx))
    return st.layers[idx[rng.choice(len(idx), p=w / w.sum())]]


def pick_cells(rng, geo):
    v = rng.choice(["note", "block", "row", "column"], p=[0.45, 0.3, 0.15, 0.1])
    nr, nc = {"note": (1, 1), "row": (1, geo.cols), "column": (geo.rows, 1)}.get(
        v, (int(rng.integers(1, 3)), int(rng.integers(2, 3))))
    r0, c0 = int(rng.integers(0, geo.rows - nr + 1)), int(rng.integers(0, geo.cols - nc + 1))
    return str(v), geo.cells_rect(r0, c0, nr, nc), [r0, c0, nr, nc]


def d_ink_void(rng, st, geo):
    total = np.clip(sum(L["cov"] for L in st.layers), 0, 1)
    cx, cy = sample_point(rng, total ** 2)
    if rng.uniform() < 0.3:
        variant, rx, ry, ang = "starvation_streak", rng.uniform(1.5, 3.5), rng.uniform(15, 70), rng.normal(0, 0.05)
    else:
        variant = "dropout"
        rx = rng.uniform(3, 16)
        ry, ang = rx * rng.uniform(0.6, 1.4), rng.uniform(0, np.pi)
    m = blob_mask(rng, total.shape, cx, cy, rx, ry, ang, rough=0.35)
    iy, ix = min(int(cy), geo.sh - 1), min(int(cx), geo.sw - 1)
    inked = [L for L in st.layers if L["cov"][iy, ix] > 0.2]
    targets = st.layers if (rng.uniform() < 0.5 or not inked) else [inked[rng.integers(len(inked))]]
    for L in targets:
        L["cov"] = L["cov"] * (1 - m * rng.uniform(0.85, 1.0))
    return dict(variant=variant, center=[cx, cy], radius=[rx, ry], layers=[L["name"] for L in targets]), None


def d_streak(rng, st, geo):
    """Doctor-blade style line running in the direction of sheet travel (vertical)."""
    H, W = geo.sh, geo.sw
    L = pick_layer(rng, st)
    x = rng.uniform(geo.margin * 0.5, W - geo.margin * 0.5)
    length = rng.uniform(0.15, 1.0) * H
    y0 = rng.uniform(0, H - length)
    ys = np.arange(int(y0), int(y0 + length))
    width, inten = rng.uniform(1.0, 3.5), rng.uniform(0.35, 0.9)
    knots = np.linspace(ys[0], ys[-1], 12)
    xw = x + np.interp(ys, knots, rng.normal(0, 0.8, 12))
    strength = inten * np.clip(0.75 + 0.25 * np.interp(ys, knots, rng.normal(0, 1, 12)), 0.3, 1.0)
    taper = np.clip(np.minimum(ys - ys[0], ys[-1] - ys) / 20.0, 0, 1)
    xa, xb = max(0, int(x) - 8), min(W, int(x) + 9)
    xx = np.arange(xa, xb, dtype=np.float32)
    prof = np.clip(width / 2 + 0.5 - np.abs(xx[None, :] - xw[:, None]), 0, 1) * (strength * taper)[:, None]
    m = np.zeros((H, W), np.float32)
    m[ys, xa:xb] = prof
    L["cov"] = L["cov"] + m * (1 - L["cov"])
    return dict(layer=L["name"], x=x, y=[y0, y0 + length], width=width, intensity=inten), None


def d_smear(rng, st, geo):
    H, W = geo.sh, geo.sw
    L = pick_layer(rng, st, exclude=("tint",))
    cx, cy = sample_point(rng, L["cov"])
    ang, length, r = rng.uniform(0, TAU), rng.uniform(12, 45), rng.uniform(8, 30)
    k = int(length) | 1
    kern = np.zeros((k, k), np.float32)
    kern[k // 2, k // 2:] = 1  # one-sided drag
    kern = cv2.warpAffine(kern, cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), np.rad2deg(-ang), 1), (k, k))
    kern /= max(kern.sum(), 1e-6)
    R = int(r + length + 10)
    x0, x1, y0, y1 = max(0, int(cx) - R), min(W, int(cx) + R), max(0, int(cy) - R), min(H, int(cy) + R)
    m = blob_mask(rng, (H, W), cx + np.cos(ang) * length / 3, cy + np.sin(ang) * length / 3,
                  r + length / 2, r * 0.6, ang, rough=0.25, soft=4)[y0:y1, x0:x1]
    cov = L["cov"]
    sub = cov[y0:y1, x0:x1]
    smeared = cv2.filter2D(sub, -1, kern, borderType=cv2.BORDER_REFLECT) * rng.uniform(1.2, 1.8)
    dep = rng.uniform(0.12, 0.35)
    new = cov.copy()
    new[y0:y1, x0:x1] = sub + m * (np.clip(np.maximum(sub, smeared) + dep * m, 0, 1) - sub)
    L["cov"] = new
    return dict(layer=L["name"], center=[cx, cy], angle=ang, length=length, radius=r), None


def d_misregistration(rng, st, geo):
    L = pick_layer(rng, st, dark=False, exclude=("serial",))
    variant, (x0, y0, x1, y1), cells = pick_cells(rng, geo)
    mag, ang = rng.uniform(2.5, 9.0), rng.uniform(0, TAU)
    dx, dy = mag * np.cos(ang), mag * np.sin(ang)
    shifted = cv2.warpAffine(L["cov"], np.float32([[1, 0, dx], [0, 1, dy]]), (geo.sw, geo.sh))
    new = L["cov"].copy()
    new[y0:y1, x0:x1] = shifted[y0:y1, x0:x1]
    L["cov"] = new
    return dict(variant=variant, layer=L["name"], cells=cells, shift_px=[dx, dy]), None


def d_color_shift(rng, st, geo):
    H, W = geo.sh, geo.sw
    L = pick_layer(rng, st, dark=False, exclude=("serial",))
    if rng.uniform() < 0.5:  # ink-key zone drift: a band along the direction of travel
        variant = "key_zone"
        bw = rng.uniform(0.08, 0.3) * W
        x0 = rng.uniform(0, W - bw)
        ln = rng.uniform(0.3, 1.0) * H
        y0 = rng.uniform(0, H - ln)
        rect = (int(x0), int(y0), int(x0 + bw), int(y0 + ln))
        cells = None
    else:
        variant, rect, cells = pick_cells(rng, geo)
    m = np.zeros((H, W), np.float32)
    m[rect[1]:rect[3], rect[0]:rect[2]] = 1
    m = cv2.GaussianBlur(m, (0, 0), 6)
    mode = "density" if rng.uniform() < 0.5 else "hue"
    params = dict(variant=variant, mode=mode, layer=L["name"], rect=list(rect), cells=cells)
    if mode == "density":
        if rng.uniform() < 0.5:
            g = rng.uniform(0.45, 0.7)
            target = L["cov"] * g
        else:
            g = rng.uniform(1.8, 3.0)
            target = 1 - (1 - L["cov"]) ** g
        L["cov"] = L["cov"] + m * (target - L["cov"])
        params["factor"] = g
    else:
        c = L["color"] if L["color"].ndim == 1 else L["color"][rect[1], rect[0]]
        h, s, v = colorsys.rgb_to_hsv(*[float(t) for t in c])
        dh = rng.choice([-1, 1]) * rng.uniform(0.05, 0.14)
        c2 = hsv(h + dh, s * rng.uniform(0.8, 1.3) + 0.1, v * rng.uniform(0.75, 1.05))
        base = L["color"] if L["color"].ndim == 3 else np.broadcast_to(L["color"], (H, W, 3))
        L["color"] = (base * (1 - m[..., None]) + c2 * m[..., None]).astype(np.float32)
        params["hue_shift"] = dh
    return params, None


def d_crease(rng, st, geo):
    H, W = geo.sh, geo.sw
    cx, cy = rng.uniform(0.1, 0.9) * W, rng.uniform(0.1, 0.9) * H
    ang = rng.uniform(0, np.pi)
    half = rng.uniform(0.1, 0.6) * np.hypot(W, H) / 2
    depth, hw, side = rng.uniform(0.08, 0.2), rng.uniform(1.0, 2.0), rng.uniform(3, 8)
    ca, sa = np.cos(ang), np.sin(ang)
    x0 = int(np.clip(min(cx - ca * half, cx + ca * half) - 20, 0, W))
    x1 = int(np.clip(max(cx - ca * half, cx + ca * half) + 20, 0, W))
    y0 = int(np.clip(min(cy - sa * half, cy + sa * half) - 20, 0, H))
    y1 = int(np.clip(max(cy - sa * half, cy + sa * half) + 20, 0, H))
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    t = (xx - cx) * ca + (yy - cy) * sa
    d = -(xx - cx) * sa + (yy - cy) * ca
    taper = np.clip((half - np.abs(t)) / 30.0, 0, 1)
    shade = (-depth * np.exp(-(d / hw) ** 2) + 0.4 * depth * np.exp(-((d - side * 0.6) / side) ** 2)
             - 0.3 * depth * np.exp(-((d + side) / (side * 1.5)) ** 2))
    f = (1 + shade * taper)[..., None]
    crack = (0.35 * np.exp(-(d / (hw * 1.5)) ** 2) * taper * (rng.uniform(size=d.shape) > 0.55))[..., None]

    def op(rgb):
        rgb = rgb.copy()
        sub = rgb[y0:y1, x0:x1]
        rgb[y0:y1, x0:x1] = (sub + (1 - sub) * crack) * f
        return rgb

    st.post.append(op)
    mask = np.zeros((H, W), np.uint8)
    mask[y0:y1, x0:x1] = (np.abs(d) < hw * 2 + 2) & (taper > 0)
    return dict(center=[cx, cy], angle=ang, length=2 * half, depth=depth), mask


def d_tear(rng, st, geo):
    H, W = geo.sh, geo.sw
    removed = np.zeros((H, W), np.uint8)
    if rng.uniform() < 0.65:
        variant = "edge_tear"
        side = int(rng.integers(4))
        span = W if side < 2 else H
        p = rng.uniform(0.05, 0.95) * span
        depth, width = rng.uniform(12, 70), rng.uniform(8, 40)
        s = np.linspace(0, 1, 14)
        a = -width / 2 + s * width + rng.uniform(-2, 2, 14)
        b = depth * (1 - np.abs(2 * s - 1)) ** 0.8 + rng.uniform(-3, 3, 14)
        b[0] = b[-1] = -4
        xs, ys = {0: (p + a, b), 1: (p + a, H - 1 - b), 2: (b, p + a), 3: (W - 1 - b, p + a)}[side]
        cv2.fillPoly(removed, [np.round(np.stack([xs, ys], -1)).astype(np.int32)], 1)
        params = dict(variant=variant, side=side, position=p, depth=depth, width=width)
    else:
        variant = "hole"
        cx, cy = rng.uniform(0.05, 0.95) * W, rng.uniform(0.05, 0.95) * H
        r = rng.uniform(3, 12)
        removed = (blob_mask(rng, (H, W), cx, cy, r, r * rng.uniform(0.7, 1.3), rng.uniform(0, np.pi), 0.4) > 0.5
                   ).astype(np.uint8)
        params = dict(variant=variant, center=[cx, cy], radius=r)
    ring = (cv2.dilate(removed, np.ones((5, 5), np.uint8)) - removed).astype(np.float32)
    fiber = (ring * rng.uniform(0.3, 0.7, ring.shape))[..., None]
    st.post.append(lambda rgb: rgb + (1 - rgb) * fiber)
    st.alpha = st.alpha * (1 - cv2.GaussianBlur(removed.astype(np.float32), (0, 0), 0.6))
    return params, (removed | (ring > 0)).astype(np.uint8)


def d_contamination(rng, st, geo):
    H, W = geo.sh, geo.sw
    variant = str(rng.choice(["speck", "fiber", "oil"], p=[0.45, 0.35, 0.2]))
    cx, cy = rng.uniform(0.02, 0.98) * W, rng.uniform(0.02, 0.98) * H
    col = hsv(rng.uniform(0.05, 0.12), rng.uniform(0.2, 0.6), rng.uniform(0.08, 0.3))
    if variant == "speck":
        r = rng.uniform(2, 7)
        m = blob_mask(rng, (H, W), cx, cy, r, r * rng.uniform(0.6, 1.3), rng.uniform(0, np.pi), 0.4)
        m *= rng.uniform(0.85, 1.0)
    elif variant == "fiber":
        n = int(rng.uniform(25, 100) / 3)
        heading = rng.uniform(0, TAU) + np.cumsum(rng.normal(0, 0.15, n))
        pts = np.stack([cx + np.cumsum(3 * np.cos(heading)), cy + np.cumsum(3 * np.sin(heading))], -1)
        canvas = np.zeros((H, W), np.uint8)
        cv2.polylines(canvas, [np.round(pts * 16).astype(np.int32)], False, 255,
                      thickness=int(rng.integers(1, 3)), lineType=cv2.LINE_AA, shift=4)
        m = canvas.astype(np.float32) / 255.0 * rng.uniform(0.6, 0.9)
    else:
        r = rng.uniform(12, 40)
        m = blob_mask(rng, (H, W), cx, cy, r, r * rng.uniform(0.6, 1.2), rng.uniform(0, np.pi), 0.2, soft=6)
        m *= rng.uniform(0.3, 0.6)
        tint = np.array([0.85, 0.78, 0.6], np.float32)
        st.post.append(lambda rgb: rgb * (1 - m[..., None] * (1 - tint)))
        return dict(variant=variant, center=[cx, cy], radius=r), None
    st.post.append(lambda rgb: rgb * (1 - m[..., None]) + col * m[..., None])
    return dict(variant=variant, center=[cx, cy]), None


DEFECTS = {"ink_void": d_ink_void, "streak": d_streak, "smear": d_smear, "misregistration": d_misregistration,
           "color_shift": d_color_shift, "crease": d_crease, "tear": d_tear, "contamination": d_contamination}


# --------------------------------------------------------------------------- camera capture


def background(rng, H, W):
    base = rng.uniform(0.1, 0.2)
    low = cv2.resize(rng.normal(0, 1, (8, 8)).astype(np.float32), (W, H), interpolation=cv2.INTER_CUBIC) * 0.015
    fine = cv2.resize(rng.normal(0, 1, (H // 2, W // 2)).astype(np.float32), (W, H)) * 0.01
    return (base + low + fine)[..., None] * np.array([1.0, 1.0, 1.04], np.float32)


def capture(rgb, alpha, masks, rng, preset, geo):
    """Place the sheet on a conveyor background and simulate an inline camera; warps masks identically."""
    P = CAPTURE[preset]
    W, H = geo.out_w, geo.out_h
    h, w = alpha.shape
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    th = np.deg2rad(rng.uniform(-P["rot"], P["rot"])) if P["rot"] else 0.0
    s = 1 + (rng.uniform(-P["scale"], P["scale"]) if P["scale"] else 0.0)
    c = np.array([W / 2 + rng.uniform(-1, 1) * P["shift"] * W, H / 2 + rng.uniform(-1, 1) * P["shift"] * H])
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    dst = (src - [w / 2, h / 2]) * s @ R.T + c + rng.uniform(-1, 1, (4, 2)) * P["persp"] * w
    Hm = cv2.getPerspectiveTransform(src, dst.astype(np.float32))

    def warp(a, interp=cv2.INTER_LINEAR):
        return cv2.warpPerspective(a, Hm, (W, H), flags=interp, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    sheet, a = warp(rgb), warp(alpha)[..., None]
    bg = background(rng, H, W)
    shadow = cv2.GaussianBlur(np.roll(a[..., 0], (5, 3), (0, 1)), (0, 0), 6)
    img = bg * (1 - 0.45 * shadow)[..., None] * (1 - a) + sheet * a
    if P["light"]:
        yn, xn = np.mgrid[-1:1:complex(H), -1:1:complex(W)].astype(np.float32)
        light = (1 + rng.uniform(-1, 1) * P["light"] * xn + rng.uniform(-1, 1) * P["light"] * yn
                 - P["vignette"] * rng.uniform(0.5, 1.0) * (xn ** 2 + yn ** 2) / 2)
        img = img * light[..., None]
    sigma = rng.uniform(*P["blur"])
    if sigma > 0.05:
        img = cv2.GaussianBlur(img, (0, 0), sigma)
    sd = rng.uniform(*P["noise"]) / 255.0
    if sd > 0:
        img = img + rng.normal(0, sd, img.shape).astype(np.float32)
    out = np.clip(img * 255 + 0.5, 0, 255).astype(np.uint8)
    wm = [(warp(m.astype(np.float32)) > 0.35).astype(np.uint8) for m in masks]
    return out, wm, int(rng.integers(P["jpeg"][0], P["jpeg"][1] + 1)), Hm


# --------------------------------------------------------------------------- worker

CFG, GEO, _DESIGNS = None, None, {}


def _init(cfg):
    global CFG, GEO
    CFG = cfg
    GEO = Geometry(cfg["width"], cfg["cols"], cfg["rows"])
    cv2.setNumThreads(1)


def get_design(d, side):
    if d not in _DESIGNS:
        kind = CFG["design_kinds"][d]
        rng = np.random.default_rng([CFG["seed"], 2, d])
        name = f"{kind}_{d:02d}"
        if kind == "banknote":
            front, back = build_banknote(name, rng, GEO.nw, GEO.nh)
            _DESIGNS[d] = {"front": front, "back": back}
        else:
            _DESIGNS[d] = {"front": build_testsheet(name, rng, GEO.nw, GEO.nh)}
    return _DESIGNS[d][side]


def render_sheet(job):
    idx, split, d_idx, side, defective = job
    geo, out = GEO, CFG["out"]
    design = get_design(d_idx, side)
    rng = np.random.default_rng([CFG["seed"], 1, idx])
    st = make_state(design, geo, idx, rng, CFG["seed"])
    prev = st.render()
    defects, masks = [], []
    if defective:
        weights = np.array(CFG["class_weights"], float)
        for ci in rng.choice(len(CLASSES), size=rng.choice([1, 2, 3], p=[0.6, 0.3, 0.1]), p=weights / weights.sum()):
            cls = CLASSES[ci]
            for _ in range(8):
                trial = st.copy()
                params, explicit = DEFECTS[cls](rng, trial, geo)
                cur = trial.render()
                m = explicit if explicit is not None else (np.abs(cur - prev).max(-1) > DIFF_THR).astype(np.uint8)
                k = CLOSE_K.get(cls, 1)
                if k > 1:
                    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
                if int(m.sum()) >= MIN_DEFECT_PX:
                    st, prev = trial, cur
                    defects.append(dict(cls=cls, params=params))
                    masks.append(m)
                    break

    img, wmasks, quality, Hm = capture(prev, st.alpha, masks, rng, CFG["capture"], geo)
    stem = f"sheet_{idx:06d}"
    Image.fromarray(img).save(os.path.join(out, "images", split, stem + ".jpg"), quality=quality)

    H, W = img.shape[:2]
    records, lines, union = [], [], np.zeros((H, W), np.uint8)
    inst = np.zeros((H, W), np.uint8)  # pixel = 1 + index into records; later defects win where they overlap
    for d, m in zip(defects, wmasks):
        ys, xs = np.nonzero(m)
        if len(xs) == 0:  # warped out of frame
            continue
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        ci = CLASSES.index(d["cls"])
        lines.append(f"{ci} {(x0 + x1) / 2 / W:.6f} {(y0 + y1) / 2 / H:.6f} {(x1 - x0) / W:.6f} {(y1 - y0) / H:.6f}")
        records.append(dict(cls=d["cls"], class_id=ci, bbox_xyxy=[x0, y0, x1, y1], area_px=int(m.sum()),
                            params=d["params"]))
        union |= m
        inst[m > 0] = len(records)
    with open(os.path.join(out, "labels", split, stem + ".txt"), "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    if records:
        Image.fromarray(union * 255).save(os.path.join(out, "masks", stem + ".png"))
        Image.fromarray(inst).save(os.path.join(out, "instance_masks", stem + ".png"))
    return dict(file=f"images/{split}/{stem}.jpg", stem=stem, split=split, design=design.name, kind=design.kind, side=design.side,
                width=W, height=H, defective=bool(records), jpeg_quality=quality, defects=records,
                sheet_homography=Hm.tolist())


# --------------------------------------------------------------------------- dataset assembly


def link(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def write_mvtec(out, records):
    """MVTec-AD layout per design: train/good, test/good|<defect>|combined, ground_truth/<defect>/*_mask.png."""
    root = os.path.join(out, "mvtec")
    for r in records:
        src = os.path.join(out, r["file"])
        cat = os.path.join(root, r["design"])
        if not r["defective"]:
            sub = "train/good" if r["split"] == "train" else "test/good"
            link(src, os.path.join(cat, sub, r["stem"] + ".jpg"))
            continue
        kinds = sorted({d["cls"] for d in r["defects"]})
        dname = kinds[0] if len(kinds) == 1 else "combined"
        link(src, os.path.join(cat, "test", dname, r["stem"] + ".jpg"))
        link(os.path.join(out, "masks", r["stem"] + ".png"),
             os.path.join(cat, "ground_truth", dname, r["stem"] + "_mask.png"))


def write_previews(out, records, n):
    pal = [(230, 25, 75), (60, 180, 75), (255, 140, 0), (0, 130, 200), (145, 30, 180), (0, 200, 200),
           (240, 50, 230), (128, 128, 0)]
    os.makedirs(os.path.join(out, "preview"), exist_ok=True)
    f = font(22, "DejaVuSans-Bold.ttf")
    for r in [r for r in records if r["defective"]][:n]:
        im = Image.open(os.path.join(out, r["file"])).convert("RGB")
        dr = ImageDraw.Draw(im)
        for d in r["defects"]:
            x0, y0, x1, y1 = d["bbox_xyxy"]
            c = pal[d["class_id"] % len(pal)]
            dr.rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4], outline=c, width=3)
            dr.text((x0, max(0, y0 - 30)), d["cls"], fill=c, font=f)
        im.save(os.path.join(out, "preview", r["stem"] + ".jpg"), quality=88)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=300, help="number of sheets")
    ap.add_argument("--defect-rate", type=float, default=0.3, help="fraction of sheets with >=1 defect")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--rows", type=int, default=8)
    ap.add_argument("--width", type=int, default=2048, help="output image width in px")
    ap.add_argument("--designs", type=int, default=4, help="number of distinct plate designs")
    ap.add_argument("--style", choices=["banknote", "both", "testsheet"], default="banknote")
    ap.add_argument("--capture", choices=sorted(CAPTURE), default="mild")
    ap.add_argument("--split", type=float, nargs=3, default=[0.7, 0.15, 0.15], metavar=("TRAIN", "VAL", "TEST"))
    ap.add_argument("--class-weights", type=float, nargs=len(CLASSES), default=[1.0] * len(CLASSES),
                    metavar="W", help="sampling weight per class, order: " + " ".join(CLASSES))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--preview", type=int, default=12, help="annotated previews of defective sheets")
    ap.add_argument("--out", default=os.path.join(HERE, "dataset"))
    ap.add_argument("--overwrite", action="store_true", help="replace an existing dataset in --out")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    if os.path.exists(out) and os.listdir(out):
        if not (args.overwrite and os.path.exists(os.path.join(out, "annotations.json"))):
            raise SystemExit(f"{out} is not empty; pass --overwrite to replace a dataset generated by this script")
        shutil.rmtree(out)
    for split in ("train", "val", "test"):
        os.makedirs(os.path.join(out, "images", split), exist_ok=True)
        os.makedirs(os.path.join(out, "labels", split), exist_ok=True)
    os.makedirs(os.path.join(out, "masks"), exist_ok=True)
    os.makedirs(os.path.join(out, "instance_masks"), exist_ok=True)

    kinds = {"both": ["banknote", "testsheet"], "banknote": ["banknote"], "testsheet": ["testsheet"]}[args.style]
    cfg = dict(seed=args.seed, width=args.width, cols=args.cols, rows=args.rows, capture=args.capture, out=out,
               design_kinds=[kinds[d % len(kinds)] for d in range(args.designs)], class_weights=args.class_weights)
    rng = np.random.default_rng(args.seed)
    defective = np.zeros(args.count, bool)
    defective[rng.permutation(args.count)[:int(round(args.count * args.defect_rate))]] = True
    p = np.array(args.split) / sum(args.split)
    splits = rng.choice(["train", "val", "test"], size=args.count, p=p)
    # Banknote designs alternate front/back sheets so each design is half fronts, half backs.
    jobs = [(i, str(splits[i]), i % args.designs,
             "back" if cfg["design_kinds"][i % args.designs] == "banknote" and (i // args.designs) % 2 else "front",
             bool(defective[i])) for i in range(args.count)]

    t0 = time.time()
    with Pool(args.workers, initializer=_init, initargs=(cfg,)) as pool:
        records = []
        for k, r in enumerate(pool.imap(render_sheet, jobs, chunksize=2), 1):
            records.append(r)
            if k % 50 == 0 or k == len(jobs):
                print(f"  {k}/{len(jobs)} sheets  ({time.time() - t0:.0f}s)", flush=True)

    write_mvtec(out, records)
    write_previews(out, records, args.preview)
    with open(os.path.join(out, "data.yaml"), "w") as f:
        f.write(f"# YOLO dataset generated by synthetic_print/generate.py\npath: {out}\n"
                "train: images/train\nval: images/val\ntest: images/test\nnames:\n")
        f.writelines(f"  {i}: {c}\n" for i, c in enumerate(CLASSES))
    geo = Geometry(args.width, args.cols, args.rows)
    with open(os.path.join(out, "annotations.json"), "w") as f:
        json.dump(dict(classes=CLASSES, config={**vars(args), "out": out},
                       sheet_px=[geo.sw, geo.sh], note_px=[geo.nw, geo.nh], images=records),
                  f, default=_json_default, indent=1)

    n_def = sum(r["defective"] for r in records)
    counts = {c: 0 for c in CLASSES}
    for r in records:
        for d in r["defects"]:
            counts[d["cls"]] += 1
    print(f"done: {len(records)} sheets ({n_def} defective) in {time.time() - t0:.0f}s -> {out}")
    print("  instances: " + ", ".join(f"{c}={n}" for c, n in counts.items()))
    print("  splits: " + ", ".join(f"{s}={sum(r['split'] == s for r in records)}" for s in ("train", "val", "test")))


if __name__ == "__main__":
    main()

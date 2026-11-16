#!/usr/bin/env python3
"""The Barrel -- a second entry for make.fun ai-excited-01.

Concept: a colossal curling wave swallows the sun. The sun shines
through the barrel's tube, and a tiny surfer is silhouetted against
it inside the opening. One monumental diagonal shape, hot sun against
cold teal, built to read at 300 px and to hit at full size.

Pure stdlib, fully deterministic: fixed seed, no clock, no network,
no unseeded randomness. Renders at 2x and box-downsamples for smooth
edges, then writes a PNG to $MAKEFUN_OUTPUT_DIR/entry.png
(falling back to /output/entry.png).
"""

import math
import os
import random
import struct
import zlib

W = H = 1024
SS = 2  # supersample factor: render at 2048, downsample to 1024
RW, RH = W * SS, H * SS
SEED = 20261116

# --- palette (design space, 1024 x 1024) ---
SKY_STOPS = [(0, (18, 9, 45)), (190, (52, 22, 78)), (290, (96, 32, 92)),
             (350, (168, 52, 84)), (390, (232, 93, 61)), (640, (255, 122, 60))]
SEA_TOP = (4, 34, 43)
SEA_BOT = (2, 18, 26)
SUN_X, SUN_Y = 620, 440
SUN_R = 110
SUN_HALO = 200
SUN_CORE = (255, 233, 168)
SUN_EDGE = (255, 138, 61)
HALO_RGB = (255, 150, 70)
WAVE = (11, 85, 104)
WAVE_LT = (23, 168, 196)
WAVE_DK = (7, 56, 74)
FOAM = (244, 254, 255)
SPRAY = (223, 246, 250)
INK = (10, 15, 20)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def lerp(a, b, t):
    return a + (b - a) * t


def lerp3(c0, c1, t):
    return (int(round(lerp(c0[0], c1[0], t))),
            int(round(lerp(c0[1], c1[1], t))),
            int(round(lerp(c0[2], c1[2], t))))


def grad_at(stops, y):
    if y <= stops[0][0]:
        return stops[0][1]
    for i in range(1, len(stops)):
        y0, c0 = stops[i - 1]
        y1, c1 = stops[i]
        if y <= y1:
            t = (y - y0) / (y1 - y0) if y1 > y0 else 0.0
            return lerp3(c0, c1, t)
    return stops[-1][1]


def sky_at(x, y):
    """Full sky colour at design coords (x, y), sun and halo included."""
    c = grad_at(SKY_STOPS, y if y < 640 else 640)
    dx = x - SUN_X
    dy = y - SUN_Y
    d2 = dx * dx + dy * dy
    if d2 < SUN_HALO * SUN_HALO:
        d = math.sqrt(d2)
        if d < SUN_R:
            c = lerp3(SUN_CORE, SUN_EDGE, d / SUN_R)
        else:
            g = (1.0 - d / SUN_HALO) ** 2 * 0.55
            c = (clamp(int(c[0] + HALO_RGB[0] * g), 0, 255),
                 clamp(int(c[1] + HALO_RGB[1] * g), 0, 255),
                 clamp(int(c[2] + HALO_RGB[2] * g), 0, 255))
    return c


def bez(p0, p1, p2, p3, n):
    """Sample a cubic bezier into n+1 points (design coords)."""
    out = []
    for i in range(n + 1):
        t = i / n
        mt = 1.0 - t
        x = (mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0]
             + 3 * mt * t * t * p2[0] + t ** 3 * p3[0])
        y = (mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1]
             + 3 * mt * t * t * p2[1] + t ** 3 * p3[1])
        out.append((x, y))
    return out


class Canvas:
    """RGB framebuffer at render resolution (RW x RH)."""

    def __init__(self):
        self.w, self.h = RW, RH
        self.buf = bytearray(RW * RH * 3)

    def span(self, y, x0, x1, rgb):
        """Fill row y, columns [x0, x1), solid rgb (render coords)."""
        if x1 <= x0 or y < 0 or y >= self.h:
            return
        if x0 < 0:
            x0 = 0
        if x1 > self.w:
            x1 = self.w
        if x1 <= x0:
            return
        row = y * self.w * 3
        self.buf[row + x0 * 3: row + x1 * 3] = bytes(rgb) * (x1 - x0)

    def vgrad(self, y0, y1, color_fn):
        """Vertical gradient: color_fn(design y) -> rgb, rows y0..y1."""
        for y in range(y0, y1):
            self.span(y, 0, self.w, color_fn(y / SS))

    def disc(self, cx, cy, r, rgb):
        """Solid filled disc, hard edge (supersampling smooths it)."""
        x0 = max(0, int(math.ceil(cx - r)))
        x1 = min(self.w, int(math.floor(cx + r)) + 1)
        y0 = max(0, int(math.ceil(cy - r)))
        y1 = min(self.h, int(math.floor(cy + r)) + 1)
        r2 = r * r
        for y in range(y0, y1):
            dy = (y + 0.5) - cy
            rem = r2 - dy * dy
            if rem <= 0:
                continue
            dx = math.sqrt(rem)
            self.span(y, int(math.ceil(cx - dx)),
                      int(math.floor(cx + dx)) + 1, rgb)

    def disc_a(self, cx, cy, r, rgb, alpha):
        """Alpha-blended filled disc (alpha 0..255)."""
        x0 = max(0, int(math.floor(cx - r)))
        x1 = min(self.w, int(math.ceil(cx + r)) + 1)
        y0 = max(0, int(math.floor(cy - r)))
        y1 = min(self.h, int(math.ceil(cy + r)) + 1)
        r2 = r * r
        buf = self.buf
        w = self.w
        ia = 255 - alpha
        for y in range(y0, y1):
            dy = (y + 0.5) - cy
            for x in range(x0, x1):
                dx = (x + 0.5) - cx
                if dx * dx + dy * dy <= r2:
                    i = (y * w + x) * 3
                    buf[i] = (rgb[0] * alpha + buf[i] * ia) // 255
                    buf[i + 1] = (rgb[1] * alpha + buf[i + 1] * ia) // 255
                    buf[i + 2] = (rgb[2] * alpha + buf[i + 2] * ia) // 255

    def ellipse(self, cx, cy, rx, ry, rgb):
        """Solid filled axis-aligned ellipse."""
        x0 = max(0, int(math.ceil(cx - rx)))
        x1 = min(self.w, int(math.floor(cx + rx)) + 1)
        y0 = max(0, int(math.ceil(cy - ry)))
        y1 = min(self.h, int(math.floor(cy + ry)) + 1)
        for y in range(y0, y1):
            dy = ((y + 0.5) - cy) / ry
            rem = 1.0 - dy * dy
            if rem <= 0:
                continue
            dx = rx * math.sqrt(rem)
            self.span(y, int(math.ceil(cx - dx)),
                      int(math.floor(cx + dx)) + 1, rgb)

    def ellipse_a(self, cx, cy, rx, ry, rgb, alpha):
        """Alpha-blended axis-aligned ellipse."""
        x0 = max(0, int(math.floor(cx - rx)))
        x1 = min(self.w, int(math.ceil(cx + rx)) + 1)
        y0 = max(0, int(math.floor(cy - ry)))
        y1 = min(self.h, int(math.ceil(cy + ry)) + 1)
        buf = self.buf
        w = self.w
        ia = 255 - alpha
        for y in range(y0, y1):
            dy = ((y + 0.5) - cy) / ry
            rem = 1.0 - dy * dy
            if rem <= 0:
                continue
            dx = rx * math.sqrt(rem)
            xa = max(x0, int(math.ceil(cx - dx)))
            xb = min(x1, int(math.floor(cx + dx)) + 1)
            for x in range(xa, xb):
                i = (y * w + x) * 3
                buf[i] = (rgb[0] * alpha + buf[i] * ia) // 255
                buf[i + 1] = (rgb[1] * alpha + buf[i + 1] * ia) // 255
                buf[i + 2] = (rgb[2] * alpha + buf[i + 2] * ia) // 255

    def fill_poly(self, pts, rgb):
        """Scanline fill of a polygon; pts are (x, y) render coords."""
        n = len(pts)
        ys = [p[1] for p in pts]
        y0 = max(0, int(math.ceil(min(ys))))
        y1 = min(self.h, int(math.floor(max(ys))) + 1)
        edges = []
        for i in range(n):
            x0, ya = pts[i]
            x1, yb = pts[(i + 1) % n]
            if ya != yb:
                edges.append((ya, yb, x0, x1))
        for y in range(y0, y1):
            yc = y + 0.5
            xs = []
            for (ya, yb, xa, xb) in edges:
                if (ya <= yc < yb) or (yb <= yc < ya):
                    t = (yc - ya) / (yb - ya)
                    xs.append(xa + t * (xb - xa))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                self.span(y, int(math.ceil(xs[i])),
                          int(math.floor(xs[i + 1])) + 1, rgb)

    def stroke(self, pts, width, rgb):
        """Thick polyline drawn as overlapping discs (render coords)."""
        r = width / 2.0
        for (x, y) in pts:
            self.disc(x, y, r, rgb)


def densify(pts, step):
    """Insert points so no two consecutive points are more than
    step apart (design coords): keeps a disc-based stroke smooth."""
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(d / step)))
        for i in range(1, n + 1):
            t = i / n
            out.append((a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t))
    return out


def scale_pts(pts):
    return [(x * SS, y * SS) for (x, y) in pts]


def offset_band(pts, dist, ref):
    """Polygon band: curve pts plus the same curve offset by dist
    toward ref (a point on the interior side). The offset tapers to
    zero at both ends so the band comes to a point. Design coords."""
    off = []
    n = len(pts)
    for i in range(n):
        p0 = pts[max(0, i - 1)]
        p1 = pts[min(n - 1, i + 1)]
        tx, ty = p1[0] - p0[0], p1[1] - p0[1]
        leng = math.hypot(tx, ty) or 1.0
        tx, ty = tx / leng, ty / leng
        nx, ny = -ty, tx
        if nx * (ref[0] - pts[i][0]) + ny * (ref[1] - pts[i][1]) < 0:
            nx, ny = -nx, -ny
        taper = math.sin(math.pi * i / max(1, n - 1)) ** 0.6
        d = dist * taper
        off.append((pts[i][0] + nx * d, pts[i][1] + ny * d))
    return pts + off[::-1]


def draw():
    rng = random.Random(SEED)
    cv = Canvas()

    # --- sky: vertical gradient, then sun disc + halo ---
    cv.vgrad(0, int(640 * SS), lambda y: grad_at(SKY_STOPS, y))
    cv.vgrad(int(640 * SS), RH, lambda y: lerp3(SEA_TOP, SEA_BOT,
                                                (y - 640) / 384.0))
    # sun + halo, painted per pixel inside the halo circle only
    hx0 = int((SUN_X - SUN_HALO) * SS)
    hx1 = int((SUN_X + SUN_HALO) * SS)
    hy0 = int((SUN_Y - SUN_HALO) * SS)
    hy1 = int((SUN_Y + SUN_HALO) * SS)
    buf = cv.buf
    w = cv.w
    halo2 = SUN_HALO * SUN_HALO
    for y in range(max(0, hy0), min(RH, hy1)):
        dy = y / SS - SUN_Y
        for x in range(max(0, hx0), min(RW, hx1)):
            dx = x / SS - SUN_X
            if dx * dx + dy * dy < halo2:
                c = sky_at(x / SS, y / SS)
                i = (y * w + x) * 3
                buf[i] = c[0]
                buf[i + 1] = c[1]
                buf[i + 2] = c[2]

    # --- the wave: one massive curling mass ---
    back = bez((-20, 1040), (40, 780), (150, 520), (330, 330), 40)
    back += bez((330, 330), (480, 190), (600, 150), (760, 150), 30)[1:]
    lip = bez((760, 150), (880, 150), (980, 240), (975, 360), 30)
    lip += bez((975, 360), (970, 430), (940, 460), (890, 470), 16)[1:]
    under = bez((890, 470), (830, 480), (760, 470), (700, 480), 12)
    face = bez((700, 480), (560, 560), (420, 720), (280, 880), 40)
    face += bez((280, 880), (180, 990), (80, 1030), (-20, 1040), 16)[1:]
    wave_pts = back + lip[1:] + under[1:] + face[1:]
    cv.fill_poly(scale_pts(wave_pts), WAVE)

    # --- face shading: light band high on the face, dark band at base ---
    face_band = offset_band(face[:41], 120, (400, 300))
    cv.fill_poly(scale_pts(face_band), WAVE_LT)
    base_band = offset_band(face[20:], 80, (400, 300))
    cv.fill_poly(scale_pts(base_band), WAVE_DK)

    # --- foam lip: thick white stroke along the crest, tapering ---
    # densified so the disc-based stroke reads as one smooth band
    lip_pts = densify(lip + under, 4.0)
    n_lip = len(lip_pts)
    for i, (x, y) in enumerate(lip_pts):
        r = lerp(13.0, 7.0, i / max(1, n_lip - 1))
        cv.disc(x * SS, y * SS, r * SS, FOAM)

    # --- the barrel tube: foam rim, then sky + sun shining through ---
    cv.ellipse(600 * SS, 420 * SS, 140 * SS, 88 * SS, FOAM)
    tx0 = int((600 - 130) * SS)
    tx1 = int((600 + 130) * SS)
    ty0 = int((420 - 80) * SS)
    ty1 = int((420 + 80) * SS)
    for y in range(max(0, ty0), min(RH, ty1)):
        dy = (y / SS - 420) / 80.0
        for x in range(max(0, tx0), min(RW, tx1)):
            dx = (x / SS - 600) / 130.0
            if dx * dx + dy * dy <= 1.0:
                c = sky_at(x / SS, y / SS)
                i = (y * w + x) * 3
                buf[i] = c[0]
                buf[i + 1] = c[1]
                buf[i + 2] = c[2]

    # --- the surfer: tiny dark silhouette inside the tube ---
    board = [(556, 508), (622, 492), (626, 497), (560, 513)]
    cv.fill_poly(scale_pts(board), INK)
    body = [(584, 470), (594, 468), (599, 492), (587, 494)]
    cv.fill_poly(scale_pts(body), INK)
    cv.disc(591 * SS, 461 * SS, 5 * SS, INK)
    leg1 = [(588, 492), (594, 491), (596, 504), (590, 505)]
    leg2 = [(594, 492), (599, 491), (604, 500), (599, 503)]
    cv.fill_poly(scale_pts(leg1), INK)
    cv.fill_poly(scale_pts(leg2), INK)

    # --- spray: droplets fanning off the crest ---
    for _ in range(200):
        a = rng.uniform(-2.9, -0.2)
        d = rng.uniform(40, 260)
        x = 760 + math.cos(a) * d
        y = 150 + math.sin(a) * d * 0.7
        r = rng.uniform(1.5, 5.5) * (1.0 - d / 340.0) + 1.0
        cv.disc_a(x * SS, y * SS, r * SS, SPRAY,
                  int(rng.uniform(160, 255)))
    # bigger foam chunks right above the crest
    for _ in range(14):
        x = rng.uniform(700, 960)
        y = rng.uniform(110, 175)
        cv.disc_a(x * SS, y * SS, rng.uniform(7, 13) * SS, FOAM,
                  int(rng.uniform(200, 255)))
    # spray trailing off the lip's outer edge
    for _ in range(40):
        x = rng.uniform(930, 1020)
        y = rng.uniform(280, 420)
        cv.disc_a(x * SS, y * SS, rng.uniform(1.5, 4.5) * SS, SPRAY,
                  int(rng.uniform(140, 230)))

    # --- foam streaks at the base, where the wave meets the sea ---
    for _ in range(10):
        x = rng.uniform(0, 450)
        y = rng.uniform(955, 1015)
        cv.ellipse_a(x * SS, y * SS, rng.uniform(30, 60) * SS,
                     rng.uniform(3, 5) * SS, FOAM,
                     int(rng.uniform(200, 255)))

    # --- birds: three tiny chevrons in the sky, for scale ---
    for (bx, by, s) in [(240, 170, 14), (300, 140, 11), (355, 185, 12)]:
        chev = [(bx - s, by - s * 0.45), (bx, by), (bx + s, by - s * 0.45)]
        pts = []
        for i in range(len(chev) - 1):
            pts += bez(chev[i], chev[i], chev[i + 1], chev[i + 1], 6)[:-1]
        pts.append(chev[-1])
        cv.stroke(scale_pts(pts), 4 * SS, INK)

    # --- downsample 2x -> 1x (box average) ---
    out = bytearray(W * H * 3)
    src = cv.buf
    for y in range(H):
        r0 = (2 * y) * RW * 3
        r1 = (2 * y + 1) * RW * 3
        o = y * W * 3
        for x in range(W):
            i0 = r0 + 6 * x
            i1 = r1 + 6 * x
            out[o + 3 * x] = (src[i0] + src[i0 + 3] + src[i1]
                              + src[i1 + 3] + 2) // 4
            out[o + 3 * x + 1] = (src[i0 + 1] + src[i0 + 4] + src[i1 + 1]
                                  + src[i1 + 4] + 2) // 4
            out[o + 3 * x + 2] = (src[i0 + 2] + src[i0 + 5] + src[i1 + 2]
                                  + src[i1 + 5] + 2) // 4
    return out


def write_png(path, w, h, rgb):
    """Write an RGB bytearray as a PNG (Up filter, zlib level 9)."""
    raw = bytearray()
    prev = bytes(w * 3)
    for y in range(h):
        row = bytes(rgb[y * w * 3:(y + 1) * w * 3])
        filt = bytes((b - p) & 0xFF for b, p in zip(row, prev))
        raw.append(2)  # filter type: Up
        raw += filt
        prev = row
    comp = zlib.compress(bytes(raw), 9)

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        c += struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        return c

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", ihdr)
    png += chunk(b"IDAT", comp)
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def main():
    outdir = os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "entry.png")
    img = draw()
    write_png(path, W, H, img)
    print("wrote %s (%d bytes)" % (path, os.path.getsize(path)))


if __name__ == "__main__":
    main()

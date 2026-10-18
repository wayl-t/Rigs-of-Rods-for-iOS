#!/usr/bin/env python3
"""Generate the iOS home-screen icon set for the Rigs of Rods iPhone port.

Design decisions, in full:

* **Pure standard library.**  The build runs on a stock GitHub macOS runner
  with no Pillow and no numpy, and installing packages just to draw one icon
  would add a network dependency to the packaging step.  ``zlib`` + ``struct``
  are enough to write a valid PNG, and the artwork is drawn from signed
  shapes (an annulus, a hub disc, three rectangular spokes) rather than
  loaded from a file.

* **RGB only, no alpha.**  Color type 2, 8 bits per channel.  iOS refuses
  icons that carry an alpha channel - a fully opaque PNG is a hard
  requirement, not a style choice.

* **Supersampled.**  Each output pixel averages a 4x4 grid of analytic
  samples, which is the anti-aliasing: at 180 px a hard-edged circle would
  otherwise stair-step visibly on the home screen.

* **Every size is rendered from the same function of the pixel coordinate**,
  so the 120 px and the 180 px icon are the same picture, not a resample of
  each other (a downscale would have to interpolate, this does not).

Artwork: a dark slate background with a vertical gradient and a mild
vignette, carrying an amber steering wheel - ring, hub and three spokes -
shaded slightly brighter toward the top-left.  Deliberately geometric: it
stays legible at 60 px, needs no photography and no font, and renders
identically on any machine.

Usage::

    python3 make_app_icon.py --out <dir> [--master]

``--out`` is the .app bundle directory (the workflow calls it before
codesign seals the bundle).  ``--master`` additionally writes a 1024 px
master that is not installed - it exists for previews and for a future
asset-catalog migration.
"""

import argparse
import os
import struct
import zlib

# File name -> edge length in pixels.  The names are what Info.plist's
# CFBundleIconFiles entries resolve: iOS picks the @2x/@3x variant of
# "AppIcon60x60" by naming convention, and the iPad dictionary asks for
# "Icon-76"/"Icon-83.5" the same way.
ICON_FILES = {
    "AppIcon60x60@2x.png": 120,   # iPhone,  60 pt @2x
    "AppIcon60x60@3x.png": 180,   # iPhone,  60 pt @3x - the size the phone shows
    "Icon-76@2x.png":      152,   # iPad,    76 pt @2x
    "Icon-83.5@2x.png":    167,   # iPad Pro, 83.5 pt @2x
}
MASTER_FILE = ("AppIcon-1024.png", 1024)   # preview / future asset catalog

SUPERSAMPLE = 4   # 4x4 samples per output pixel

# --- artwork constants (all in normalised coordinates, half-size = 1.0) ---
BG_TOP = (34, 42, 58)
BG_BOT = (14, 17, 24)
AMBER = (238, 148, 38)
COLUMN_DOT = (24, 28, 38)

RING_IN2 = 0.525 * 0.525      # inner ring radius squared
RING_OUT2 = 0.675 * 0.675     # outer ring radius squared
HUB2 = 0.16 * 0.16            # hub disc radius squared
DOT2 = 0.055 * 0.055          # steering-column dot radius squared
SPOKE_W = 0.055               # spoke half-width
SPOKE_NEAR = 0.10             # spoke start (overlaps hub, unioned away)
SPOKE_FAR = 0.56              # spoke end (overlaps ring, unioned away)
SHADE = 0.18                  # strength of the top-left light


def _render(size, ss=SUPERSAMPLE):
    """Return ``size*size*3`` bytes of RGB pixel data."""
    n = size * ss
    step = 2.0 / n
    first = step * 0.5 - 1.0        # coordinate of the first sample centre
    buf = bytearray(size * size * 3)

    for y in range(size):
        base_y = first + (y * ss) * step
        row = y * size * 3
        for x in range(size):
            base_x = first + (x * ss) * step
            r = g = b = 0
            sy = base_y
            for _ in range(ss):
                sx = base_x
                for _ in range(ss):
                    px = sx
                    py = sy
                    r2 = px * px + py * py
                    if r2 <= DOT2:
                        cr, cg, cb = COLUMN_DOT
                    elif RING_IN2 <= r2 <= RING_OUT2 or r2 <= HUB2 \
                            or (abs(py) <= SPOKE_W and SPOKE_NEAR <= -px <= SPOKE_FAR) \
                            or (abs(py) <= SPOKE_W and SPOKE_NEAR <= px <= SPOKE_FAR) \
                            or (abs(px) <= SPOKE_W and SPOKE_NEAR <= py <= SPOKE_FAR):
                        lum = 1.0 + SHADE * (-px - py) * 0.7071
                        cr = min(255, int(AMBER[0] * lum + 0.5))
                        cg = min(255, int(AMBER[1] * lum + 0.5))
                        cb = min(255, int(AMBER[2] * lum + 0.5))
                    else:
                        ty = (py + 1.0) * 0.5
                        vig = 1.0 - 0.25 * r2
                        cr = int((BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * ty) * vig + 0.5)
                        cg = int((BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * ty) * vig + 0.5)
                        cb = int((BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * ty) * vig + 0.5)
                    r += cr
                    g += cg
                    b += cb
                    sx += step
                sy += step
            inv = 1.0 / (ss * ss)
            i = row + x * 3
            buf[i] = int(r * inv + 0.5)
            buf[i + 1] = int(g * inv + 0.5)
            buf[i + 2] = int(b * inv + 0.5)
    return buf


def _chunk(tag, data):
    out = struct.pack(">I", len(data)) + tag + data
    out += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return out


def write_png(path, size, pixels):
    """Write an 8-bit RGB (color type 2 - no alpha) PNG."""
    stride = size * 3
    raw = bytearray()
    for y in range(size):
        raw.append(0)                       # filter: None
        raw += pixels[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", ihdr)
           + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def main():
    ap = argparse.ArgumentParser(description="Generate the RoR iOS app icon set.")
    ap.add_argument("--out", required=True, help="output directory (the .app bundle)")
    ap.add_argument("--master", action="store_true",
                    help="also write the 1024 px master (preview only, never installed)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    jobs = list(ICON_FILES.items())
    if args.master:
        jobs.append(MASTER_FILE)

    for name, size in jobs:
        pixels = _render(size)
        path = os.path.join(args.out, name)
        write_png(path, size, pixels)
        print("icon: %s %dx%d %d bytes" % (name, size, size, os.path.getsize(path)))


if __name__ == "__main__":
    main()

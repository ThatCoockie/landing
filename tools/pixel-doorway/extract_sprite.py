#!/usr/bin/env python3
"""
extract_sprite.py - Holt eine Pixel-Art-Figur verlustarm aus einem (hochskalierten) Video.

Ablauf:
  1. Frame zum Zeitpunkt --time per ffmpeg extrahieren
  2. Auf das Original-Pixelraster zuruecksampeln (--pixel, --phase-x/--phase-y);
     pro Rasterzelle wird der Median der Zellmitte genommen (entfernt Kompressionsrauschen)
  3. Figur im Ausschnitt --box per Farbe freistellen (neutrale Grau-/Schwarztoene + Gelb),
     groesste zusammenhaengende Flaeche behalten, Loecher fuellen
  4. Manuelle Korrekturen: --erase x0,y0,x1,y1 (inklusive, Sprite-Koordinaten)
  5. Auf --colors Farben quantisieren und als transparentes PNG speichern

Standardwerte passen zum Referenzvideo (Figur in der Mitte, Szene 1).

    python extract_sprite.py --video referenz.mov -o assets/heroine.png
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image


def grab_frame(video: Path, t: float) -> Image.Image:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("Fehler: ffmpeg nicht gefunden.")
    png = subprocess.run([ffmpeg, "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1",
                          "-f", "image2pipe", "-vcodec", "png", "-"], check=True, capture_output=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGB")


def to_native(img: Image.Image, pixel: float, phx: float, phy: float) -> np.ndarray:
    a = np.asarray(img).astype(float)
    h, w, _ = a.shape
    nx, ny = int((w - phx) // pixel), int((h - phy) // pixel)
    out = np.zeros((ny, nx, 3), np.uint8)
    inset = 2  # Abstand zum Zellrand (Kompressionsartefakte an Kanten meiden)
    k = 3      # Groesse des Messfensters in der Zellmitte
    for j in range(ny):
        for i in range(nx):
            x0, y0 = int(phx + i * pixel + inset), int(phy + j * pixel + inset)
            out[j, i] = np.median(a[y0:y0 + k, x0:x0 + k].reshape(-1, 3), 0)
    return out


def flood(mask: np.ndarray, seeds) -> np.ndarray:
    h, w = mask.shape
    seen = np.zeros_like(mask, bool)
    q = deque()
    for s in seeds:
        if mask[s] and not seen[s]:
            seen[s] = True
            q.append(s)
    while q:
        y, x = q.popleft()
        for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                seen[yy, xx] = True
                q.append((yy, xx))
    return seen


def segment(rgb: np.ndarray) -> np.ndarray:
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    neutral = (np.abs(r - b) < 22) & (np.abs(r - g) < 20) & (r < 150)
    yellow = (r > 110) & (g > 100) & (b < 90) & (np.abs(r - g) < 60)
    m = neutral | yellow
    # groesste Komponente
    best, best_n = None, 0
    todo = m.copy()
    for y, x in zip(*np.nonzero(m)):
        if todo[y, x]:
            comp = flood(m, [(y, x)])
            todo &= ~comp
            if comp.sum() > best_n:
                best, best_n = comp, comp.sum()
    # Loecher fuellen: alles, was nicht vom Rand aus erreichbar ist, gehoert zur Figur
    h, w = m.shape
    border = [(y, x) for y in range(h) for x in (0, w - 1)] + [(y, x) for x in range(w) for y in (0, h - 1)]
    outside = flood(~best, border)
    return ~outside


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Pixel-Art-Figur aus Video extrahieren")
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("-o", "--out", type=Path, default=Path("assets/heroine.png"))
    ap.add_argument("--time", type=float, default=1.0, help="Zeitpunkt im Video (s)")
    ap.add_argument("--band-y", type=int, default=570, help="Oberkante des Bildstreifens im Video (px)")
    ap.add_argument("--pixel", type=float, default=7.35, help="Groesse eines Original-Pixels im Video")
    ap.add_argument("--phase-x", type=float, default=6.5)
    ap.add_argument("--phase-y", type=float, default=5.75)
    ap.add_argument("--box", default="40,28,72,101", help="Ausschnitt im Raster: x0,y0,x1,y1")
    ap.add_argument("--erase", action="append", default=None,
                    help="Rechteck x0,y0,x1,y1 im fertigen Sprite loeschen (mehrfach moeglich)")
    ap.add_argument("--colors", type=int, default=24, help="Palettengroesse")
    ap.add_argument("--accent", default="196,180,44",
                    help="Feste Farbe fuer die gelben Akzentlinien (R,G,B); leer = Originalton")
    a = ap.parse_args(argv)
    erase = a.erase if a.erase is not None else [
        # Standard-Korrekturen fuer das Referenzvideo: Bodenkante und Wandreste neben den Beinen
        "0,70,29,70", "10,69,15,69", "24,69,29,69", "23,53,29,68", "25,20,29,40",
    ]

    frame = grab_frame(a.video, a.time)
    band = frame.crop((0, a.band_y, frame.width, frame.height))
    native = to_native(band, a.pixel, a.phase_x, a.phase_y)
    x0, y0, x1, y1 = map(int, a.box.split(","))
    crop = native[y0:y1, x0:x1]
    mask = segment(crop)

    ys, xs = np.nonzero(mask)
    crop, mask = crop[ys.min():ys.max() + 1, xs.min():xs.max() + 1], mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    for rect in erase:
        ex0, ey0, ex1, ey1 = map(int, rect.split(","))
        mask[ey0:ey1 + 1, ex0:ex1 + 1] = False

    # Palette reduzieren (nur deckende Pixel zaehlen)
    crop = crop.copy()
    r, g, b = (crop[..., i].astype(int) for i in range(3))
    is_accent = mask & (r > 110) & (g > 100) & (b < 90)
    crop[~mask] = crop[mask][0]  # Hintergrund soll keine Palettenplaetze belegen
    rgb = Image.fromarray(crop)
    q = rgb.quantize(colors=a.colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")
    qa = np.asarray(q).copy()
    if a.accent:
        # Kompression daempft das Gelb; Akzentpixel auf eine klare Palettenfarbe setzen
        qa[is_accent] = tuple(map(int, a.accent.split(",")))
    out = np.dstack([qa, mask.astype(np.uint8) * 255])
    img = Image.fromarray(out, "RGBA")
    img = img.crop(img.getbbox())
    a.out.parent.mkdir(parents=True, exist_ok=True)
    img.save(a.out)
    print(f"Sprite gespeichert: {a.out} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()

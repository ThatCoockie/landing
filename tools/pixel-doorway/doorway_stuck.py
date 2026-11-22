#!/usr/bin/env python3
"""
doorway_stuck.py - Slapstick-Pixel-Art-Animation: Figur steckt im Tuerrahmen fest.

Die Figur ist ein Pixel-Sprite (assets/heroine.png, extrahiert mit extract_sprite.py).
Das Skript zerlegt das Sprite in horizontale Teile (Kopf, Oberkoerper, Becken, Beine)
und animiert diese pixelgenau (Verschieben, Scheren, horizontales Stauchen/Dehnen).
Gerendert wird in kleiner Basisaufloesung (256x144), hochskaliert per Nearest-Neighbor.

Exporte:
  * MP4  (ueber ffmpeg, Standard)
  * GIF  (optional, --gif)
  * Sprite-Sheet PNG (optional, --sheet)
  * Einzelbilder PNG (optional, --frames-dir)

Die Ausgabe ist deterministisch: gleiche Argumente (inkl. --seed) => gleiche Bilder.

Anpassen: Alle Stellschrauben stehen gesammelt in den Abschnitten
  1) PALETTE        - Farben der Umgebung und Effekte
  2) LAYOUT         - Aufloesung, Raum- und Tuergeometrie
  3) SPRITE         - Sprite-Datei, Aufteilung in Koerperteile, Proportionen
  4) PHASES         - Ablauf, Dauer und Bewegungsamplituden der Animation
"""

from __future__ import annotations

import argparse
import math
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent

# =============================================================================
# 1) PALETTE (Umgebung + Effekte; die Figur bringt ihre eigene Palette mit)
# -----------------------------------------------------------------------------
# Rampen: (dunkel/Schatten, Grundton, Licht)
# =============================================================================
OUTLINE = (24, 20, 26)
WHITE = (250, 248, 240)
WOOD = ((84, 50, 28), (132, 82, 42), (180, 124, 68))
WALLPAPER = ((150, 132, 108), (192, 176, 146), (210, 196, 166))
WAINSCOT = ((90, 60, 44), (118, 82, 58), (146, 106, 76))
FLOOR = ((72, 48, 36), (104, 72, 52), (130, 94, 66))
# Nebenzimmer hinter der Tuer bewusst HELL, damit die dunkle Figur sich gut abhebt
BACKROOM = ((150, 140, 130), (184, 174, 158), (214, 204, 184))
LAMP = ((210, 170, 80), (250, 222, 140), (255, 246, 210))
ACCENT = (196, 180, 44)        # Gelb der Figur, fuer Effekte wiederverwendet
SWEAT = (160, 214, 255)
DUST = (206, 194, 170)

# =============================================================================
# 2) LAYOUT (alle Werte in Basis-Pixeln vor dem Hochskalieren)
# =============================================================================
BASE_W, BASE_H = 256, 144      # x5 => 1280x720
FLOOR_Y = 126                  # Oberkante des Bodens (Fuesse stehen hier)
DOOR_CX = 128                  # Mittelpunkt der Tuer
DOOR_HALF_OPENING = 9          # halbe lichte Breite der Tueroeffnung (schmaler als das Becken!)
JAMB_W = 4                     # Breite der Tuerpfosten
DOOR_TOP = 48                  # Oberkante der Tueroeffnung
LINTEL_H = 5                   # Hoehe des Tuersturzes

# =============================================================================
# 3) SPRITE
# -----------------------------------------------------------------------------
# Zeilenbereiche (inklusive) der Koerperteile im Sprite. Die Bereiche ueberlappen
# absichtlich um 1-2 Zeilen: Wenn sich ein Teil verschiebt, entsteht keine Luecke.
# Zeichenreihenfolge: Beine -> Becken -> Tuerpfosten -> Oberkoerper -> Kopf.
# =============================================================================
SPRITE_PATH = HERE / "assets" / "heroine.png"
PART_HEAD = (0, 20)            # Helm/Kopf
PART_TORSO = (18, 41)          # Jacke inkl. Arme
PART_HIPS = (39, 53)           # Becken/Oberschenkel (wird im Tuerrahmen eingeklemmt)
PART_LEGS = (50, 69)           # Unterschenkel/Stiefel
SPRITE_PIVOT_X = 13.0          # horizontale Mitte der Figur im Sprite (fuer Stauchen/Platzieren)

# Cartoon-Uebertreibung: Becken wird zusaetzlich verbreitert. Das Profil ist eine
# weiche "Beule" ueber die Beckenzeilen (Mitte = HIP_BULGE_ROW).
HIP_WIDEN = 1.6                # 1.0 = Original-Sprite
HIP_BULGE_ROW = 46             # Sprite-Zeile mit der breitesten Stelle

# =============================================================================
# 4) PHASES (Ablauf)
# -----------------------------------------------------------------------------
# (Name, Dauer in Sekunden). --speed skaliert alle Dauern gemeinsam. Standard ~4,8 s.
# Die Pose je Phase steht in pose_at().
# =============================================================================
PHASES = [
    ("idle", 0.70),       # Feststecken, verdutzt ("?")
    ("grip", 0.50),       # ansetzen: Oberkoerper geht kurz zurueck, holt Schwung
    ("strain", 1.50),     # Kraftanstrengung: ziehen, druecken, wackeln
    ("slip", 0.35),       # ruckt ein kleines Stueck vor ("!")
    ("snap", 0.75),       # federt zurueck (gedaempfte Schwingung)
    ("exhausted", 1.00),  # Erschoepfung, Schnaufen ("~")
]

# Bewegungsamplituden (zusaetzlich mit --amplitude multipliziert)
WIGGLE_PX = 3.0        # seitliches Wackeln des Oberkoerpers (Pixel an der Schulter)
WIGGLE_CYCLES = 3.0    # Wackel-Zyklen in "strain"
SLIP_PX = 4.0          # wie weit sie beim Vorrutschen nach vorne kommt
HIP_SQUEEZE = 0.14     # wie stark das Becken beim Vorrutschen zusammengedrueckt wird
HIP_BOUNCE = 0.10      # wie stark das Becken beim Zurueckfedern ausbeult
SHAKE_PX = 1           # Zittern von Tuerrahmen/Figur bei Kraftanstrengung


# =============================================================================
# Hilfsfunktionen
# =============================================================================
def lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def clamp01(u: float) -> float:
    return max(0.0, min(1.0, u))


def ease_in_out(u: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * clamp01(u))


def ease_out(u: float) -> float:
    return 1 - (1 - clamp01(u)) ** 3


def dither_rect(d: ImageDraw.ImageDraw, box, color, every: int = 2) -> None:
    """Schachbrett-Dithering (typisch fuer Retro-Licht/Schatten-Verlaeufe)."""
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0 + (y % every), x1, every):
            d.point((x, y), fill=color)


def shaded_oval(d, cx, cy, rx, ry, ramp) -> None:
    """Kleines Oval mit Outline + 3-Stufen-Shading (fuer Lampen/Deko)."""
    cx, cy, rx, ry = round(cx), round(cy), round(rx), round(ry)
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=OUTLINE)
    d.ellipse([cx - rx + 1, cy - ry + 1, cx + rx - 1, cy + ry - 1], fill=ramp[0])
    d.ellipse([cx - rx + 1, cy - ry + 1, cx + rx - 2, cy + ry - 2], fill=ramp[1])
    d.point((cx - 1, cy - 1), fill=ramp[2])


# Mini-Pixelglyphen fuer Effekt-Blasen (1 = Pixel gesetzt)
GLYPHS = {
    "!": ["010", "010", "010", "000", "010"],
    "?": ["111", "001", "011", "000", "010"],
    "~": ["00000", "01001", "10110", "00000", "00000"],
}


def draw_glyph(d, x: int, y: int, ch: str, color) -> None:
    for j, row in enumerate(GLYPHS[ch]):
        for i, c in enumerate(row):
            if c == "1":
                d.point((x + i, y + j), fill=color)


# =============================================================================
# Hintergrund (statisch, einmal gerendert und je Frame kopiert)
# =============================================================================
def render_background() -> Image.Image:
    img = Image.new("RGB", (BASE_W, BASE_H), WALLPAPER[1])
    d = ImageDraw.Draw(img)
    wainscot_top = FLOOR_Y - 26

    # Tapete: vertikale Streifen + kleines Rautenmuster
    for x in range(0, BASE_W, 8):
        d.rectangle([x, 0, x + 2, FLOOR_Y], fill=WALLPAPER[2])
    for y in range(10, wainscot_top - 4, 10):
        for x in range(4 + (y // 10 % 2) * 4, BASE_W, 8):
            d.point((x, y), fill=WALLPAPER[0])
            d.point((x - 1, y + 1), fill=WALLPAPER[0])
            d.point((x + 1, y + 1), fill=WALLPAPER[0])
            d.point((x, y + 2), fill=WALLPAPER[0])

    # Deckenleiste
    d.rectangle([0, 0, BASE_W, 4], fill=WOOD[0])
    d.line([0, 5, BASE_W, 5], fill=OUTLINE)

    # Wandvertaefelung
    d.rectangle([0, wainscot_top, BASE_W, FLOOR_Y], fill=WAINSCOT[1])
    d.rectangle([0, wainscot_top, BASE_W, wainscot_top + 2], fill=WAINSCOT[2])
    d.line([0, wainscot_top + 3, BASE_W, wainscot_top + 3], fill=WAINSCOT[0])
    for x in range(5, BASE_W, 22):
        d.rectangle([x, wainscot_top + 7, x + 15, FLOOR_Y - 4], outline=WAINSCOT[0])
        d.line([x + 1, wainscot_top + 8, x + 14, wainscot_top + 8], fill=WAINSCOT[2])

    # Wandlampen mit gedithertem Lichtschein
    for lx in (52, 204):
        ly = 44
        for rad, col in ((22, WALLPAPER[2]), (13, LAMP[2])):
            for y in range(ly - rad, ly + rad):
                for x in range(lx - rad, lx + rad):
                    if (x - lx) ** 2 + (y - ly) ** 2 < rad * rad and (x + y) % 2 == 0:
                        d.point((x, y), fill=col)
        d.rectangle([lx - 2, ly + 2, lx + 2, ly + 9], fill=WOOD[0], outline=OUTLINE)
        shaded_oval(d, lx, ly - 2, 5, 5, LAMP)

    # Bilderrahmen (Deko)
    d.rectangle([76, 58, 98, 76], fill=WOOD[1], outline=OUTLINE)
    d.rectangle([79, 61, 95, 73], fill=(120, 170, 150))
    d.polygon([(79, 73), (85, 65), (90, 70), (92, 67), (95, 73)], fill=(70, 120, 90))

    # Boden: Dielen mit versetzten Fugen, nach vorne dunkler
    d.rectangle([0, FLOOR_Y, BASE_W, BASE_H], fill=FLOOR[1])
    d.line([0, FLOOR_Y, BASE_W, FLOOR_Y], fill=OUTLINE)
    for i, y in enumerate(range(FLOOR_Y + 5, BASE_H, 6)):
        d.line([0, y, BASE_W, y], fill=FLOOR[0])
        for x in range((i * 19) % 41, BASE_W, 41):
            d.line([x, y - 5, x, y - 1], fill=FLOOR[0])
    dither_rect(d, (0, BASE_H - 6, BASE_W, BASE_H), FLOOR[0])

    # Tueroeffnung: helles Nebenzimmer (Kontrast zur dunklen Figur)
    ox0, ox1 = DOOR_CX - DOOR_HALF_OPENING, DOOR_CX + DOOR_HALF_OPENING
    d.rectangle([ox0, DOOR_TOP, ox1, FLOOR_Y], fill=BACKROOM[1])
    dither_rect(d, (ox0, DOOR_TOP, ox1 + 1, DOOR_TOP + 12), BACKROOM[0])
    d.rectangle([ox0, FLOOR_Y - 6, ox1, FLOOR_Y], fill=BACKROOM[0])
    d.rectangle([ox0 - JAMB_W, FLOOR_Y, ox1 + JAMB_W, FLOOR_Y + 1], fill=WOOD[1], outline=OUTLINE)

    # Vignette an den Bildraendern
    for y in range(BASE_H):
        for x in list(range(0, 14)) + list(range(BASE_W - 14, BASE_W)):
            edge = min(x, BASE_W - 1 - x)
            if (x + y) % 2 == 0 and edge < 14 - (y % 3) * 3:
                r, g, b = img.getpixel((x, y))
                img.putpixel((x, y), (int(r * 0.8), int(g * 0.8), int(b * 0.85)))
    return img


def draw_door_frame(d: ImageDraw.ImageDraw, shake_x: int) -> None:
    """Pfosten + Sturz; pro Frame gezeichnet, weil sie UEBER dem Becken liegen."""
    for side in (-1, 1):
        inner = DOOR_CX + side * (DOOR_HALF_OPENING + 1)
        x0, x1 = sorted((inner, inner + side * (JAMB_W - 1)))
        x0 += shake_x
        x1 += shake_x
        d.rectangle([x0, DOOR_TOP - LINTEL_H, x1, FLOOR_Y], fill=WOOD[1], outline=OUTLINE)
        d.line([x0 + 1, DOOR_TOP, x0 + 1, FLOOR_Y - 1], fill=WOOD[2])
        for y in range(DOOR_TOP + 7, FLOOR_Y, 13):  # Holzmaserung
            d.point((x0 + 2, y), fill=WOOD[0])
            d.point((x0 + 2, y + 1), fill=WOOD[0])
    lx0 = DOOR_CX - DOOR_HALF_OPENING - JAMB_W - 2
    lx1 = DOOR_CX + DOOR_HALF_OPENING + JAMB_W + 2
    d.rectangle([lx0, DOOR_TOP - LINTEL_H - 1, lx1, DOOR_TOP], fill=WOOD[1], outline=OUTLINE)
    d.line([lx0 + 1, DOOR_TOP - LINTEL_H, lx1 - 1, DOOR_TOP - LINTEL_H], fill=WOOD[2])


# =============================================================================
# Sprite-Teile und pixelgenaue Verformung
# =============================================================================
class Figure:
    """Haelt die zerlegten Sprite-Teile und setzt sie pro Frame verformt zusammen."""

    def __init__(self, path: Path):
        if not path.exists():
            sys.exit(f"Fehler: Sprite {path} fehlt. Erst extract_sprite.py ausfuehren (siehe README).")
        self.sprite = Image.open(path).convert("RGBA")
        self.h = self.sprite.height

    def part(self, rows) -> Image.Image:
        y0, y1 = rows
        return self.sprite.crop((0, y0, self.sprite.width, y1 + 1))

    @staticmethod
    def deform(part: Image.Image, row_offset: int, dx_top: float, dx_bottom: float,
               sx_of_row) -> Image.Image:
        """Verformt ein Teil zeilenweise (Nearest-Neighbor, keine Zwischenfarben).

        dx_top/dx_bottom: horizontale Verschiebung oben/unten (dazwischen linear => Scherung)
        sx_of_row(sprite_row) -> horizontaler Skalierungsfaktor um SPRITE_PIVOT_X
        Ergebnis hat einen Rand von PAD Pixeln links/rechts.
        """
        pad = 16
        w, h = part.size
        out = Image.new("RGBA", (w + 2 * pad, h), (0, 0, 0, 0))
        src = part.load()
        dst = out.load()
        for y in range(h):
            v = y / max(1, h - 1)
            dx = lerp(dx_top, dx_bottom, v)
            sx = sx_of_row(row_offset + y)
            for X in range(w + 2 * pad):
                # inverse Abbildung: Zielspalte -> Quellspalte
                xs = (X - pad - dx - SPRITE_PIVOT_X) / sx + SPRITE_PIVOT_X
                xi = math.floor(xs + 0.5)
                if 0 <= xi < w:
                    px = src[xi, y]
                    if px[3]:
                        dst[X, y] = px
        return out


def hip_profile(widen: float):
    """Liefert sx(row): weiche Beule um HIP_BULGE_ROW mit Maximalfaktor `widen`."""
    y0, y1 = PART_HIPS
    half = max(HIP_BULGE_ROW - y0, y1 - HIP_BULGE_ROW) + 1

    def sx(row: int) -> float:
        k = max(0.0, 1 - abs(row - HIP_BULGE_ROW) / half)
        return 1 + (widen - 1) * math.sin(k * math.pi / 2)
    return sx


# =============================================================================
# Pose & Animation
# =============================================================================
@dataclass
class Pose:
    tx_top: float = 0.0        # seitliche Verschiebung des Oberkoerpers an den Schultern
    ty: float = 0.0            # vertikale Verschiebung von Oberkoerper/Kopf
    forward: float = 0.0       # Fortschritt "durch die Tuer" Richtung Kamera
    hip_scale: float = 1.0     # zusaetzlicher Faktor auf die Beckenbreite (<1 gequetscht)
    head_dx: float = 0.0
    head_dy: float = 0.0
    shake: int = 0             # Zitteramplitude (Pixel)
    bubble: str | None = None  # "!", "?", "~" oder None
    squeeze_marks: bool = False
    boing: float = 0.0         # Staerke der Feder-Bewegungslinien
    visor_glint: bool = False  # Lichtreflex auf dem Visier


def pose_at(phase: str, u: float, t: float, amp: float) -> Pose:
    """Pose fuer `phase` bei lokalem Fortschritt u in [0,1].

    Anfangs-/Endposen der Phasen passen nahtlos aneinander. `t` = globale Zeit (s),
    `amp` skaliert alle Bewegungen.
    """
    breathe = math.sin(t * 2 * math.pi * 1.2)

    if phase == "idle":
        # Steckt fest, schaut nach links/rechts, Fragezeichen erscheint
        look = round(math.sin(u * 2 * math.pi) * 1.5)
        return Pose(ty=round(breathe * 0.5), head_dx=look, bubble="?" if u > 0.45 else None)

    if phase == "grip":
        # Ansetzen: lehnt sich kurz zurueck (nach oben gestreckt), holt Schwung
        e = ease_in_out(u)
        return Pose(ty=-round(e * 2 * amp), head_dy=-round(e * 1))

    if phase == "strain":
        # Ziehen & Wackeln: Oberkoerper pendelt (Scherung), Pumpen hoch/runter,
        # Becken pulsiert leicht, alles zittert
        w = math.sin(u * 2 * math.pi * WIGGLE_CYCLES)
        pump = math.sin(u * 2 * math.pi * WIGGLE_CYCLES * 2)
        ramp_in = min(1.0, u * 4)
        return Pose(tx_top=w * WIGGLE_PX * amp * ramp_in,
                    ty=-2 * amp + round(pump * 1 * amp),
                    hip_scale=1 + 0.03 * pump * amp,
                    head_dx=w * 1.0 * amp * ramp_in, head_dy=-1,
                    shake=SHAKE_PX, squeeze_marks=True)

    if phase == "slip":
        # Ruck nach vorne: schneller Ease-Out, Becken quetscht sich zusammen
        e = ease_out(u)
        return Pose(ty=-2 * amp * (1 - e), forward=e * SLIP_PX * amp,
                    hip_scale=1 - HIP_SQUEEZE * e, head_dy=-1 * (1 - e),
                    bubble="!", squeeze_marks=True, visor_glint=True)

    if phase == "snap":
        # Zurueckfedern: gedaempfte Feder mit Ueberschwingen, Becken beult aus
        spring = math.exp(-4.5 * u) * math.cos(3 * math.pi * u)
        wob = math.sin(u * 14) * math.exp(-4 * u)
        return Pose(forward=SLIP_PX * amp * spring,
                    hip_scale=1 - HIP_SQUEEZE * max(0, spring) + HIP_BOUNCE * max(0, -spring) * 3,
                    tx_top=wob * 2 * amp, head_dx=wob * 1.5 * amp,
                    boing=math.exp(-3 * u))

    if phase == "exhausted":
        # Erschoepft: sackt zusammen, Kopf haengt, schweres Atmen
        e = ease_in_out(min(1.0, u / 0.4))
        return Pose(ty=round(lerp(0, 2, e) + breathe * 0.6), head_dy=round(lerp(0, 1, e)),
                    bubble="~" if u > 0.35 else None)

    raise ValueError(f"unbekannte Phase: {phase}")


# =============================================================================
# Effekte
# =============================================================================
@dataclass
class Particle:
    born: int
    life: int
    x: float
    y: float
    vx: float
    vy: float
    kind: str          # "sweat" | "dust"


def build_particles(timeline, fps: int, rng: random.Random, head_y: int, hip_y: int) -> list[Particle]:
    """Alle Partikel vorab erzeugen (deterministisch durch den Seed)."""
    parts: list[Particle] = []
    s = 12 / fps  # Geschwindigkeiten unabhaengig von der FPS halten
    for phase, f0, f1 in timeline:
        if phase in ("strain", "exhausted"):
            step = max(1, fps // (5 if phase == "strain" else 3))
            for f in range(f0, f1, step):
                side = rng.choice((-1, 1))
                parts.append(Particle(f, max(4, fps // 2), side * rng.uniform(6, 9), head_y + rng.uniform(-6, 0),
                                      side * rng.uniform(0.6, 1.2) * s, -rng.uniform(0.5, 1.0) * s, "sweat"))
        if phase == "slip":
            for side in (-1, 1):
                for _ in range(5):
                    parts.append(Particle(f0 + rng.randint(0, 1), max(5, int(fps * 0.6)),
                                          side * (DOOR_HALF_OPENING + JAMB_W), hip_y + rng.uniform(-5, 5),
                                          side * rng.uniform(0.6, 1.6) * s, rng.uniform(-0.5, 0.3) * s, "dust"))
    return parts


def draw_particles(d, parts, frame: int, head_dx: float) -> None:
    for p in parts:
        age = frame - p.born
        if not 0 <= age < p.life:
            continue
        if p.kind == "sweat":
            x = round(DOOR_CX + head_dx + p.x + p.vx * age)
            y = round(p.y + p.vy * age + 0.12 * age * age)
            d.point((x, y - 1), fill=SWEAT)
            d.rectangle([x - 1, y, x, y + 1], fill=SWEAT)
        else:
            r = max(0, round(2 - 2 * age / p.life))
            x = round(DOOR_CX + p.x + p.vx * age)
            y = round(p.y + p.vy * age)
            d.ellipse([x - r, y - r, x + r, y + r], fill=DUST)


def draw_bubble(d, x: int, y: int, ch: str) -> None:
    w = 8 if ch != "~" else 10
    d.rectangle([x, y, x + w, y + 8], fill=WHITE, outline=OUTLINE)
    d.polygon([(x + 1, y + 8), (x + 3, y + 8), (x, y + 11)], fill=WHITE, outline=OUTLINE)
    d.line([x + 1, y + 8, x + 2, y + 8], fill=WHITE)
    draw_glyph(d, x + 3 if ch != "~" else x + 3, y + 2, ch, OUTLINE)


def draw_squeeze_marks(d, hip_y: int, half_w: int, frame: int) -> None:
    """Spannungsstriche dort, wo das Becken seitlich gegen die Pfosten drueckt."""
    if frame % 2:
        return
    for side in (-1, 1):
        x = DOOR_CX + side * (half_w + 2)
        for dy in (-3, 1):
            d.line([x, hip_y + dy, x + side * 2, hip_y + dy - 1], fill=WHITE)


def draw_boing(d, strength: float, hip_y: int, half_w: int, frame: int) -> None:
    """Federnde Bewegungslinien am Becken nach dem Zurueckschnappen."""
    if strength < 0.15:
        return
    off = frame % 2
    for side in (-1, 1):
        x0 = DOOR_CX + side * (half_w + 2 + off)
        for i in range(3 if strength > 0.5 else 2):
            y = hip_y - 4 + i * 4
            d.line([x0, y, x0 + side * (2 + i), y], fill=OUTLINE)


def draw_floor_shadow(d, forward: float) -> None:
    rx = 14 + round(forward)
    for y in range(FLOOR_Y + 1, FLOOR_Y + 4):
        for x in range(DOOR_CX - rx, DOOR_CX + rx):
            if (x + y) % 2 == 0:
                d.point((x, y), fill=FLOOR[0])


# =============================================================================
# Rendering-Pipeline
# =============================================================================
def build_timeline(fps: int, speed: float):
    out, f = [], 0
    for name, secs in PHASES:
        n = max(1, round(secs / speed * fps))
        out.append((name, f, f + n))
        f += n
    return out


def render_frames(fps: int, speed: float, amp: float, seed: int, sprite: Path) -> list[Image.Image]:
    rng = random.Random(seed)
    bg = render_background()
    fig = Figure(sprite)
    top = FLOOR_Y - fig.h + 1                     # Sprite-Oberkante im Bild
    left = round(DOOR_CX - SPRITE_PIVOT_X)        # Sprite-Spalte 0 im Bild
    parts = {k: fig.part(r) for k, r in
             (("legs", PART_LEGS), ("hips", PART_HIPS), ("torso", PART_TORSO), ("head", PART_HEAD))}
    hip_y_img = top + HIP_BULGE_ROW
    timeline = build_timeline(fps, speed)
    particles = build_particles(timeline, fps, rng, top + 6, hip_y_img)
    unit = lambda row: 1.0  # noqa: E731  (keine Skalierung)
    frames: list[Image.Image] = []

    for phase, f0, f1 in timeline:
        for f in range(f0, f1):
            u = (f - f0) / max(1, (f1 - f0 - 1))
            p = pose_at(phase, u, f / fps, amp)
            img = bg.copy()
            d = ImageDraw.Draw(img)
            shake = ((1 if f % 2 else -1) * p.shake) if p.shake else 0
            by = round(p.forward * 0.5)       # Vorrutschen wirkt in Frontansicht als Absenken

            def paste(name, rows, dy, dx_top, dx_bottom, sx):
                layer = Figure.deform(parts[name], rows[0], dx_top, dx_bottom, sx)
                img.paste(layer, (left - 16 + shake, top + rows[0] + round(dy)), layer)

            draw_floor_shadow(d, p.forward)
            # 1) Beine (im Nebenzimmer, bleiben fast stehen)
            paste("legs", PART_LEGS, round(by * 0.5), 0, 0, unit)
            # 2) Becken: verbreitert + gequetscht/ausgebeult
            hips_sx = hip_profile(1 + (HIP_WIDEN - 1) * p.hip_scale + (p.hip_scale - 1))
            paste("hips", PART_HIPS, by, 0, 0, hips_sx)
            # 3) Tuerrahmen UEBER dem Becken => eingeklemmt
            draw_door_frame(d, shake)
            # 4) Oberkoerper: Scherung (oben tx_top, unten fast fest)
            tx = p.tx_top
            # Der Oberkoerper darf nur nach UNTEN (ueber das Becken) wandern, sonst wuerde
            # unter ihm der Jackensaum des Becken-Teils doppelt sichtbar. Hochziehen/Strecken
            # uebernimmt der Kopf (p.ty negativ => nur Kopf hebt sich, Hals wird sichtbar).
            torso_dy = by + max(0, p.ty)
            paste("torso", PART_TORSO, torso_dy, round(tx), round(tx * 0.2), unit)
            # 5) Kopf folgt den Schultern
            hx = round(tx * 1.1 + p.head_dx)
            head_dy = torso_dy + min(0, p.ty) * 0.5 + p.head_dy
            paste("head", PART_HEAD, head_dy, hx, hx, unit)

            # Effekte
            half_w = round(hips_sx(HIP_BULGE_ROW) * SPRITE_PIVOT_X)
            if p.squeeze_marks:
                draw_squeeze_marks(d, hip_y_img + by, half_w, f)
            draw_boing(d, p.boing, hip_y_img + by, half_w, f)
            head_top = top + round(head_dy)
            if p.visor_glint and f % 2 == 0:
                gx, gy = left + hx + 9 + shake, head_top + 9
                d.line([gx, gy, gx + 2, gy], fill=WHITE)
            draw_particles(d, particles, f, hx)
            if p.bubble:
                draw_bubble(d, left + hx + 22, head_top - 8, p.bubble)
            frames.append(img)
    return frames


def upscale(img: Image.Image, scale: int) -> Image.Image:
    # NEAREST = harte Pixelkanten, kein Weichzeichnen
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def export_mp4(frames, path: Path, fps: int, scale: int, loops: int, crf: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("Fehler: ffmpeg nicht gefunden. Bitte installieren (siehe README) oder --no-mp4 nutzen.")
    w, h = frames[0].width * scale, frames[0].height * scale
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "slow", "-tune", "animation", "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for _ in range(loops):
        for fr in frames:
            proc.stdin.write(upscale(fr, scale).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit("Fehler: ffmpeg ist fehlgeschlagen.")


def export_gif(frames, path: Path, fps: int, scale: int) -> None:
    big = [upscale(f, scale) for f in frames]
    big[0].save(path, save_all=True, append_images=big[1:], duration=round(1000 / fps),
                loop=0, optimize=False, disposal=1)


def export_sheet(frames, path: Path, cols: int, scale: int) -> None:
    w, h = frames[0].size
    rows = math.ceil(len(frames) / cols)
    sheet = Image.new("RGB", (cols * w, rows * h), (0, 0, 0))
    for i, f in enumerate(frames):
        sheet.paste(f, ((i % cols) * w, (i // cols) * h))
    upscale(sheet, scale).save(path)


# =============================================================================
# CLI
# =============================================================================
def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Rendert eine Pixel-Art-Slapstick-Animation (Figur steckt im Tuerrahmen fest).")
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("out"), help="Ausgabeordner (Standard: out)")
    ap.add_argument("--name", default="doorway_stuck", help="Basisname der Ausgabedateien")
    ap.add_argument("--sprite", type=Path, default=SPRITE_PATH, help="Sprite-PNG der Figur")
    ap.add_argument("--fps", type=int, default=12, help="Bildrate, empfohlen 12-15 (Standard: 12)")
    ap.add_argument("--scale", type=int, default=5, help="Hochskalierung, 5 => 1280x720 (Standard: 5)")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="Tempo-Faktor: >1 schneller/kuerzer, <1 langsamer (Standard: 1.0)")
    ap.add_argument("--amplitude", type=float, default=1.0,
                    help="Faktor fuer Bewegungsamplituden (Wackeln, Vorrutschen) (Standard: 1.0)")
    ap.add_argument("--seed", type=int, default=7, help="Zufalls-Seed fuer Partikel (reproduzierbar)")
    ap.add_argument("--loops", type=int, default=1, help="Wie oft die Animation im MP4 wiederholt wird")
    ap.add_argument("--crf", type=int, default=16, help="x264-Qualitaet (kleiner = besser, Standard: 16)")
    ap.add_argument("--no-mp4", action="store_true", help="Kein MP4 erzeugen")
    ap.add_argument("--gif", action="store_true", help="Zusaetzlich GIF exportieren")
    ap.add_argument("--gif-scale", type=int, default=3, help="Skalierung des GIFs (Standard: 3)")
    ap.add_argument("--sheet", action="store_true", help="Zusaetzlich Sprite-Sheet (PNG) exportieren")
    ap.add_argument("--sheet-cols", type=int, default=10, help="Spalten im Sprite-Sheet")
    ap.add_argument("--sheet-scale", type=int, default=1, help="Skalierung des Sprite-Sheets")
    ap.add_argument("--frames-dir", type=Path, help="Optional: Einzelbilder (Basisaufloesung) hier ablegen")
    a = ap.parse_args(argv)
    if a.fps < 1 or a.scale < 1 or a.speed <= 0 or a.loops < 1:
        ap.error("--fps, --scale und --loops muessen >= 1 sein, --speed > 0")
    return a


def main(argv=None) -> None:
    a = parse_args(argv)
    a.out_dir.mkdir(parents=True, exist_ok=True)

    frames = render_frames(a.fps, a.speed, a.amplitude, a.seed, a.sprite)
    print(f"{len(frames)} Frames gerendert ({len(frames) / a.fps:.2f} s @ {a.fps} FPS, "
          f"{BASE_W}x{BASE_H} -> {BASE_W * a.scale}x{BASE_H * a.scale})")

    written = []
    if not a.no_mp4:
        p = a.out_dir / f"{a.name}.mp4"
        export_mp4(frames, p, a.fps, a.scale, a.loops, a.crf)
        written.append(p)
    if a.gif:
        p = a.out_dir / f"{a.name}.gif"
        export_gif(frames, p, a.fps, a.gif_scale)
        written.append(p)
    if a.sheet:
        p = a.out_dir / f"{a.name}_sheet.png"
        export_sheet(frames, p, a.sheet_cols, a.sheet_scale)
        written.append(p)
    if a.frames_dir:
        a.frames_dir.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(frames):
            f.save(a.frames_dir / f"frame_{i:04d}.png")
        written.append(a.frames_dir)

    for p in written:
        print(f"  -> {p}")
    print("\nStart:  python doorway_stuck.py [--fps 15] [--gif] [--sheet]   (Hilfe: --help)")
    print("Benoetigt: Python >= 3.9, pillow + numpy (pip install -r requirements.txt), ffmpeg im PATH")


if __name__ == "__main__":
    main()

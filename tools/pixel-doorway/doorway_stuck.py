#!/usr/bin/env python3
"""
doorway_stuck.py - Slapstick-Pixel-Art-Animation: Figur steckt im Tuerrahmen fest.

Rendert eine kurze 16-Bit-artige Animation pixelgenau mit Pillow in einer kleinen
Basisaufloesung (320x180), skaliert sie per Nearest-Neighbor hoch und exportiert:

  * MP4  (ueber ffmpeg, Standard)
  * GIF  (optional, --gif)
  * Sprite-Sheet PNG (optional, --sheet)
  * Einzelbilder PNG (optional, --frames-dir)

Die Ausgabe ist deterministisch: gleiche Argumente (inkl. --seed) => gleiche Bilder.

Schnellstart:
    python doorway_stuck.py                       # -> out/doorway_stuck.mp4
    python doorway_stuck.py --gif --sheet         # zusaetzlich GIF + Sprite-Sheet
    python doorway_stuck.py --fps 15 --speed 0.8  # 15 FPS, 25 % langsamer

Anpassen: Alle Stellschrauben stehen gesammelt in den Abschnitten
  1) PALETTE        - Farben / Schattierungsrampen
  2) LAYOUT         - Raum- und Tuergeometrie
  3) PROPORTIONS    - Koerperproportionen der Figur
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

# =============================================================================
# 1) PALETTE
# -----------------------------------------------------------------------------
# Begrenzte Palette im 16-Bit-Stil. Jede "Rampe" hat drei Stufen:
#   (dunkel/Schatten, Grundton, Licht/Glanz)
# Wer Farben aendern will, aendert nur hier.
# =============================================================================
OUTLINE = (34, 24, 38)          # Einheitliche dunkle Outline fuer alle Figurenteile
WHITE = (250, 248, 240)

SKIN = ((196, 118, 90), (236, 168, 128), (252, 206, 168))
HAIR = ((92, 38, 30), (150, 64, 40), (204, 106, 60))
SHIRT = ((38, 86, 118), (58, 136, 168), (126, 198, 212))
PANTS = ((66, 48, 88), (102, 76, 132), (146, 118, 176))
SHOES = ((40, 30, 32), (70, 52, 50), (104, 80, 72))
EYE = (40, 30, 60)
BLUSH = (232, 120, 120)

WOOD = ((92, 54, 28), (138, 86, 42), (186, 128, 70))
WALLPAPER = ((150, 132, 108), (196, 180, 148), (214, 200, 170))
WAINSCOT = ((96, 64, 46), (124, 86, 60), (152, 110, 78))
FLOOR = ((78, 52, 38), (110, 76, 54), (136, 98, 68))
BACKROOM = ((22, 18, 32), (40, 34, 54), (60, 54, 78))
LAMP = ((210, 170, 80), (250, 222, 140), (255, 246, 210))

SWEAT = (160, 214, 255)
DUST = (206, 194, 170)

# =============================================================================
# 2) LAYOUT (Basisaufloesung, alle Werte in "echten" Pixeln vor dem Hochskalieren)
# =============================================================================
BASE_W, BASE_H = 320, 180
FLOOR_Y = 150                  # Oberkante des Bodens
DOOR_CX = 160                  # Mittelpunkt der Tuer
DOOR_HALF_OPENING = 23         # halbe lichte Breite der Tueroeffnung (schmal!)
JAMB_W = 8                     # Breite der Tuerpfosten
DOOR_TOP = 38                  # Oberkante der Tueroeffnung
LINTEL_H = 8                   # Hoehe des Tuersturzes

# =============================================================================
# 3) PROPORTIONS (Figur in Frontansicht, Werte = Radien/Offsets in Pixeln)
# -----------------------------------------------------------------------------
# Ueberzeichnete Cartoon-Proportionen. HIP_RX > DOOR_HALF_OPENING sorgt dafuer,
# dass das Becken seitlich hinter den Pfosten "eingeklemmt" ist.
# =============================================================================
HIP_Y = 112                    # Mittelpunkt Becken/Hueften
HIP_RX, HIP_RY = 31, 13        # Becken: halbe Breite / halbe Hoehe
WAIST_Y, WAIST_RX, WAIST_RY = 100, 12, 8
TRUNK_Y, TRUNK_RX, TRUNK_RY = 89, 15, 13
CHEST_Y, CHEST_DX, CHEST_RX, CHEST_RY = 88, 7, 10, 8   # zwei Ovale, Abstand +-CHEST_DX
SHOULDER_Y, SHOULDER_DX = 79, 16
HEAD_Y, HEAD_RX, HEAD_RY = 62, 10, 11
ARM_W = 4                      # Armdicke (ohne Outline)
HAND_R = 3

# =============================================================================
# 4) PHASES (Ablauf)
# -----------------------------------------------------------------------------
# (Name, Dauer in Sekunden). Mit --speed werden alle Dauern gemeinsam skaliert.
# Standard ~4,8 s. Die eigentliche Pose je Phase steht in pose_at().
# =============================================================================
PHASES = [
    ("idle", 0.70),       # Feststecken, schaut verdutzt
    ("grip", 0.50),       # greift die Pfosten
    ("strain", 1.50),     # Kraftanstrengung: zieht, drueckt, wackelt
    ("slip", 0.35),       # ruckt ein kleines Stueck vor
    ("snap", 0.75),       # federt zurueck (gedaempfte Schwingung)
    ("exhausted", 1.00),  # Erschoepfung, Schnaufen
]

# Bewegungsamplituden (werden zusaetzlich mit --amplitude multipliziert)
WIGGLE_PX = 3.0        # seitliches Wackeln des Oberkoerpers in strain
WIGGLE_CYCLES = 3.0    # Anzahl Wackel-Zyklen in strain
SLIP_PX = 4.0          # wie weit sie beim Vorrutschen kommt
HIP_SQUEEZE = 0.12     # Anteil, um den die Hueften beim Vorrutschen zusammengedrueckt werden
SHAKE_PX = 1           # Zittern von Tuerrahmen/Figur bei Kraftanstrengung


# =============================================================================
# Zeichen-Helfer
# =============================================================================
def lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def ease_in_out(u: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, u)))


def ease_out(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return 1 - (1 - u) ** 3


def shaded_oval(d: ImageDraw.ImageDraw, cx: float, cy: float, rx: float, ry: float, ramp) -> None:
    """Oval mit 1px-Outline und klassischem 3-Stufen-Retro-Shading.

    Schatten unten rechts, Glanzlicht oben links (Licht kommt von oben links).
    """
    cx, cy, rx, ry = round(cx), round(cy), max(1, round(rx)), max(1, round(ry))
    dark, mid, light = ramp
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=OUTLINE)
    if rx < 2 or ry < 2:
        return
    d.ellipse([cx - rx + 1, cy - ry + 1, cx + rx - 1, cy + ry - 1], fill=dark)
    # Grundton leicht nach oben links versetzt -> Schattenkante unten rechts
    d.ellipse([cx - rx + 1, cy - ry + 1, cx + rx - 2, cy + ry - 2], fill=mid)
    # Glanzlicht
    hx, hy = cx - rx * 0.35, cy - ry * 0.4
    hrx, hry = max(1, rx * 0.3), max(1, ry * 0.25)
    d.ellipse([round(hx - hrx), round(hy - hry), round(hx + hrx), round(hy + hry)], fill=light)


def shaded_blob(d: ImageDraw.ImageDraw, ovals, ramp) -> None:
    """Mehrere Ovale als EINE Form mit gemeinsamer Aussen-Outline.

    Erst alle Outlines, dann alle Fuellungen -> keine Outline-Linien im Inneren
    (wichtig fuer zusammengesetzte Koerperteile wie den Oberkoerper).
    ovals = [(cx, cy, rx, ry), ...]
    """
    ovals = [(round(cx), round(cy), max(2, round(rx)), max(2, round(ry))) for cx, cy, rx, ry in ovals]
    dark, mid, light = ramp
    for cx, cy, rx, ry in ovals:
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=OUTLINE)
    for cx, cy, rx, ry in ovals:
        d.ellipse([cx - rx + 1, cy - ry + 1, cx + rx - 1, cy + ry - 1], fill=dark)
    for cx, cy, rx, ry in ovals:
        d.ellipse([cx - rx + 1, cy - ry + 1, cx + rx - 2, cy + ry - 2], fill=mid)
    for cx, cy, rx, ry in ovals:
        hx, hy = cx - rx * 0.35, cy - ry * 0.4
        hrx, hry = max(1, rx * 0.3), max(1, ry * 0.25)
        d.ellipse([round(hx - hrx), round(hy - hry), round(hx + hrx), round(hy + hry)], fill=light)


def thick_segment(d: ImageDraw.ImageDraw, p0, p1, width: int, color) -> None:
    """Gliedmassen-Segment mit Outline (Outline-Linie, darauf Fuelllinie, runde Gelenke)."""
    p0 = (round(p0[0]), round(p0[1]))
    p1 = (round(p1[0]), round(p1[1]))
    ow = width + 2
    d.line([p0, p1], fill=OUTLINE, width=ow)
    for p in (p0, p1):
        r = ow // 2
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=OUTLINE)
    d.line([p0, p1], fill=color, width=width)
    for p in (p0, p1):
        r = width // 2
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=color)


# Mini-Pixelglyphen fuer Effekt-Blasen (1 = Pixel gesetzt)
GLYPHS = {
    "!": ["010", "010", "010", "000", "010"],
    "?": ["111", "001", "011", "000", "010"],
    "~": ["00000", "01001", "10110", "00000", "00000"],
}


def draw_glyph(d: ImageDraw.ImageDraw, x: int, y: int, ch: str, color) -> None:
    for j, row in enumerate(GLYPHS[ch]):
        for i, c in enumerate(row):
            if c == "1":
                d.point((x + i, y + j), fill=color)


def dither_rect(d: ImageDraw.ImageDraw, box, color, every: int = 2) -> None:
    """Schachbrett-Dithering (typisch fuer Retro-Licht/Schatten-Verlaeufe)."""
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0 + (y % every), x1, every):
            d.point((x, y), fill=color)


# =============================================================================
# Hintergrund (statisch, wird einmal gerendert und je Frame kopiert)
# =============================================================================
def render_background() -> Image.Image:
    img = Image.new("RGB", (BASE_W, BASE_H), WALLPAPER[1])
    d = ImageDraw.Draw(img)

    # Tapete: vertikale Streifen + kleines Rautenmuster
    for x in range(0, BASE_W, 10):
        d.rectangle([x, 0, x + 3, FLOOR_Y], fill=WALLPAPER[2])
    for y in range(12, 116, 12):
        for x in range(5 + (y // 12 % 2) * 5, BASE_W, 10):
            d.point((x, y), fill=WALLPAPER[0])
            d.point((x - 1, y + 1), fill=WALLPAPER[0])
            d.point((x + 1, y + 1), fill=WALLPAPER[0])
            d.point((x, y + 2), fill=WALLPAPER[0])

    # Deckenleiste
    d.rectangle([0, 0, BASE_W, 5], fill=WOOD[0])
    d.line([0, 6, BASE_W, 6], fill=OUTLINE)

    # Wandvertaefelung (Wainscot) unten
    d.rectangle([0, 118, BASE_W, FLOOR_Y], fill=WAINSCOT[1])
    d.rectangle([0, 118, BASE_W, 121], fill=WAINSCOT[2])
    d.line([0, 122, BASE_W, 122], fill=WAINSCOT[0])
    for x in range(6, BASE_W, 28):
        d.rectangle([x, 127, x + 20, FLOOR_Y - 5], outline=WAINSCOT[0])
        d.line([x + 1, 128, x + 19, 128], fill=WAINSCOT[2])

    # Wandlampen links/rechts mit gedithertem Lichtschein
    for lx in (58, 262):
        for rad, col in ((26, WALLPAPER[2]), (16, LAMP[2])):
            for y in range(50 - rad, 50 + rad):
                for x in range(lx - rad, lx + rad):
                    if (x - lx) ** 2 + (y - 50) ** 2 < rad * rad and (x + y) % 2 == 0:
                        d.point((x, y), fill=col)
        d.rectangle([lx - 2, 52, lx + 2, 60], fill=WOOD[0], outline=OUTLINE)
        shaded_oval(d, lx, 47, 6, 6, LAMP)

    # Bilderrahmen an der Wand (Deko)
    d.rectangle([92, 70, 118, 92], fill=WOOD[1], outline=OUTLINE)
    d.rectangle([95, 73, 115, 89], fill=(120, 170, 150))
    d.polygon([(95, 89), (102, 79), (108, 86), (111, 82), (115, 89)], fill=(70, 120, 90))

    # Boden: Dielen mit versetzten Fugen, nach vorne dunkler (gedithert)
    d.rectangle([0, FLOOR_Y, BASE_W, BASE_H], fill=FLOOR[1])
    d.line([0, FLOOR_Y, BASE_W, FLOOR_Y], fill=OUTLINE)
    for i, y in enumerate(range(FLOOR_Y + 6, BASE_H, 7)):
        d.line([0, y, BASE_W, y], fill=FLOOR[0])
        for x in range((i * 23) % 47, BASE_W, 47):
            d.line([x, y - 6, x, y - 1], fill=FLOOR[0])
    dither_rect(d, (0, 168, BASE_W, BASE_H), FLOOR[0])

    # Tueroeffnung: dunkles Nebenzimmer
    ox0, ox1 = DOOR_CX - DOOR_HALF_OPENING, DOOR_CX + DOOR_HALF_OPENING
    d.rectangle([ox0, DOOR_TOP, ox1, FLOOR_Y], fill=BACKROOM[1])
    dither_rect(d, (ox0, DOOR_TOP, ox1 + 1, DOOR_TOP + 30), BACKROOM[0])
    d.rectangle([ox0, FLOOR_Y - 8, ox1, FLOOR_Y], fill=BACKROOM[2])
    # Tuerschwelle
    d.rectangle([ox0 - JAMB_W, FLOOR_Y, ox1 + JAMB_W, FLOOR_Y + 2], fill=WOOD[1], outline=OUTLINE)

    # Leichte Vignette in den Ecken (Licht konzentriert sich auf die Tuer)
    for y in range(BASE_H):
        for x in list(range(0, 18)) + list(range(BASE_W - 18, BASE_W)):
            edge = min(x, BASE_W - 1 - x)
            if (x + y) % 2 == 0 and edge < 18 - (y % 3) * 4:
                r, g, b = img.getpixel((x, y))
                img.putpixel((x, y), (int(r * 0.8), int(g * 0.8), int(b * 0.85)))
    return img


def draw_door_frame(d: ImageDraw.ImageDraw, shake_x: int) -> None:
    """Pfosten + Sturz. Wird pro Frame gezeichnet, weil er UEBER dem Becken liegt."""
    for side in (-1, 1):
        inner = DOOR_CX + side * DOOR_HALF_OPENING
        x0, x1 = sorted((inner, inner + side * JAMB_W))
        x0 += shake_x
        x1 += shake_x
        d.rectangle([x0, DOOR_TOP - LINTEL_H, x1, FLOOR_Y], fill=WOOD[1], outline=OUTLINE)
        d.line([x0 + 2, DOOR_TOP, x0 + 2, FLOOR_Y - 2], fill=WOOD[2])
        d.line([x1 - 2, DOOR_TOP, x1 - 2, FLOOR_Y - 2], fill=WOOD[0])
        for y in range(DOOR_TOP + 9, FLOOR_Y, 17):  # Holzmaserung
            d.point((x0 + 4, y), fill=WOOD[0])
            d.point((x0 + 4, y + 1), fill=WOOD[0])
    lx0 = DOOR_CX - DOOR_HALF_OPENING - JAMB_W - 3
    lx1 = DOOR_CX + DOOR_HALF_OPENING + JAMB_W + 3
    d.rectangle([lx0, DOOR_TOP - LINTEL_H - 2, lx1, DOOR_TOP], fill=WOOD[1], outline=OUTLINE)
    d.line([lx0 + 1, DOOR_TOP - LINTEL_H - 1, lx1 - 1, DOOR_TOP - LINTEL_H - 1], fill=WOOD[2])
    d.line([lx0 + 1, DOOR_TOP - 1, lx1 - 1, DOOR_TOP - 1], fill=WOOD[0])


# =============================================================================
# Pose & Animation
# =============================================================================
@dataclass
class Pose:
    tx: float = 0.0            # seitliche Verschiebung des Oberkoerpers
    ty: float = 0.0            # vertikale Verschiebung des Oberkoerpers (Atmen/Zusammensacken)
    forward: float = 0.0       # Fortschritt "durch die Tuer" (Richtung Kamera)
    hip_sq: float = 1.0        # Hueftbreiten-Faktor (<1 gequetscht, >1 ausgebeult)
    lh: tuple = (-20.0, 102.0) # linke Hand (relativ zu DOOR_CX, absolut y)
    rh: tuple = (20.0, 102.0)  # rechte Hand
    head_dx: float = 0.0
    head_tilt: float = 0.0     # vertikaler Versatz des Kopfes
    eyes: str = "open"         # open | determined | squint | wide | half
    mouth: str = "frown"       # frown | grit | o | pant_open | pant_closed | flat
    blush: bool = False
    shake: int = 0             # Zitteramplitude in Pixeln
    bubble: str | None = None  # "!", "?", "~" oder None
    squeeze_marks: bool = False
    boing: float = 0.0         # Staerke der Feder-Bewegungslinien
    ponytail: float = 0.0      # Schwung des Pferdeschwanzes


HANDS_ON_HIPS = ((-21.0, 103.0), (21.0, 103.0))
HANDS_ON_JAMBS = ((-27.0, 92.0), (27.0, 92.0))
HANDS_HANGING = ((-23.0, 108.0), (23.0, 108.0))


def lerp2(a, b, u):
    return (lerp(a[0], b[0], u), lerp(a[1], b[1], u))


def pose_at(phase: str, u: float, t: float, amp: float) -> Pose:
    """Liefert die Pose fuer Phase `phase` bei lokalem Fortschritt u in [0,1].

    Alle Phasen sind so gebaut, dass Anfangs- und Endposen nahtlos aneinander passen.
    `t` ist die globale Zeit in Sekunden (fuer Atmen/Blinzeln), `amp` skaliert Bewegungen.
    """
    breathe = math.sin(t * 2 * math.pi * 1.2)

    if phase == "idle":
        # Verdutzt feststecken: Haende in die Hueften, Kopf schaut links/rechts.
        look = math.sin(u * 2 * math.pi) * 2
        return Pose(ty=round(breathe * 0.6), lh=HANDS_ON_HIPS[0], rh=HANDS_ON_HIPS[1],
                    head_dx=look, eyes="open", mouth="frown",
                    bubble="?" if u > 0.45 else None)

    if phase == "grip":
        # Haende wandern zu den Pfosten, Blick wird entschlossen.
        e = ease_in_out(u)
        return Pose(ty=-e * 1, lh=lerp2(HANDS_ON_HIPS[0], HANDS_ON_JAMBS[0], e),
                    rh=lerp2(HANDS_ON_HIPS[1], HANDS_ON_JAMBS[1], e),
                    eyes="determined", mouth="flat" if u < 0.5 else "grit")

    if phase == "strain":
        # Ziehen & Wackeln: Oberkoerper pendelt seitlich, Haende stemmen sich gegen
        # die Pfosten (leicht ruckelnd), Hueften pulsieren minimal.
        w = math.sin(u * 2 * math.pi * WIGGLE_CYCLES)
        push = math.sin(u * 2 * math.pi * WIGGLE_CYCLES * 2)
        ramp_in = min(1.0, u * 4)  # Wackeln faengt sanft an
        jitter = 1 if int(t * 24) % 2 else 0
        return Pose(tx=w * WIGGLE_PX * amp * ramp_in, ty=-1 + push * 0.8 * amp,
                    hip_sq=1 + 0.03 * push * amp,
                    lh=(HANDS_ON_JAMBS[0][0], HANDS_ON_JAMBS[0][1] + jitter),
                    rh=(HANDS_ON_JAMBS[1][0], HANDS_ON_JAMBS[1][1] - jitter),
                    head_dx=w * 1.5 * amp * ramp_in, head_tilt=1,
                    eyes="squint", mouth="grit", blush=True, shake=SHAKE_PX,
                    squeeze_marks=True, ponytail=-w * ramp_in)

    if phase == "slip":
        # Ruckartiges Vorrutschen: schneller Ease-Out, Hueften quetschen sich zusammen.
        e = ease_out(u)
        return Pose(ty=-1, forward=e * SLIP_PX * amp, hip_sq=1 - HIP_SQUEEZE * e,
                    lh=HANDS_ON_JAMBS[0], rh=HANDS_ON_JAMBS[1],
                    eyes="wide", mouth="o", blush=True, bubble="!", squeeze_marks=True)

    if phase == "snap":
        # Zurueckfedern: gedaempfte Schwingung (Feder) zurueck auf 0 mit Ueberschwingen.
        spring = math.exp(-4.5 * u) * math.cos(3 * math.pi * u)
        return Pose(ty=-1 + (1 - spring) * 1, forward=SLIP_PX * amp * spring,
                    hip_sq=1 - HIP_SQUEEZE * spring,
                    lh=HANDS_ON_JAMBS[0], rh=HANDS_ON_JAMBS[1],
                    tx=math.sin(u * 14) * math.exp(-4 * u) * 2 * amp,
                    eyes="squint", mouth="o" if u < 0.3 else "grit", blush=True,
                    boing=math.exp(-3 * u), ponytail=spring * 2)

    if phase == "exhausted":
        # Erschoepft: Haende sinken herab, Oberkoerper sackt zusammen, Schnaufen.
        e = ease_in_out(min(1.0, u / 0.4))
        pant = int(t * 6) % 2 == 0
        return Pose(ty=lerp(0, 2, e) + breathe * 0.5,
                    lh=lerp2(HANDS_ON_JAMBS[0], HANDS_HANGING[0], e),
                    rh=lerp2(HANDS_ON_JAMBS[1], HANDS_HANGING[1], e),
                    head_dx=0, head_tilt=lerp(0, 2, e),
                    eyes="half", mouth="pant_open" if pant else "pant_closed", blush=True,
                    bubble="~" if u > 0.35 else None)

    raise ValueError(f"unbekannte Phase: {phase}")


# =============================================================================
# Figur zeichnen
# =============================================================================
def draw_arm(d, shoulder, hand, side: int) -> None:
    """Zweigliedriger Arm; der Ellbogen knickt nach aussen ab (side = -1 links, +1 rechts)."""
    mx, my = (shoulder[0] + hand[0]) / 2, (shoulder[1] + hand[1]) / 2
    dx, dy = hand[0] - shoulder[0], hand[1] - shoulder[1]
    length = math.hypot(dx, dy) or 1
    bend = max(0.0, 14 - length) * 0.6 + 2      # kurzer Abstand -> staerker gebeugt
    nx, ny = -dy / length, dx / length           # Normale
    if nx * side < 0:
        nx, ny = -nx, -ny
    elbow = (mx + nx * bend, my + ny * bend)
    thick_segment(d, shoulder, elbow, ARM_W, SKIN[1])
    thick_segment(d, elbow, hand, ARM_W, SKIN[1])
    shaded_oval(d, hand[0], hand[1], HAND_R, HAND_R, SKIN)


def draw_eyes(d, hx: int, hy: int, state: str) -> None:
    for side in (-1, 1):
        ex, ey = hx + side * 4, hy + 1
        if state in ("open", "determined"):
            d.rectangle([ex - 1, ey - 2, ex + 1, ey + 1], fill=WHITE)
            d.rectangle([ex - (side < 0), ey - 1, ex + (side > 0), ey + 1], fill=EYE)
            if state == "determined":  # schraege Augenbrauen
                d.line([ex - 2 * side, ey - 5, ex + 2 * side, ey - 3], fill=OUTLINE)
            else:
                d.line([ex - 1, ey - 4, ex + 1, ey - 4], fill=HAIR[0])
        elif state == "squint":  # > <
            d.line([ex - 2 * side, ey - 2, ex + 1 * side, ey], fill=OUTLINE)
            d.line([ex + 1 * side, ey, ex - 2 * side, ey + 2], fill=OUTLINE)
        elif state == "wide":
            d.rectangle([ex - 1, ey - 3, ex + 1, ey + 1], fill=WHITE, outline=OUTLINE)
            d.point((ex, ey - 1), fill=EYE)
        elif state == "half":
            d.line([ex - 2, ey - 1, ex + 2, ey - 1], fill=OUTLINE)
            d.rectangle([ex - 1, ey, ex + 1, ey + 1], fill=EYE)


def draw_mouth(d, hx: int, hy: int, state: str) -> None:
    my = hy + 6
    if state == "frown":
        d.line([hx - 2, my + 1, hx + 2, my + 1], fill=OUTLINE)
        d.point((hx - 3, my + 2), fill=OUTLINE)
        d.point((hx + 3, my + 2), fill=OUTLINE)
    elif state == "flat":
        d.line([hx - 2, my, hx + 2, my], fill=OUTLINE)
    elif state == "grit":  # zusammengebissene Zaehne
        d.rectangle([hx - 3, my - 1, hx + 3, my + 2], fill=WHITE, outline=OUTLINE)
        d.line([hx - 2, my + 0, hx + 2, my + 0], fill=(200, 190, 190))
    elif state == "o":
        d.ellipse([hx - 2, my - 1, hx + 2, my + 3], fill=OUTLINE)
        d.point((hx, my + 2), fill=(170, 70, 80))
    elif state == "pant_open":
        d.ellipse([hx - 2, my, hx + 2, my + 3], fill=OUTLINE)
    elif state == "pant_closed":
        d.line([hx - 2, my + 1, hx + 2, my + 1], fill=OUTLINE)


def draw_character(d: ImageDraw.ImageDraw, p: Pose, shake_x: int) -> tuple[int, int]:
    """Zeichnet die Figur in fester Reihenfolge (hinten -> vorne).

    Gibt die Kopfposition zurueck (fuer Effekte). Die Tuerpfosten werden zwischen
    Becken und Oberkoerper gezeichnet, damit das Becken eingeklemmt wirkt.
    """
    cx = DOOR_CX + shake_x
    fwd = p.forward                    # Vorrutschen: Figur kommt ein Stueck Richtung Kamera
    by = fwd * 0.75                    # ... was in der Frontansicht als leichtes Absenken wirkt

    # --- Beine (stehen im Nebenzimmer, daher dunklere Rampe) --------------------
    leg_ramp = (PANTS[0], PANTS[0], PANTS[1])
    for side in (-1, 1):
        shaded_oval(d, cx + side * 11, 129 + by * 0.5, 8, 10, leg_ramp)
        shaded_oval(d, cx + side * 10, 141 + by * 0.3, 5, 7, leg_ramp)
        shaded_oval(d, cx + side * 11, FLOOR_Y - 2, 6, 3, SHOES)

    # --- Becken / Hueften (breiter als die Tueroeffnung) ----------------------
    hip_rx = HIP_RX * p.hip_sq
    hip_y = HIP_Y + by
    shaded_oval(d, cx, hip_y, hip_rx, HIP_RY, PANTS)
    d.line([cx, hip_y - 4, cx, hip_y + 6], fill=PANTS[0])          # Hosennaht
    d.rectangle([cx - 14, hip_y - HIP_RY + 1, cx + 14, hip_y - HIP_RY + 3], fill=PANTS[0])  # Bund

    return cx, by


def draw_upper_body(d: ImageDraw.ImageDraw, p: Pose, cx: int, by: float) -> tuple[int, int]:
    tx, ty = p.tx, p.ty + by

    # Oberkoerper (Shirt) als eine zusammenhaengende Form:
    # Taille, Rumpf und Brust (zwei Ovale, cartoonhaft, bekleidet)
    shaded_blob(d, [
        (cx + tx * 0.4, WAIST_Y + ty * 0.6 + by * 0.4, WAIST_RX, WAIST_RY),
        (cx + tx * 0.8, TRUNK_Y + ty, TRUNK_RX, TRUNK_RY),
        (cx + tx - CHEST_DX, CHEST_Y + ty, CHEST_RX, CHEST_RY),
        (cx + tx + CHEST_DX, CHEST_Y + ty, CHEST_RX, CHEST_RY),
    ], SHIRT)
    # Stofffalte zwischen den Brust-Ovalen und Saum ueber dem Bund
    fx = round(cx + tx)
    d.line([fx, round(CHEST_Y + ty - 2), fx, round(CHEST_Y + ty + 5)], fill=SHIRT[0])
    d.line([round(cx + tx * 0.4 - 8), round(WAIST_Y + ty * 0.6 + by * 0.4 + 5),
            round(cx + tx * 0.4 + 8), round(WAIST_Y + ty * 0.6 + by * 0.4 + 5)], fill=SHIRT[0])
    # Ausschnitt / Kragen
    nx, ny = round(cx + tx), round(TRUNK_Y - TRUNK_RY + ty)
    d.polygon([(nx - 4, ny + 1), (nx + 4, ny + 1), (nx, ny + 5)], fill=SKIN[1], outline=OUTLINE)

    # Kopf-Position
    hx = round(cx + tx * 1.2 + p.head_dx)
    hy = round(HEAD_Y + ty + p.head_tilt)

    # Hals
    d.rectangle([nx - 2, hy + HEAD_RY - 3, nx + 2, ny + 2], fill=SKIN[0], outline=OUTLINE)

    # Haare hinten + Pferdeschwanz (schwingt gegen die Bewegung)
    shaded_oval(d, hx + 1, hy - 1, HEAD_RX + 2, HEAD_RY + 1, HAIR)
    pt_x = hx + 12 + p.ponytail * 2 - tx * 0.5
    shaded_oval(d, pt_x, hy + 4, 4, 8, HAIR)

    # Gesicht
    shaded_oval(d, hx, hy + 1, HEAD_RX, HEAD_RY - 1, SKIN)
    # Pony
    d.ellipse([hx - HEAD_RX - 1, hy - HEAD_RY, hx + HEAD_RX + 1, hy - 3], fill=OUTLINE)
    d.ellipse([hx - HEAD_RX, hy - HEAD_RY + 1, hx + HEAD_RX, hy - 4], fill=HAIR[1])
    d.line([hx - 5, hy - HEAD_RY + 3, hx - 2, hy - HEAD_RY + 3], fill=HAIR[2])
    if p.blush:
        for side in (-1, 1):
            d.line([hx + side * 6 - 1, hy + 4, hx + side * 6 + 1, hy + 4], fill=BLUSH)
    draw_eyes(d, hx, hy, p.eyes)
    draw_mouth(d, hx, hy, p.mouth)

    # Arme zuletzt (liegen vor Rumpf und Tuerpfosten)
    for side, hand in ((-1, p.lh), (1, p.rh)):
        shoulder = (cx + tx * 0.9 + side * SHOULDER_DX, SHOULDER_Y + ty)
        hand_abs = (cx + hand[0], hand[1] + by)
        # Haende in Hueft-Position folgen dem Oberkoerper ein wenig
        if abs(hand[0]) < 25:
            hand_abs = (hand_abs[0] + tx * 0.5, hand_abs[1] + ty * 0.3)
        draw_arm(d, shoulder, hand_abs, side)
    return hx, hy


# =============================================================================
# Effekte
# =============================================================================
@dataclass
class Particle:
    born: int          # Frame der Entstehung
    life: int          # Lebensdauer in Frames
    x: float
    y: float
    vx: float
    vy: float
    kind: str          # "sweat" | "dust"


def build_particles(timeline, fps: int, rng: random.Random) -> list[Particle]:
    """Erzeugt alle Partikel vorab (deterministisch durch den Seed)."""
    parts: list[Particle] = []
    for phase, f0, f1 in timeline:
        if phase in ("strain", "exhausted"):
            # Schweisstropfen fliegen vom Kopf weg
            step = max(1, fps // (5 if phase == "strain" else 3))
            for f in range(f0, f1, step):
                side = rng.choice((-1, 1))
                parts.append(Particle(f, max(4, fps // 2), side * rng.uniform(8, 11),
                                      rng.uniform(-8, -3), side * rng.uniform(0.6, 1.2) * 12 / fps,
                                      -rng.uniform(0.6, 1.1) * 12 / fps, "sweat"))
        if phase == "slip":
            # Staubwolken an den Kontaktstellen Huefte/Pfosten
            for side in (-1, 1):
                for _ in range(5):
                    parts.append(Particle(f0 + rng.randint(0, 1), max(5, int(fps * 0.6)),
                                          side * DOOR_HALF_OPENING, HIP_Y + rng.uniform(-8, 8),
                                          side * rng.uniform(0.6, 1.6) * 12 / fps,
                                          rng.uniform(-0.5, 0.3) * 12 / fps, "dust"))
    return parts


def draw_particles(d, parts, frame: int, head_xy) -> None:
    hx, hy = head_xy
    for p in parts:
        age = frame - p.born
        if not 0 <= age < p.life:
            continue
        if p.kind == "sweat":
            x = hx + p.x + p.vx * age
            y = hy + p.y + p.vy * age + 0.25 * age * age * (12 / max(1, p.life)) * 0.3
            x, y = round(x), round(y)
            d.point((x, y - 1), fill=SWEAT)
            d.rectangle([x - 1, y, x + 1, y + 1], fill=SWEAT)
            d.point((x - 1, y + 1), fill=(110, 160, 220))
        else:
            r = max(1, round(3 - 2 * age / p.life))
            x = round(DOOR_CX + p.x + p.vx * age)
            y = round(p.y + p.vy * age)
            d.ellipse([x - r, y - r, x + r, y + r], fill=DUST)


def draw_bubble(d, hx: int, hy: int, ch: str) -> None:
    bx, by = hx + 12, hy - 20
    w = 9 if ch != "~" else 11
    d.rectangle([bx, by, bx + w, by + 9], fill=WHITE, outline=OUTLINE)
    d.polygon([(bx + 1, by + 9), (bx + 4, by + 9), (bx, by + 12)], fill=WHITE, outline=OUTLINE)
    d.line([bx + 1, by + 9, bx + 3, by + 9], fill=WHITE)
    draw_glyph(d, bx + 3, by + 2, ch, OUTLINE)


def draw_squeeze_marks(d, hip_rx: float, hip_y: float, frame: int) -> None:
    """Kleine Spannungsstriche dort, wo die Hueften gegen die Pfosten druecken."""
    if frame % 2:
        return
    for side in (-1, 1):
        x = DOOR_CX + side * (DOOR_HALF_OPENING + JAMB_W + 2)
        for dy in (-5, 0, 5):
            d.line([x, round(hip_y) + dy, x + side * 3, round(hip_y) + dy - 1], fill=WHITE)


def draw_boing(d, strength: float, hip_y: float, frame: int) -> None:
    """Federnde Bewegungslinien seitlich am Becken nach dem Zurueckschnappen."""
    if strength < 0.15:
        return
    n = 3 if strength > 0.5 else 2
    off = frame % 2
    for side in (-1, 1):
        x0 = DOOR_CX + side * (DOOR_HALF_OPENING + JAMB_W + 3 + off)
        for i in range(n):
            y = round(hip_y) - 6 + i * 6
            d.line([x0, y, x0 + side * (4 + 2 * i), y], fill=OUTLINE)


def draw_floor_shadow(d, forward: float) -> None:
    """Weicher (gedithert) Schatten vor der Tuer; waechst, wenn sie vorrutscht."""
    rx = 30 + round(forward)
    for y in range(FLOOR_Y + 1, FLOOR_Y + 5):
        for x in range(DOOR_CX - rx, DOOR_CX + rx):
            if (x + y) % 2 == 0:
                d.point((x, y), fill=FLOOR[0])


# =============================================================================
# Rendering-Pipeline
# =============================================================================
def build_timeline(fps: int, speed: float):
    """Wandelt PHASES in Frame-Intervalle um: [(name, start_frame, end_frame), ...]."""
    out, f = [], 0
    for name, secs in PHASES:
        n = max(1, round(secs / speed * fps))
        out.append((name, f, f + n))
        f += n
    return out


def render_frames(fps: int, speed: float, amp: float, seed: int) -> list[Image.Image]:
    rng = random.Random(seed)
    bg = render_background()
    timeline = build_timeline(fps, speed)
    particles = build_particles(timeline, fps, rng)
    frames: list[Image.Image] = []

    for phase, f0, f1 in timeline:
        for f in range(f0, f1):
            u = (f - f0) / max(1, (f1 - f0 - 1))
            t = f / fps
            pose = pose_at(phase, u, t, amp)

            img = bg.copy()
            d = ImageDraw.Draw(img)
            shake_x = (1 if f % 2 else -1) * pose.shake if pose.shake else 0

            draw_floor_shadow(d, pose.forward)
            cx, by = draw_character(d, pose, shake_x)
            draw_door_frame(d, shake_x)          # Pfosten UEBER dem Becken
            hip_y = HIP_Y + by
            if pose.squeeze_marks:
                draw_squeeze_marks(d, HIP_RX * pose.hip_sq, hip_y, f)
            draw_boing(d, pose.boing, hip_y, f)
            hx, hy = draw_upper_body(d, pose, cx, by)
            draw_particles(d, particles, f, (hx, hy))
            if pose.bubble:
                draw_bubble(d, hx, hy, pose.bubble)
            frames.append(img)
    return frames


def upscale(img: Image.Image, scale: int) -> Image.Image:
    # NEAREST = harte Pixelkanten, kein Weichzeichnen.
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
    ap.add_argument("--fps", type=int, default=12, help="Bildrate, empfohlen 12-15 (Standard: 12)")
    ap.add_argument("--scale", type=int, default=4, help="Hochskalierung, 4 => 1280x720 (Standard: 4)")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="Tempo-Faktor: >1 schneller/kuerzer, <1 langsamer (Standard: 1.0)")
    ap.add_argument("--amplitude", type=float, default=1.0,
                    help="Faktor fuer Bewegungsamplituden (Wackeln, Vorrutschen) (Standard: 1.0)")
    ap.add_argument("--seed", type=int, default=7, help="Zufalls-Seed fuer Partikel (reproduzierbar)")
    ap.add_argument("--loops", type=int, default=1, help="Wie oft die Animation im MP4 wiederholt wird")
    ap.add_argument("--crf", type=int, default=16, help="x264-Qualitaet (kleiner = besser, Standard: 16)")
    ap.add_argument("--no-mp4", action="store_true", help="Kein MP4 erzeugen")
    ap.add_argument("--gif", action="store_true", help="Zusaetzlich GIF exportieren")
    ap.add_argument("--gif-scale", type=int, default=2, help="Skalierung des GIFs (Standard: 2)")
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

    frames = render_frames(a.fps, a.speed, a.amplitude, a.seed)
    dur = len(frames) / a.fps
    print(f"{len(frames)} Frames gerendert ({dur:.2f} s @ {a.fps} FPS, "
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
    print("Benoetigt: Python >= 3.9, pillow (pip install -r requirements.txt), ffmpeg im PATH")


if __name__ == "__main__":
    main()

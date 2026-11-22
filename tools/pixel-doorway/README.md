# Pixel-Doorway – Pixel-Art-Slapstick-Animation

Eine Pixel-Figur steckt mit dem Becken in einem schmalen Holztürrahmen fest, stemmt sich dagegen,
wackelt, ruckt ein kleines Stück vor und federt wieder zurück.

- Figur: eigenes Sprite (`assets/heroine.png`, 25×70 px), aus dem Referenzvideo extrahiert
- Basisauflösung 256×144, Nearest-Neighbor-Upscaling ×5 → 1280×720
- 12–15 FPS, ca. 4,8 s
- Export: MP4 (ffmpeg), optional GIF, Sprite-Sheet, Einzelbilder
- Deterministisch (`--seed`)

## Abhängigkeiten

- Python ≥ 3.9
- `pip install -r requirements.txt` (Pillow, numpy)
- ffmpeg im `PATH` (für MP4 und die Sprite-Extraktion), z. B. `brew install ffmpeg` / `sudo apt install ffmpeg`

## Benutzung

```bash
cd tools/pixel-doorway
python doorway_stuck.py                          # -> out/doorway_stuck.mp4
python doorway_stuck.py --gif --sheet            # + GIF und Sprite-Sheet
python doorway_stuck.py --fps 15 --speed 0.8     # 15 FPS, 25 % langsamer
python doorway_stuck.py --amplitude 1.5 --loops 3
python doorway_stuck.py --help
```

| Argument | Bedeutung | Standard |
|---|---|---|
| `-o, --out-dir` | Ausgabeordner | `out` |
| `--sprite` | Sprite-PNG der Figur | `assets/heroine.png` |
| `--fps` | Bildrate | 12 |
| `--scale` | Hochskalierung (Nearest-Neighbor) | 5 |
| `--speed` | Tempo-Faktor aller Phasen | 1.0 |
| `--amplitude` | Stärke von Wackeln/Vorrutschen | 1.0 |
| `--seed` | Seed für Partikel | 7 |
| `--loops` | Wiederholungen im MP4 | 1 |
| `--gif`, `--sheet`, `--frames-dir` | Zusatzexporte | aus |
| `--no-mp4` | kein MP4 | – |

## Sprite neu extrahieren

`assets/heroine.png` ist eingecheckt. Neu erzeugen (oder eine andere Figur holen):

```bash
python extract_sprite.py --video referenz.mov -o assets/heroine.png
# andere Stelle/Figur: --time 12.5 --box x0,y0,x1,y1 ; Reste entfernen: --erase x0,y0,x1,y1
```

Das Skript sampelt das hochskalierte Video auf das Original-Pixelraster zurück
(`--pixel 7.35`), stellt die Figur per Farbe frei, reduziert die Palette und setzt die
gelben Akzentlinien auf eine klare Farbe (`--accent`).

## Anpassen (`doorway_stuck.py`, Abschnitte oben in der Datei)

1. **PALETTE** – Farben von Raum und Effekten
2. **LAYOUT** – Auflösung, Boden, Türbreite (`DOOR_HALF_OPENING`), Pfostenbreite
3. **SPRITE** – Zeilenbereiche der Körperteile, `HIP_WIDEN` (Beckenbreite), `HIP_BULGE_ROW`
4. **PHASES** – Ablauf/Dauer (`idle`, `grip`, `strain`, `slip`, `snap`, `exhausted`) und
   Amplituden (`WIGGLE_PX`, `SLIP_PX`, `HIP_SQUEEZE`, `HIP_BOUNCE`, `SHAKE_PX`)

Die Posen je Phase berechnet `pose_at()`. Die Figur wird in Streifen (Kopf, Oberkörper,
Becken, Beine) zerlegt und pixelgenau verschoben, geschert und horizontal gestaucht;
die Türpfosten liegen zwischen Becken und Oberkörper, dadurch wirkt sie eingeklemmt.

Hinweis: Das Sprite ist ein einzelnes Standbild. Die Arme sind darin nicht vom Körper
getrennt und bewegen sich deshalb mit dem Oberkörper. Für eigenständige Armbewegungen
(z. B. gegen den Rahmen stemmen) werden separate Arm-Sprites benötigt.

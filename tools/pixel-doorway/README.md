# Pixel-Doorway – Pixel-Art-Slapstick-Animation

Python-Skript, das eine kurze Pixel-Art-Animation rendert: Eine Figur steckt mit den Hüften
in einem schmalen Holztürrahmen fest, stemmt sich dagegen, ruckt ein Stück vor und federt zurück.

- Basisauflösung 320×180, Nearest-Neighbor-Upscaling (Standard ×4 → 1280×720)
- 12–15 FPS, ca. 4,8 s
- Export: MP4 (ffmpeg), optional GIF, Sprite-Sheet, Einzelbilder
- Deterministisch (`--seed`)

## Abhängigkeiten

- Python ≥ 3.9
- Pillow: `pip install -r requirements.txt`
- ffmpeg im `PATH` (nur für MP4), z. B. `brew install ffmpeg` / `sudo apt install ffmpeg`

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
| `--fps` | Bildrate | 12 |
| `--scale` | Hochskalierung (Nearest-Neighbor) | 4 |
| `--speed` | Tempo-Faktor aller Phasen | 1.0 |
| `--amplitude` | Stärke von Wackeln/Vorrutschen | 1.0 |
| `--seed` | Seed für Partikel | 7 |
| `--loops` | Wiederholungen im MP4 | 1 |
| `--gif`, `--sheet`, `--frames-dir` | Zusatzexporte | aus |
| `--no-mp4` | kein MP4 | – |

## Anpassen

Alle Stellschrauben stehen oben in `doorway_stuck.py`:

1. **PALETTE** – Farbrampen (Schatten, Grundton, Licht)
2. **LAYOUT** – Raum- und Türgeometrie
3. **PROPORTIONS** – Körperproportionen
4. **PHASES** – Ablauf/Dauer (`idle`, `grip`, `strain`, `slip`, `snap`, `exhausted`) und Amplituden

Die Posen je Phase berechnet `pose_at()`.

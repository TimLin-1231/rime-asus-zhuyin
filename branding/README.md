# 島輝 DaoHui — icon set

## Chosen design: F 反白 (Negative)

A paper-white brush-calligraphy 島 sits on an ink-black rounded tile. A grey brush circle (ensō, 円相) runs behind it, and a thin light rim keeps the tile visible on a dark taskbar. The set uses only black, greys and paper-white.

Mode icons use the same language:

| Icon | Design |
|---|---|
| Chinese | black badge, white 中 |
| English (ASCII) | inverted: paper badge, black **A** |
| Full-width | ● |
| Half-width | ◐ |
| Reload / deploying | circular arrow |
| Installer | app tile with a download badge |

Preview: `final_F_preview.png` shows every icon at 16/20/24/32/48/256 px and at 16/24 px ×4, on light and dark backgrounds.

How the glyph is drawn at each size:

| Size | Glyph |
|---|---|
| ≥ 40 px | Brush 島 with the full ink treatment: ragged edges, ensō, paper/ink-stone grain |
| 32 px | Brush 島, plain |
| 24 px | Brush 島, FreeType-hinted at the real pixel size |
| 16–20 px | Hinted Microsoft JhengHei Bold, because the brush 島 turns to mush at these sizes |

The 中 and A at ≤ 32 px are hand-built on the pixel grid.

## Fonts and licences

- **Yuji Syuku Regular** supplies the brush 島 (≥ 24 px) and the brush 中 in mode icons (≥ 40 px).
  - Copyright 2021 The Yuji Project Authors. **SIL Open Font License 1.1.**
  - Source: <https://github.com/Kinutafontfactory/Yuji> (`fonts/ttf/YujiSyuku-Regular.ttf`, SHA-256 `82728EBAFC8C97391E2DAB633414A806F344B8E4E2227D307179F07B548FCA61`).
  - The font and its licence are kept in `fonts/` (`fonts/OFL.txt`).
  - The OFL allows using the font to make images and logos, and the icons themselves are not font software. Credit is not required for the icons, but it is good practice. Suggested line for About/credits:
    > Logo glyph drawn with Yuji Syuku © 2021 The Yuji Project Authors, licensed under the SIL Open Font License 1.1.
- **Microsoft JhengHei Bold** (bundled with Windows) supplies only the 16 and 20 px 島 frames of `weasel.ico`, `installer.ico` and `tauri/icons/icon.ico`.
  - This is the only non-OFL font in the shipped set.
  - For an all-OFL set, set `SMALL_GLYPH_FONT = FONT_NOTO_TC_BOLD` in `make_icons.py` and regenerate. Noto Sans TC is SIL OFL and ships with Windows, but it renders softer and greyer at 16 px.
- **No font** is used for the hand-drawn 中/A (≤ 32 px), ●, ◐, the reload arrow or the download arrow.
- **DFKai-SB (kaiu.ttf)** is no longer used. It is a commercial DynaComware font, and the D–F drafts (`inkwash_*.png`, `out/D`, `out/E`) were rendered with it. Do not ship those.

## Where each file goes

Everything is in `out/F/`. Every `.ico` contains 16, 20, 24, 32, 40, 48, 64, 96, 128 and 256 px: 32-bit BMP with an AND mask below 256 px, and PNG at 256 px.

| File | Destination | Used for |
|---|---|---|
| `weasel.ico` | `D:\src\daohui\resource\weasel.ico` | App icon: IME list, server/tray app |
| `zh.ico` | `D:\src\daohui\resource\zh.ico` | Tray / language bar: Chinese mode |
| `en.ico` | `D:\src\daohui\resource\en.ico` | Tray / language bar: English (ASCII) mode |
| `full.ico` | `D:\src\daohui\resource\full.ico` | Full-width |
| `half.ico` | `D:\src\daohui\resource\half.ico` | Half-width |
| `reload.ico` | `D:\src\daohui\resource\reload.ico` | Deploying / reloading |
| `installer.ico` | Installer project (setup exe icon) | Installer |
| `app-256.png`, `app-512.png` | README, website, store listing, About page | App icon as PNG |
| `tauri/icons/32x32.png` | settings app `src-tauri/icons/` | Tauri bundle icons (`bundle.icon` in `tauri.conf.json`) |
| `tauri/icons/128x128.png` | settings app `src-tauri/icons/` | " |
| `tauri/icons/128x128@2x.png` (256 px) | settings app `src-tauri/icons/` | " |
| `tauri/icons/icon.ico` | settings app `src-tauri/icons/` | Windows |
| `tauri/icons/icon.icns` | settings app `src-tauri/icons/` | macOS. Written by Pillow; macOS work is postponed and this file is untested on a Mac |

The vector source is `concepts/F.svg`. Its ink texture is an approximation built from SVG filters. The raster icons are the reference, so treat `concepts/F-512.png` as the master image.

## Regenerate

The generator needs Python 3 with Pillow, numpy, scipy, scikit-image and fontTools (Anaconda includes all of them). It also needs `C:\Windows\Fonts\msjhbd.ttc` and `fonts/YujiSyuku-Regular.ttf`.

```
cd D:\Document\輸入法\branding
C:\Users\User\anaconda3\python.exe make_icons.py final F    # about 2.5 minutes
```

`final F` rewrites `concepts/F.svg`, `concepts/F-512.png`, everything under `out/F/` (including `tauri/icons/`) and `final_F_preview.png`. It then opens every `.ico`/`.icns` with Pillow and checks the size list.

To change the design, edit these parts of `make_icons.py`:

| What | Where |
|---|---|
| Composition | `concept_F()` |
| Badge colours | `MODE_PAL["F"]` |
| Per-size glyph rules | `ink_glyph_ops()` |
| Ink effects | `Raster.ink()`, `Raster.enso()` |

Other commands: `concepts`, `modes`, `export`, `verify`, `all` (see the header of `make_icons.py`).

## Other files (drafts, not for shipping)

| Files | Contents |
|---|---|
| `concepts_preview.png`, `mode_icons_preview.png`, `out/A`, `out/B`, `out/C` | Colour drafts: A sunrise island, B gold 島, C wave + arc |
| `inkwash_preview.png`, `inkwash_mode_icons_preview.png`, `out/D`, `out/E` | Ink-wash drafts D–F as first rendered with DFKai-SB. The F shown there is superseded by `final_F_preview.png` / `out/F` |
| `concepts/*.svg`, `concepts/*-512.png` | Vector / 512 px sources for every concept |

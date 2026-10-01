# Copilot Instructions for pillow-heif-spike

This is a small exploratory ("spike") repo comparing two approaches to converting
HEIC/HEIF photos to JPG on Windows. There is no package manifest, test suite, or
build system — scripts are run directly with `python`.

## Running the scripts

No installed CLI/entry points; just run the files directly from the repo root
(paths are resolved relative to `__file__`, so cwd doesn't matter):

```powershell
python convert_heic.py          # pillow-heif/libheif based conversion
python heic_to_jpg_native.py    # pure-Python/ffmpeg based conversion (no libheif)
python stitch_tiles.py          # standalone debug helper, see below
```

Both `convert_heic.py` and `heic_to_jpg_native.py` scan `data/*.heic` and
`data/*.HEIC` and write JPGs next to the source files. Dependencies
(`pillow`, `pillow_heif`, `imageio_ffmpeg`) are installed globally — there is
no `requirements.txt` or virtualenv in this repo; install missing packages
with `pip install pillow pillow-heif imageio-ffmpeg`.

There are no automated tests or linters configured. Validate changes by
running the relevant script against the sample images in `data/` and visually
inspecting the resulting JPGs (and comparing `*.jpg` output from
`convert_heic.py` against `*.native.jpg` output from `heic_to_jpg_native.py`
for the same source image).

## Architecture: two independent conversion paths

- **`convert_heic.py`** — the baseline/reference approach. Uses
  `pillow_heif.register_heif_opener()` so `PIL.Image.open()` transparently
  decodes HEIC via libheif. Simple and correct, but depends on libheif being
  available.
- **`heic_to_jpg_native.py` + `heif_box_parser.py`** — a from-scratch
  alternative that avoids pillow-heif/libheif entirely:
  1. `heif_box_parser.py` hand-parses the ISOBMFF/HEIF container (stdlib
     `struct` only) to extract just enough metadata: the primary item id
     (`pitm`), item types (`iinf`), item byte ranges (`iloc`), tile ordering
     for grid images (`iref`'s `dimg` references), and the `irot` rotation
     property (`iprp`/`ipco`/`ipma`).
  2. `heic_to_jpg_native.py` then shells out to the ffmpeg binary bundled by
     `imageio_ffmpeg` to decode each item's raw HEVC bitstream to a PNG frame
     (`-map 0:<stream_index>`).
  3. If the primary item is a tiled `grid` construct, tiles are stitched
     into a canvas with Pillow, cropped to the declared output size, rotated
     per `irot`, and saved as JPEG. Non-tiled images are decoded directly.

  **Key assumption to know before touching this file:** ffmpeg's `mov`
  demuxer is assumed to expose a HEIC's per-tile HEVC substreams as
  sequential numbered streams (0, 1, 2, ...) in the same order as the
  container's `dimg` item references. This was verified empirically, not
  guaranteed by spec — if it breaks on a new file, tiles render out of order
  (visible mosaic misalignment) rather than raising an error.

- **`stitch_tiles.py`** — a standalone debug helper, not part of either
  pipeline. It expects pre-extracted tile PNGs in `data/tiles/tile_<n>.png`
  and hardcodes grid geometry (`COLS, ROWS = 8, 6`, `TILE_W, TILE_H = 512,
  512`, `FINAL_W, FINAL_H = 4032, 3024`) for one specific source image. Update
  these constants manually when debugging a different image's tiling.

## Conventions

- Scripts resolve `data/` relative to `Path(__file__).parent`, not the
  current working directory — follow this pattern in any new script so it
  works regardless of where it's invoked from.
- `heic_to_jpg_native.py` output files use a `.native.jpg` suffix
  (`heic_file.with_suffix(".native.jpg")`) to distinguish them from
  `convert_heic.py`'s plain `.jpg` output when both are run against the same
  `data/` folder.
- `heif_box_parser.py` is pure stdlib by design (no Pillow/pillow-heif
  dependency) — keep it that way so `heic_to_jpg_native.py`'s whole point
  (avoiding libheif) still holds.




Use  /resume  to switch back into this exact session later.

• Run  /resume  with no args to pick from a list of recent sessions by name.
• Or  /resume <id>  with this session's local/cloud history ID or task ID.

This session is named/trackable by its summary — if you don't recall the ID, just run  /resume  and select it from the list (session work is auto-saved, including the proposal log I just wrote to the session  files/  folder, so it'll be there when you pick this session back up).
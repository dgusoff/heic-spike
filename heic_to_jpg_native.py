"""
Convert HEIC/HEIF files to JPG using only Pillow + imageio-ffmpeg (no pillow-heif
/ libheif). Works by:

  1. Parsing the HEIF container ourselves (heif_box_parser.py) to discover
     whether the primary image is a tiled 'grid' construction, its geometry,
     and any 'irot' rotation to apply.
  2. Using ffmpeg's native HEVC decoder (bundled by imageio-ffmpeg) to decode
     each tile (or the single image) to a PNG frame.
  3. Stitching tiles into the full-resolution canvas with Pillow, cropping to
     the declared output size, applying rotation, and saving as JPEG.

Tile-to-ffmpeg-stream mapping assumption: ffmpeg's mov demuxer exposes the
HEIC's per-tile HEVC substreams as sequential numbered streams (0, 1, 2, ...)
in the same order as the container's 'dimg' item references. This was
verified empirically; if a file breaks this assumption, tiles would appear
out of order (a visible mosaic misalignment) rather than failing silently.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
import imageio_ffmpeg

from heif_box_parser import parse_heif

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _run_ffmpeg(args):
    result = subprocess.run(
        [FFMPEG, *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-2000:]}")


def _decode_stream_to_png(heic_path: Path, stream_index: int, out_path: Path):
    _run_ffmpeg([
        "-i", str(heic_path),
        "-map", f"0:{stream_index}",
        "-update", "1", "-frames:v", "1",
        "-y", str(out_path),
    ])


def convert_heic_to_jpg(heic_path: Path, jpg_path: Path, quality: int = 95) -> Path:
    data = heic_path.read_bytes()
    info = parse_heif(data)

    primary_id = info.primary_item_id
    if primary_id is None:
        raise ValueError("No primary item found in HEIF container")

    rotation = info.rotation_degrees(primary_id)
    primary_type = info.item_types.get(primary_id)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        if primary_type == "grid":
            rows, cols, out_w, out_h = info.grid_geometry(primary_id, data)
            tile_ids = info.dimg_refs[primary_id]
            if len(tile_ids) != rows * cols:
                raise ValueError(
                    f"Grid declares {rows}x{cols}={rows*cols} tiles but 'dimg' "
                    f"lists {len(tile_ids)} - cannot safely map to ffmpeg streams"
                )

            tiles = []
            for stream_index in range(len(tile_ids)):
                png_path = tmp_dir / f"tile_{stream_index}.png"
                _decode_stream_to_png(heic_path, stream_index, png_path)
                tiles.append(Image.open(png_path))

            tile_w, tile_h = tiles[0].size
            canvas = Image.new("RGB", (cols * tile_w, rows * tile_h))
            for idx, tile in enumerate(tiles):
                row, col = divmod(idx, cols)
                canvas.paste(tile, (col * tile_w, row * tile_h))
            image = canvas.crop((0, 0, out_w, out_h))
        else:
            # Single (non-tiled) HEIC image - decode the primary video stream directly.
            png_path = tmp_dir / "single.png"
            _decode_stream_to_png(heic_path, 0, png_path)
            image = Image.open(png_path).convert("RGB")

        if rotation:
            # HEIF 'irot' angle is counter-clockwise; PIL's rotate() matches that
            # convention directly.
            image = image.rotate(rotation, expand=True)

        image.save(jpg_path, "JPEG", quality=quality)

    return jpg_path


def main():
    data_dir = Path(__file__).parent / "data"
    heic_files = sorted(data_dir.glob("*.heic")) + sorted(data_dir.glob("*.HEIC"))
    if not heic_files:
        print(f"No HEIC files found in {data_dir}")
        sys.exit(1)

    for heic_file in heic_files:
        jpg_file = heic_file.with_suffix(".native.jpg")
        convert_heic_to_jpg(heic_file, jpg_file)
        print(f"Converted: {heic_file.name} -> {jpg_file.name}")


if __name__ == "__main__":
    main()

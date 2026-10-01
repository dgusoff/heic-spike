"""Stitch extracted HEVC tile PNGs (row-major grid) into the full-resolution image."""
from pathlib import Path
from PIL import Image

TILES_DIR = Path("data/tiles")
COLS, ROWS = 8, 6
TILE_W, TILE_H = 512, 512
FINAL_W, FINAL_H = 4032, 3024

canvas = Image.new("RGB", (COLS * TILE_W, ROWS * TILE_H))

for idx in range(COLS * ROWS):
    tile = Image.open(TILES_DIR / f"tile_{idx}.png")
    row, col = divmod(idx, COLS)
    canvas.paste(tile, (col * TILE_W, row * TILE_H))

canvas = canvas.crop((0, 0, FINAL_W, FINAL_H))
canvas.save("data/stitched_result.jpg", "JPEG", quality=95)
print("Saved:", canvas.size)

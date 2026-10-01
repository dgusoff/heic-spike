
"""Convert HEIC images in the data/ folder to JPG using pillow-heif."""
import sys
from pathlib import Path

from PIL import Image
import pillow_heif

pillow_heif.register_heif_opener()

DATA_DIR = Path(__file__).parent / "data"


def convert_heic_to_jpg(heic_path: Path) -> Path:
    jpg_path = heic_path.with_suffix(".jpg")
    image = Image.open(heic_path)
    image.convert("RGB").save(jpg_path, "JPEG", quality=95)
    return jpg_path


def main() -> None:
    heic_files = sorted(DATA_DIR.glob("*.heic")) + sorted(DATA_DIR.glob("*.HEIC"))
    if not heic_files:
        print(f"No HEIC files found in {DATA_DIR}")
        sys.exit(1)

    for heic_file in heic_files:
        jpg_file = convert_heic_to_jpg(heic_file)
        print(f"Converted: {heic_file.name} -> {jpg_file.name}")


if __name__ == "__main__":
    main()

"""Bounded offline regression checks. Optional attached fixture is read-only."""
import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import convert_heif as recovery


class BoundsTests(unittest.TestCase):
    def test_reader_truncation(self):
        with self.assertRaises(ValueError):
            recovery.Reader(b"\x01").raw(2)

    def test_truncated_extended_box(self):
        with self.assertRaises(ValueError):
            recovery.Container(b"\x00\x00\x00\x01meta")

    def test_box_beyond_file(self):
        with self.assertRaises(ValueError):
            recovery.Container(b"\xff\xff\xff\xffmeta")

    def test_oversized_dimensions(self):
        with self.assertRaises(ValueError):
            recovery.dimensions(65536, 65536)

    def test_missing_decoder(self):
        with patch.dict("sys.modules", {"imageio_ffmpeg": None}), patch.object(
                recovery.shutil, "which", return_value=None):
            with self.assertRaises(ValueError):
                recovery.discover_ffmpeg()


def fixture(source, output_parent):
    expected = json.loads((Path(__file__).resolve().parent.parent /
                           "references/regression-fixture.json").read_text())
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    if before != expected["source_sha256"]:
        raise ValueError("Not the documented regression fixture")
    output_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="heif-regression-", dir=output_parent) as temp:
        root = Path(temp)
        report = recovery.recover(source.resolve(), root)
        for key in ("primary_item", "grid_rows", "grid_columns", "grid_size",
                    "orientation", "output_sha256"):
            if report[key] != expected[key]:
                raise ValueError("Fixture mismatch: " + key)
        if (len(report["decoded_tiles"]) != expected["tile_count"]
                or report["edge_crop"] != expected["edge_crop_right_bottom"]):
            raise ValueError("Fixture coverage mismatch")
        for n, tile in enumerate(report["decoded_tiles"]):
            row, col = divmod(n, expected["grid_columns"])
            if (tile["item"] != n + 1 or tile["row"] != row or tile["column"] != col
                    or tile["size"] != expected["tile_size"]
                    or tile["origin"] != [col * 896, row * 960]):
                raise ValueError("Fixture source mapping mismatch")
        with Image.open(root / report["output"]) as png:
            png.load()
            if list(png.size) != expected["grid_size"] or png.mode != "RGB":
                raise ValueError("Fixture PNG dimensions/mode mismatch")
        if hashlib.sha256(source.read_bytes()).hexdigest() != before:
            raise ValueError("Source changed")
        print(json.dumps({"fixture": "PASS", "tile_count": 30,
                          "size": report["grid_size"], "pixel_mapping": report["pixel_verification"],
                          "output_matches_verified_fixture": True,
                          "source_unchanged": True, "visual_review": "not repeated by this script"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--working-dir", type=Path, default=Path("working/heif-regression"))
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BoundsTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        return 1
    if args.fixture:
        fixture(args.fixture, args.working_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Bounded local HEIF grid recovery: item/hvcC extraction and FFmpeg HEVC decoding.

Grid primaries with hvc1 tiles, an ICC profile, and an optional declared
0/90/180/270-degree 'irot' rotation are supported; the rotation is applied to
the assembled output. EXIF-only orientation, mirroring and clean-aperture
crop remain unsupported. No installation, network, environment inventory or
visual-success claim. Adapted from the verified local recovery implementation.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageChops

MAX_FILE = 512 * 1024 * 1024
MAX_PIXELS = 80_000_000
MAX_TILE_PIXELS = 16_000_000
MAX_TILES = 256
MAX_ITEMS = 4096
MAX_BOXES = 20000


class Reader:
    def __init__(self, data, start=0, end=None):
        self.data, self.pos = data, start
        self.end = len(data) if end is None else end
        if not 0 <= start <= self.end <= len(data):
            raise ValueError("Invalid reader bounds")

    def raw(self, n):
        if n < 0 or n > self.end - self.pos:
            raise ValueError("Truncated field")
        value = self.data[self.pos:self.pos + n]
        self.pos += n
        return value

    def num(self, n):
        return int.from_bytes(self.raw(n), "big")

    def fullbox(self, versions, allowed_flags=0):
        version, flags = self.num(1), self.num(3)
        if version not in versions or flags & ~allowed_flags:
            raise ValueError("Unsupported full box version/flags")
        return version, flags

    def done(self):
        if self.pos != self.end:
            raise ValueError("Unexpected trailing data")


def dimensions(w, h, limit=MAX_PIXELS):
    if not 0 < w <= 65536 or not 0 < h <= 65536 or w * h > limit:
        raise ValueError("Image dimensions exceed resource limits")
    return w, h


class Container:
    def __init__(self, data):
        self.data = data
        self.types, self.locations, self.refs = {}, {}, {}
        self.properties, self.associations = [], {}
        self.primary, self.idat = None, None
        self.box_count = 0
        top = list(self.boxes(0, len(data)))
        metas = [(s, e) for k, s, e in top if k == "meta"]
        if len(metas) != 1 or not any(k == "ftyp" for k, _, _ in top):
            raise ValueError("Require one HEIF meta box and file type box")
        s, e = metas[0]
        r = Reader(data, s, e)
        r.fullbox((0,))
        children = list(self.boxes(r.pos, e))
        seen = set()
        for kind, start, end in children:
            if kind in {"pitm", "idat", "iinf", "iloc", "iref", "iprp"}:
                if kind in seen:
                    raise ValueError("Duplicate metadata box")
                seen.add(kind)
            r = Reader(data, start, end)
            if kind == "pitm":
                v, _ = r.fullbox((0, 1))
                self.primary = r.num(2 if v == 0 else 4)
                r.done()
            elif kind == "idat":
                self.idat = (start, end)
            elif kind == "iinf":
                v, _ = r.fullbox((0, 1))
                count = r.num(2 if v == 0 else 4)
                if count > MAX_ITEMS:
                    raise ValueError("Too many items")
                entries = list(self.boxes(r.pos, end))
                if len(entries) != count:
                    raise ValueError("Item count mismatch")
                for name, a, b in entries:
                    if name != "infe":
                        raise ValueError("Unsupported item entry")
                    q = Reader(data, a, b)
                    v, _ = q.fullbox((2, 3), 1)
                    item = q.num(2 if v == 2 else 4)
                    if not item or item in self.types or q.num(2):
                        raise ValueError("Duplicate/protected item")
                    self.types[item] = q.raw(4).decode("ascii")
            elif kind == "iloc":
                v, _ = r.fullbox((0, 1, 2))
                a, b = r.num(1), r.num(1)
                offset_size, length_size = a >> 4, a & 15
                base_size, index_size = b >> 4, b & 15
                if any(x not in (0, 4, 8) for x in
                       (offset_size, length_size, base_size, index_size)):
                    raise ValueError("Unsupported extent integer size")
                if v == 0 and index_size:
                    raise ValueError("Reserved iloc bits")
                count = r.num(2 if v < 2 else 4)
                if count > MAX_ITEMS:
                    raise ValueError("Too many locations")
                for _ in range(count):
                    item = r.num(2 if v < 2 else 4)
                    method = r.num(2) if v else 0
                    ref, base = r.num(2), r.num(base_size)
                    n = r.num(2)
                    if n > 256 or item in self.locations:
                        raise ValueError("Duplicate/oversized item extents")
                    extents = []
                    for _ in range(n):
                        if v and index_size and r.num(index_size):
                            raise ValueError("Indexed extents unsupported")
                        extents.append((r.num(offset_size), r.num(length_size)))
                    self.locations[item] = (method, ref, base, extents)
                r.done()
            elif kind == "iref":
                v, _ = r.fullbox((0, 1))
                for name, a, b in self.boxes(r.pos, end):
                    q = Reader(data, a, b)
                    item = q.num(2 if v == 0 else 4)
                    n = q.num(2)
                    if n > MAX_ITEMS or (item, name) in self.refs:
                        raise ValueError("Duplicate/oversized reference list")
                    self.refs[(item, name)] = [
                        q.num(2 if v == 0 else 4) for _ in range(n)]
                    q.done()
            elif kind == "iprp":
                for name, a, b in self.boxes(start, end):
                    if name == "ipco":
                        if self.properties:
                            raise ValueError("Duplicate property container")
                        self.properties = [(k, data[x:y])
                                           for k, x, y in self.boxes(a, b)]
                    elif name == "ipma":
                        q = Reader(data, a, b)
                        v, flags = q.fullbox((0, 1), 1)
                        n = q.num(4)
                        if n > MAX_ITEMS:
                            raise ValueError("Too many associations")
                        for _ in range(n):
                            item = q.num(2 if v == 0 else 4)
                            if item in self.associations:
                                raise ValueError("Duplicate associations")
                            entries = []
                            for _ in range(q.num(1)):
                                val = q.num(2 if flags & 1 else 1)
                                mask = 32767 if flags & 1 else 127
                                entries.append((val & mask, bool(val & (mask + 1))))
                            self.associations[item] = entries
                        q.done()
                    else:
                        raise ValueError("Unsupported property box")
        if self.primary not in self.types:
            raise ValueError("Missing primary identity")

    def boxes(self, start, end):
        r = Reader(self.data, start, end)
        while r.pos < end:
            self.box_count += 1
            if self.box_count > MAX_BOXES:
                raise ValueError("Too many boxes")
            origin = r.pos
            size, kind = r.num(4), r.raw(4).decode("ascii")
            header = 8
            if size == 1:
                size, header = r.num(8), 16
            if size == 0:
                size = end - origin
            if size < header or size > end - origin:
                raise ValueError("Invalid box bounds")
            yield kind, origin + header, origin + size
            r.pos = origin + size

    def item_bytes(self, item):
        if item not in self.locations:
            raise ValueError("Missing item location")
        method, ref, base, extents = self.locations[item]
        if ref or method not in (0, 1) or not extents:
            raise ValueError("Unsupported construction/data reference")
        if method == 1 and self.idat is None:
            raise ValueError("Missing idat")
        origin, bound = (0, len(self.data)) if method == 0 else self.idat
        chunks, total = [], 0
        for offset, length in extents:
            start = origin + base + offset
            total += length
            if not length or start < origin or start + length > bound or total > MAX_FILE:
                raise ValueError("Invalid item extent")
            chunks.append(self.data[start:start + length])
        return b"".join(chunks)

    def props(self, item):
        entries, names = [], set()
        known = {"ispe", "hvcC", "colr", "pixi", "irot", "imir", "clap", "clli"}
        for index, essential in self.associations.get(item, []):
            if not index:
                if essential:
                    raise ValueError("Invalid essential property index")
                continue
            if index > len(self.properties):
                raise ValueError("Property index out of bounds")
            kind, value = self.properties[index - 1]
            if kind not in known:
                raise ValueError("Unsupported rendering property: " + kind)
            if kind in names:
                raise ValueError("Duplicate item property")
            names.add(kind)
            entries.append((kind, value))
        return dict(entries)

    def size(self, item):
        r = Reader(self.props(item).get("ispe", b""))
        r.fullbox((0,))
        result = dimensions(r.num(4), r.num(4))
        r.done()
        return result

    def identity(self, item):
        props = self.props(item)
        if "imir" in props or "clap" in props:
            raise ValueError("Mirror/clean-aperture transform unsupported")
        if "pixi" in props:
            r = Reader(props["pixi"])
            r.fullbox((0,))
            n = r.num(1)
            if n != 3 or r.raw(n) != b"\x08\x08\x08":
                raise ValueError("Only declared 8-bit three-channel primary supported")
            r.done()

    def rotation(self, item):
        """Return the primary's declared 'irot' angle in degrees counter-clockwise
        (0/90/180/270), or 0 if the property is absent."""
        payload = self.props(item).get("irot")
        if payload is None:
            return 0
        if len(payload) != 1 or payload[0] & 0xFC:
            raise ValueError("Malformed 'irot' property")
        return (payload[0] & 0x03) * 90

    def orientation(self):
        linked = [i for i, k in self.types.items()
                  if k == "Exif" and self.primary in self.refs.get((i, "cdsc"), [])]
        if len(linked) > 1:
            raise ValueError("Ambiguous Exif orientation")
        if not linked:
            return None
        payload = self.item_bytes(linked[0])
        r = Reader(payload)
        offset = 4 + r.num(4)
        if offset > len(payload) - 8:
            raise ValueError("Invalid Exif offset")
        tiff = Reader(payload, offset)
        order = tiff.raw(2)
        if order not in (b"II", b"MM"):
            raise ValueError("Invalid TIFF byte order")
        endian = "little" if order == b"II" else "big"

        def num(n):
            return int.from_bytes(tiff.raw(n), endian)

        if num(2) != 42:
            raise ValueError("Unsupported TIFF header")
        ifd = offset + num(4)
        if ifd < offset + 8:
            raise ValueError("Invalid TIFF directory")
        tiff = Reader(payload, ifd)
        count = num(2)
        if count > MAX_ITEMS:
            raise ValueError("Too many TIFF entries")
        orientation = None
        for _ in range(count):
            tag, kind, n, value = num(2), num(2), num(4), tiff.raw(4)
            if tag == 274:
                if orientation is not None or kind != 3 or n != 1:
                    raise ValueError("Invalid/duplicate orientation tag")
                orientation = int.from_bytes(value[:2], endian)
        tiff.raw(4)
        if orientation not in (None, 1):
            raise ValueError("Nonidentity Exif orientation unsupported")
        return orientation

    def annex_b(self, item):
        hvcc = self.props(item).get("hvcC", b"")
        if len(hvcc) < 23 or hvcc[0] != 1:
            raise ValueError("Missing/unsupported hvcC")
        if hvcc[17] & 7 or hvcc[18] & 7:
            raise ValueError("High-bit-depth HEVC is outside this 8-bit recovery path")
        length_size = (hvcc[21] & 3) + 1
        r = Reader(hvcc, 23)
        stream, parameter_types = bytearray(), set()
        for _ in range(hvcc[22]):
            nal_type = r.num(1) & 63
            if nal_type not in (32, 33, 34, 39, 40):
                raise ValueError("Unsupported parameter array")
            for _ in range(r.num(2)):
                value = r.raw(r.num(2))
                if len(value) < 2 or (value[0] >> 1) & 63 != nal_type:
                    raise ValueError("Invalid parameter NAL")
                parameter_types.add(nal_type)
                stream.extend(b"\x00\x00\x00\x01" + value)
        r.done()
        if not {32, 33, 34} <= parameter_types:
            raise ValueError("Missing HEVC parameter sets")
        payload = Reader(self.item_bytes(item))
        count, pictures = 0, 0
        while payload.pos < payload.end:
            value = payload.raw(payload.num(length_size))
            count += 1
            if len(value) < 2 or count > 10000:
                raise ValueError("Invalid/oversized NAL sequence")
            nal_type = (value[0] >> 1) & 63
            if value[0] & 128 or value[0] & 1 or value[1] >> 3 or not value[1] & 7:
                raise ValueError("Invalid/multilayer NAL header")
            if nal_type < 32:
                if len(value) < 3 or not 16 <= nal_type <= 23:
                    raise ValueError("Only independently coded intra pictures supported")
                pictures += bool(value[2] & 128)
            stream.extend(b"\x00\x00\x00\x01" + value)
            if len(stream) > MAX_FILE:
                raise ValueError("Oversized coded stream")
        if not count or pictures != 1:
            raise ValueError("Require exactly one independently coded picture per item")
        return stream


def discover_ffmpeg():
    # Normal dependency import/executable lookup only; no environment probes.
    try:
        import imageio_ffmpeg
        candidate = imageio_ffmpeg.get_ffmpeg_exe()
        found = shutil.which(candidate)
        if found:
            return str(Path(found).resolve())
    except (ImportError, RuntimeError, OSError):
        pass
    candidate = shutil.which("ffmpeg")
    if not candidate:
        raise ValueError("No available FFmpeg; supply JPG instead (no installation)")
    return str(Path(candidate).resolve())


def recover(source, directory, expected=None):
    with source.open("rb") as handle:
        data = handle.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError("Source exceeds resource limit")
    c = Container(data)
    primary = c.primary
    if c.types[primary] != "grid":
        raise ValueError("Only HEIF grid primary supported by this path")
    if any(i == primary and k != "dimg" for i, k in c.refs):
        raise ValueError("Unsupported primary dependencies")
    c.identity(primary)
    angle = c.rotation(primary)
    orientation = c.orientation()
    r = Reader(c.item_bytes(primary))
    if r.num(1) != 0:
        raise ValueError("Unsupported grid version")
    flags = r.num(1)
    if flags & ~1:
        raise ValueError("Unsupported grid flags")
    rows, cols = r.num(1) + 1, r.num(1) + 1
    n = 4 if flags & 1 else 2
    width, height = dimensions(r.num(n), r.num(n))
    r.done()
    if c.size(primary) != (width, height) or (expected and tuple(expected) != (width, height)):
        raise ValueError("Grid/ispe/expected dimensions disagree")
    ids = c.refs.get((primary, "dimg"), [])
    if rows * cols > MAX_TILES or len(ids) != rows * cols or len(set(ids)) != len(ids):
        raise ValueError("Invalid/oversized grid references")
    sizes = []
    for item in ids:
        if c.types.get(item) != "hvc1" or any(i == item for i, _ in c.refs):
            raise ValueError("Unsupported tile type/dependencies")
        if any(k == "Exif" and item in c.refs.get((i, "cdsc"), [])
               for i, k in c.types.items()):
            raise ValueError("Tile-level Exif unsupported")
        c.identity(item)
        if "irot" in c.props(item):
            raise ValueError("Tile-level rotation unsupported")
        sizes.append(dimensions(*c.size(item), limit=MAX_TILE_PIXELS))
    tw, th = sizes[0]
    if any(s != (tw, th) for s in sizes) or len(ids) * tw * th > 160_000_000:
        raise ValueError("Unequal/excessive tiles")
    if not ((cols - 1) * tw < width <= cols * tw
            and (rows - 1) * th < height <= rows * th):
        raise ValueError("Invalid grid coverage")
    colr = c.props(primary).get("colr", b"")
    icc = colr[4:]
    if (colr[:4] not in (b"prof", b"rICC") or not 128 <= len(icc) <= 4 * 1024 * 1024
            or int.from_bytes(icc[:4], "big") != len(icc) or icc[36:40] != b"acsp"):
        raise ValueError("Require valid embedded primary ICC")
    ffmpeg = discover_ffmpeg()
    canvas = Image.new("RGB", (width, height))
    mapping = []
    started = time.monotonic()
    for index, item in enumerate(ids):
        if time.monotonic() - started > 600:
            raise ValueError("Overall decoding time limit")
        coded, png = directory / f"item-{item}.hevc", directory / f"item-{item}.png"
        coded.write_bytes(c.annex_b(item))
        # Argument vector, never shell; file-only protocol, bounded frame,
        # single threads, strict errors and no overwrite.
        with tempfile.TemporaryFile(dir=directory) as error_log:
            run = subprocess.run([
                ffmpeg, "-nostdin", "-n", "-hide_banner", "-loglevel", "error",
                "-xerror", "-err_detect", "explode", "-protocol_whitelist", "file",
                "-max_pixels", str(MAX_TILE_PIXELS), "-threads", "1",
                "-f", "hevc", "-i", str(coded), "-frames:v", "1",
                "-pix_fmt", "rgb24", "-threads", "1", str(png),
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=error_log, timeout=30, check=False)
            error_log.seek(0)
            error = error_log.read(2000)
        if run.returncode or error.strip() or not png.is_file():
            raise ValueError("HEVC decoder failed: " + error.decode("utf-8", "replace"))
        with Image.open(png) as tile:
            if tile.size != (tw, th) or tile.mode != "RGB" or tile.format != "PNG":
                raise ValueError("Tile dimension/mode mismatch")
            tile.load()
            row, col = divmod(index, cols)
            x, y = col * tw, row * th
            canvas.paste(tile, (x, y))
            mapping.append({"item": item, "row": row, "column": col,
                            "size": [tw, th], "origin": [x, y]})
        coded.unlink()
    # Pixel verification always runs against the unrotated, row-major grid
    # assembly first (canonical tile-grid space), independent of any declared
    # rotation applied below.
    assembled = directory / "assembled-grid.png"
    canvas.save(assembled, icc_profile=icc)
    with Image.open(assembled) as check:
        check.load()
        if check.size != (width, height) or check.mode != "RGB" or check.info.get("icc_profile") != icc:
            raise ValueError("Reopened PNG verification failed")
        for item in mapping:
            x, y = item["origin"]
            bounds = (x, y, min(x + tw, width), min(y + th, height))
            with Image.open(directory / f"item-{item['item']}.png") as tile:
                region = tile.crop((0, 0, bounds[2] - x, bounds[3] - y))
                if ImageChops.difference(check.crop(bounds), region).getbbox():
                    raise ValueError("Source-to-PNG pixel mapping mismatch")
        # HEIF 'irot' is a counter-clockwise rotation; PIL's rotate() matches
        # that convention directly. Applied only after the pixel-verified
        # grid assembly above, so verification itself never depends on it.
        final_image = check.rotate(angle, expand=True) if angle else check.copy()
    assembled.unlink()
    final_width, final_height = final_image.size
    output = directory / "primary-full.png"
    final_image.save(output, icc_profile=icc)
    with Image.open(output) as oriented:
        oriented.load()
        if (oriented.size != (final_width, final_height) or oriented.mode != "RGB"
                or oriented.info.get("icc_profile") != icc):
            raise ValueError("Reopened oriented PNG verification failed")
        preview = oriented.copy()
        preview.thumbnail((1200, 1600))
        preview.save(directory / "full-preview.png", icc_profile=icc)
        quadrants = []
        for i, bounds in enumerate(((0, 0, final_width // 2, final_height // 2),
                                   (final_width // 2, 0, final_width, final_height // 2),
                                   (0, final_height // 2, final_width // 2, final_height),
                                   (final_width // 2, final_height // 2, final_width, final_height))):
            if bounds[2] > bounds[0] and bounds[3] > bounds[1]:
                name = f"quadrant-{i + 1}.png"
                oriented.crop(bounds).save(directory / name, icc_profile=icc)
                quadrants.append({"png": name, "bounds": list(bounds)})
    return {
        "status": "candidates_ready", "source": str(source),
        "source_name": source.name, "source_sha256": hashlib.sha256(data).hexdigest(),
        "decoder": ffmpeg, "decoder_method": "HEIF item/hvcC extraction + FFmpeg HEVC",
        "primary_item": primary, "grid_rows": rows, "grid_columns": cols,
        "grid_size": [width, height], "ispe_size": list(c.size(primary)),
        "rotation_degrees_applied": angle,
        "output_size": [final_width, final_height],
        "orientation": orientation,
        "orientation_evidence": ("EXIF identity or absent; declared HEIF 'irot' rotation "
                                 "(if any) applied to the output"),
        "transforms": [(k, v.hex()) for k, v in c.props(primary).items()
                       if k in ("irot", "imir", "clap")],
        "decoded_tiles": mapping, "layout": "HEIF dimg reference order, row-major",
        "edge_crop": [cols * tw - width, rows * th - height],
        "output": output.name, "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "pixel_verification": ("All visible source tile regions match the unrotated grid "
                                "assembly exactly, prior to applying any declared rotation"),
        "color": "8-bit RGB rendering with embedded original primary ICC; not HDR preservation",
        "auxiliary_recovery": "not attempted", "visual_verification": "pending",
        "preview": "full-preview.png", "quadrants": quadrants,
        "next_gate": "Whole-image and exhaustive full-resolution visual review before publication",
    }



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-primary-size", type=int, nargs=2)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in {".heif", ".heic", ".hif"}:
        parser.error("Supply one manually attached HEIF/HEIC/HIF file")
    if source.stat().st_size > MAX_FILE:
        parser.error("Source exceeds 512 MiB limit")
    if args.expected_primary_size:
        dimensions(*args.expected_primary_size)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    directory = args.output_dir.resolve(strict=True)
    try:
        manifest = recover(source, directory, args.expected_primary_size)
        exit_code = 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        manifest = {"status": "failed", "source": str(source), "error": str(exc),
                    "visual_verification": "not performed",
                    "note": "Any candidates are provisional. Stop and offer JPG; do not publish."}
        exit_code = 1
    path = directory / "recovery-manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "manifest": str(path)}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())



"""
Pure stdlib ISOBMFF/HEIF box parser.

Extracts just enough metadata from a HEIC file to drive native reconstruction
without pillow-heif/libheif:
  - whether the primary image is a tiled 'grid' construction or a single image
  - grid geometry (rows, columns, tile canvas size, output width/height)
  - the ordered list of tile item IDs -> mapped to ffmpeg demuxed stream order
  - rotation ('irot') property value associated with the displayed item
"""
import struct
from dataclasses import dataclass, field
from typing import Optional


def iter_boxes(data: bytes, start: int, end: int):
    """Yield (box_type, header_len, payload_start, payload_end) for boxes in [start, end)."""
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        box_type = data[pos + 4:pos + 8].decode("ascii", "replace")
        header_len = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header_len = 16
        elif size == 0:
            size = end - pos
        payload_start = pos + header_len
        payload_end = pos + size
        yield box_type, payload_start, payload_end
        pos += size


def find_box(data: bytes, start: int, end: int, box_type: str):
    for t, ps, pe in iter_boxes(data, start, end):
        if t == box_type:
            return ps, pe
    return None


@dataclass
class HeifInfo:
    primary_item_id: Optional[int] = None
    item_types: dict = field(default_factory=dict)       # item_id -> 4cc type
    iloc: dict = field(default_factory=dict)              # item_id -> [(offset, length), ...]
    dimg_refs: dict = field(default_factory=dict)         # from_item_id -> [to_item_id, ...] (tile order)
    item_props: dict = field(default_factory=dict)        # item_id -> [property_index, ...] (1-based into ipco)
    ipco_props: list = field(default_factory=list)        # list of (prop_type, payload_bytes)

    def rotation_degrees(self, item_id: int) -> int:
        for idx in self.item_props.get(item_id, []):
            prop_type, payload = self.ipco_props[idx - 1]
            if prop_type == "irot":
                return (payload[0] & 0x03) * 90
        return 0

    def grid_geometry(self, grid_item_id: int, data: bytes):
        """Parse the raw ImageGrid construct for the given item id."""
        extents = self.iloc[grid_item_id]
        offset, length = extents[0]
        raw = data[offset:offset + length]
        version, flags = raw[0], raw[1]
        rows = raw[2] + 1
        cols = raw[3] + 1
        field_size = 4 if (flags & 1) else 2
        pos = 4
        width = int.from_bytes(raw[pos:pos + field_size], "big")
        pos += field_size
        height = int.from_bytes(raw[pos:pos + field_size], "big")
        return rows, cols, width, height


def parse_heif(data: bytes) -> HeifInfo:
    info = HeifInfo()
    meta_ps, meta_pe = find_box(data, 0, len(data), "meta")
    if meta_ps is None:
        raise ValueError("No 'meta' box found - not a HEIF/HEIC file")
    # meta is a FullBox: 4 bytes version/flags before child boxes
    meta_ps += 4

    # pitm - primary item
    pitm = find_box(data, meta_ps, meta_pe, "pitm")
    if pitm:
        ps, pe = pitm
        version = data[ps]
        idsize = 2 if version == 0 else 4
        info.primary_item_id = int.from_bytes(data[ps + 4:ps + 4 + idsize], "big")

    # iinf - item info -> item types
    iinf = find_box(data, meta_ps, meta_pe, "iinf")
    if iinf:
        ps, pe = iinf
        version = data[ps]
        pos = ps + 4
        entry_count = struct.unpack(">H", data[pos:pos + 2])[0] if version == 0 else struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 2 if version == 0 else 4
        for t, ips, ipe in iter_boxes(data, pos, pe):
            if t != "infe":
                continue
            iver = data[ips]
            p = ips + 4
            if iver == 3:
                item_id = struct.unpack(">I", data[p:p + 4])[0]
                p += 4
            else:
                # versions 0, 1, and 2 all use a 16-bit item_ID
                item_id = struct.unpack(">H", data[p:p + 2])[0]
                p += 2
            p += 2  # item_protection_index
            item_type = data[p:p + 4].decode("ascii", "replace")
            info.item_types[item_id] = item_type

    # iloc - item locations
    iloc = find_box(data, meta_ps, meta_pe, "iloc")
    if iloc:
        ps, pe = iloc
        version = data[ps]
        p = ps + 4
        sizes_byte = data[p]
        offset_size = sizes_byte >> 4
        length_size = sizes_byte & 0x0F
        p += 1
        base_offset_size = data[p] >> 4
        index_size = data[p] & 0x0F if version in (1, 2) else 0
        p += 1
        if version < 2:
            item_count = struct.unpack(">H", data[p:p + 2])[0]
            p += 2
        else:
            item_count = struct.unpack(">I", data[p:p + 4])[0]
            p += 4
        for _ in range(item_count):
            if version < 2:
                item_id = struct.unpack(">H", data[p:p + 2])[0]
                p += 2
            else:
                item_id = struct.unpack(">I", data[p:p + 4])[0]
                p += 4
            if version in (1, 2):
                p += 2  # construction_method (reserved(12)+method(4) in 2 bytes)
            p += 2  # data_reference_index
            base_offset = int.from_bytes(data[p:p + base_offset_size], "big") if base_offset_size else 0
            p += base_offset_size
            extent_count = struct.unpack(">H", data[p:p + 2])[0]
            p += 2
            extents = []
            for _ in range(extent_count):
                if version in (1, 2) and index_size:
                    p += index_size
                ext_offset = int.from_bytes(data[p:p + offset_size], "big") if offset_size else 0
                p += offset_size
                ext_length = int.from_bytes(data[p:p + length_size], "big") if length_size else 0
                p += length_size
                extents.append((base_offset + ext_offset, ext_length))
            info.iloc[item_id] = extents

    # iref - item references (looking for 'dimg' = derived image tile ordering)
    iref = find_box(data, meta_ps, meta_pe, "iref")
    if iref:
        ps, pe = iref
        version = data[ps]
        idsize = 2 if version == 0 else 4
        pos = ps + 4
        for t, rps, rpe in iter_boxes(data, pos, pe):
            p = rps
            from_id = int.from_bytes(data[p:p + idsize], "big")
            p += idsize
            ref_count = struct.unpack(">H", data[p:p + 2])[0]
            p += 2
            to_ids = []
            for _ in range(ref_count):
                to_ids.append(int.from_bytes(data[p:p + idsize], "big"))
                p += idsize
            if t == "dimg":
                info.dimg_refs[from_id] = to_ids

    # iprp - item properties (ipco + ipma)
    iprp = find_box(data, meta_ps, meta_pe, "iprp")
    if iprp:
        ps, pe = iprp
        ipco = find_box(data, ps, pe, "ipco")
        if ipco:
            cps, cpe = ipco
            for t, pps, ppe in iter_boxes(data, cps, cpe):
                info.ipco_props.append((t, data[pps:ppe]))
        ipma = find_box(data, ps, pe, "ipma")
        if ipma:
            mps, mpe = ipma
            version = data[mps]
            flags = int.from_bytes(data[mps + 1:mps + 4], "big")
            p = mps + 4
            entry_count = struct.unpack(">I", data[p:p + 4])[0]
            p += 4
            for _ in range(entry_count):
                if version == 0:
                    item_id = struct.unpack(">H", data[p:p + 2])[0]
                    p += 2
                else:
                    item_id = struct.unpack(">I", data[p:p + 4])[0]
                    p += 4
                assoc_count = data[p]
                p += 1
                props = []
                for _ in range(assoc_count):
                    if flags & 1:
                        val = struct.unpack(">H", data[p:p + 2])[0]
                        p += 2
                        props.append(val & 0x7FFF)
                    else:
                        val = data[p]
                        p += 1
                        props.append(val & 0x7F)
                info.item_props[item_id] = props

    return info

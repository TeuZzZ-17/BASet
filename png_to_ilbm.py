#!/usr/bin/env python3
"""Convert edited indexed/RGB PNG textures back to template-compatible ILBM.

Inputs are read-only. Output files are written to a separate folder and never
replace the raw extracted VBMP/SKLT/ANM payloads or the ILBM template folder.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


ASCII_TAG = re.compile(rb"^[\x20-\x7e]{4}$")


class PngToIlbmError(Exception):
    pass


@dataclass
class Chunk:
    tag: str
    offset: int
    size: int
    data_start: int
    data_end: int
    padded_end: int
    form_type: Optional[str] = None


@dataclass
class Bmhd:
    width: int
    height: int
    x: int
    y: int
    planes: int
    masking: int
    compression: int
    pad1: int
    transparent_color: int
    x_aspect: int
    y_aspect: int
    page_width: int
    page_height: int


@dataclass
class IlbmTemplate:
    path: Path
    data: bytes
    root: Chunk
    bmhd_chunk: Chunk
    cmap_chunk: Chunk
    body_chunk: Chunk
    bmhd: Bmhd
    cmap: bytes


@dataclass
class ConvertResult:
    png_path: Path
    template_path: Path
    out_path: Path
    width: int
    height: int
    warning: str = ""


def read_u16be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def read_s16be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">h", data, offset)[0]


def read_u32be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def decode_tag(raw: bytes) -> str:
    if len(raw) != 4:
        return "????"
    if ASCII_TAG.match(raw):
        return raw.decode("ascii")
    return "".join(chr(b) if 32 <= b <= 126 else "." for b in raw)


def parse_chunk_at(data: bytes, offset: int, container_end: int) -> Chunk:
    if offset + 8 > container_end:
        raise PngToIlbmError(f"truncated chunk header at 0x{offset:X}")
    tag = decode_tag(data[offset:offset + 4])
    size = read_u32be(data, offset + 4)
    data_start = offset + 8
    data_end = data_start + size
    padded_end = data_end + (size & 1)
    if data_end > container_end:
        raise PngToIlbmError(f"chunk {tag} at 0x{offset:X} extends past container end")
    if padded_end > len(data):
        raise PngToIlbmError(f"chunk {tag} at 0x{offset:X} padding extends past file end")

    form_type = None
    if tag == "FORM":
        if size < 4:
            raise PngToIlbmError(f"FORM at 0x{offset:X} is too small")
        form_type = decode_tag(data[data_start:data_start + 4])
    return Chunk(tag, offset, size, data_start, data_end, padded_end, form_type)


def parse_iff_chunks(data: bytes, start: int, end: int) -> Iterable[Chunk]:
    offset = start
    while offset < end:
        if offset + 8 > end:
            raise PngToIlbmError(f"trailing bytes before FORM end at 0x{offset:X}")
        chunk = parse_chunk_at(data, offset, end)
        yield chunk
        offset = chunk.padded_end


def require_form(data: bytes, expected_type: str, path: Path) -> Chunk:
    if len(data) < 12:
        raise PngToIlbmError(f"{path} is too small to be an IFF FORM")
    root = parse_chunk_at(data, 0, len(data))
    if root.tag != "FORM":
        raise PngToIlbmError(f"{path} does not start with FORM")
    if root.form_type != expected_type:
        raise PngToIlbmError(f"{path} is FORM {root.form_type}, expected FORM {expected_type}")
    return root


def find_chunk(data: bytes, form: Chunk, tag: str) -> Optional[Chunk]:
    for chunk in parse_iff_chunks(data, form.data_start + 4, form.data_end):
        if chunk.tag == tag:
            return chunk
    return None


def read_bmhd(payload: bytes) -> Bmhd:
    if len(payload) < 20:
        raise PngToIlbmError(f"BMHD is too small: {len(payload)} bytes")
    return Bmhd(
        width=read_u16be(payload, 0),
        height=read_u16be(payload, 2),
        x=read_s16be(payload, 4),
        y=read_s16be(payload, 6),
        planes=payload[8],
        masking=payload[9],
        compression=payload[10],
        pad1=payload[11],
        transparent_color=read_u16be(payload, 12),
        x_aspect=payload[14],
        y_aspect=payload[15],
        page_width=read_u16be(payload, 16),
        page_height=read_u16be(payload, 18),
    )


def read_ilbm_template(path: Path) -> IlbmTemplate:
    data = path.read_bytes()
    root = require_form(data, "ILBM", path)
    bmhd_chunk = find_chunk(data, root, "BMHD")
    cmap_chunk = find_chunk(data, root, "CMAP")
    body_chunk = find_chunk(data, root, "BODY")
    if bmhd_chunk is None:
        raise PngToIlbmError("template ILBM is missing BMHD")
    if cmap_chunk is None:
        raise PngToIlbmError("template ILBM is missing CMAP")
    if body_chunk is None:
        raise PngToIlbmError("template ILBM is missing BODY")

    bmhd = read_bmhd(data[bmhd_chunk.data_start:bmhd_chunk.data_end])
    cmap = data[cmap_chunk.data_start:cmap_chunk.data_end]
    if bmhd.planes <= 0 or bmhd.planes > 8:
        raise PngToIlbmError(f"unsupported ILBM bit depth: {bmhd.planes}")
    if len(cmap) < (1 << bmhd.planes) * 3:
        raise PngToIlbmError(f"CMAP is too small for {bmhd.planes} planes")
    if bmhd.compression not in (0, 1):
        raise PngToIlbmError(f"unsupported ILBM compression mode: {bmhd.compression}")

    return IlbmTemplate(path, data, root, bmhd_chunk, cmap_chunk, body_chunk, bmhd, cmap)


def load_pillow_image():
    try:
        from PIL import Image
    except ImportError as exc:
        raise PngToIlbmError(
            "Missing dependency: Pillow. Install it with:\npython -m pip install pillow"
        ) from exc
    return Image


def palette_entries(cmap: bytes, count: int) -> List[Tuple[int, int, int]]:
    return [(cmap[i * 3], cmap[i * 3 + 1], cmap[i * 3 + 2]) for i in range(count)]


def nearest_palette_index(rgb: Tuple[int, int, int], palette: List[Tuple[int, int, int]]) -> int:
    best_index = 0
    best_distance = None
    r, g, b = rgb
    for idx, (pr, pg, pb) in enumerate(palette):
        dr = r - pr
        dg = g - pg
        db = b - pb
        distance = dr * dr + dg * dg + db * db
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_index = idx
    return best_index


def png_to_indices(png_path: Path, template: IlbmTemplate, image_module) -> Tuple[bytes, bool]:
    image = image_module.open(png_path)
    try:
        if image.size != (template.bmhd.width, template.bmhd.height):
            raise PngToIlbmError(
                f"PNG size {image.size[0]}x{image.size[1]} does not match template "
                f"{template.bmhd.width}x{template.bmhd.height}"
            )

        max_colors = 1 << template.bmhd.planes
        palette = palette_entries(template.cmap, max_colors)
        palette_map = {rgb: idx for idx, rgb in enumerate(palette)}
        rgb_image = image.convert("RGB")
        if hasattr(rgb_image, "get_flattened_data"):
            pixels = list(rgb_image.get_flattened_data())
        else:
            pixels = list(rgb_image.getdata())
        out = bytearray(len(pixels))
        warned = False
        for i, rgb in enumerate(pixels):
            idx = palette_map.get(rgb)
            if idx is None:
                idx = nearest_palette_index(rgb, palette)
                warned = True
            out[i] = idx
        return bytes(out), warned
    finally:
        image.close()


def row_bytes_per_plane(width: int) -> int:
    return ((width + 15) // 16) * 2


def pack_ilbm_body(indexed_pixels: bytes, width: int, height: int, planes: int) -> bytes:
    expected = width * height
    if len(indexed_pixels) != expected:
        raise PngToIlbmError(f"pixel data size {len(indexed_pixels)} does not match {width}x{height}")

    row_bytes = row_bytes_per_plane(width)
    body = bytearray(height * planes * row_bytes)
    pos = 0
    for y in range(height):
        row = indexed_pixels[y * width:(y + 1) * width]
        for plane in range(planes):
            for byte_x in range(row_bytes):
                value = 0
                base_x = byte_x * 8
                for bit_pos in range(8):
                    x = base_x + bit_pos
                    if x < width and ((row[x] >> plane) & 1):
                        value |= 0x80 >> bit_pos
                body[pos] = value
                pos += 1
    return bytes(body)


def byterun1_pack_row(row: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(row)
    while i < n:
        run_len = 1
        while i + run_len < n and run_len < 128 and row[i + run_len] == row[i]:
            run_len += 1
        if run_len >= 3:
            out.append((257 - run_len) & 0xFF)
            out.append(row[i])
            i += run_len
            continue

        literal_start = i
        i += 1
        while i < n:
            run_len = 1
            while i + run_len < n and run_len < 128 and row[i + run_len] == row[i]:
                run_len += 1
            if run_len >= 3 or i - literal_start >= 128:
                break
            i += 1
        literal = row[literal_start:i]
        out.append(len(literal) - 1)
        out.extend(literal)
    return bytes(out)


def compress_body_byterun1(raw_body: bytes, width: int, height: int, planes: int) -> bytes:
    row_bytes = row_bytes_per_plane(width)
    expected = height * planes * row_bytes
    if len(raw_body) != expected:
        raise PngToIlbmError(f"raw BODY size {len(raw_body)} does not match expected {expected}")

    out = bytearray()
    pos = 0
    for _y in range(height):
        for _plane in range(planes):
            row = raw_body[pos:pos + row_bytes]
            out.extend(byterun1_pack_row(row))
            pos += row_bytes
    return bytes(out)


def iff_chunk(tag: bytes, payload: bytes) -> bytes:
    if len(tag) != 4:
        raise PngToIlbmError("IFF chunk tag must be 4 bytes")
    data = bytearray(tag)
    data.extend(len(payload).to_bytes(4, "big"))
    data.extend(payload)
    if len(payload) & 1:
        data.append(0)
    return bytes(data)


def replace_body(template: IlbmTemplate, new_body: bytes) -> bytes:
    data = template.data
    before_body = data[template.root.data_start + 4:template.body_chunk.offset]
    after_body = data[template.body_chunk.padded_end:template.root.data_end]
    payload = b"ILBM" + before_body + iff_chunk(b"BODY", new_body) + after_body
    return iff_chunk(b"FORM", payload)


def convert_png_to_ilbm(png_path: Path, template_path: Path, out_path: Path, image_module=None) -> ConvertResult:
    if image_module is None:
        image_module = load_pillow_image()
    template = read_ilbm_template(template_path)
    indices, palette_warning = png_to_indices(png_path, template, image_module)
    raw_body = pack_ilbm_body(indices, template.bmhd.width, template.bmhd.height, template.bmhd.planes)
    if template.bmhd.compression == 1:
        body = compress_body_byterun1(raw_body, template.bmhd.width, template.bmhd.height, template.bmhd.planes)
    else:
        body = raw_body

    out_bytes = replace_body(template, body)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out_bytes)
    validate_written_ilbm(out_path, template.bmhd.width, template.bmhd.height)
    warning = "PNG had colors outside original palette; mapped to nearest palette colors" if palette_warning else ""
    return ConvertResult(png_path, template_path, out_path, template.bmhd.width, template.bmhd.height, warning)


def validate_written_ilbm(path: Path, width: int, height: int) -> None:
    template = read_ilbm_template(path)
    if template.bmhd.width != width or template.bmhd.height != height:
        raise PngToIlbmError(f"generated ILBM validation failed for {path}")


def iter_png_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.png")):
        if path.is_file():
            yield path


def build_template_index(template_dir: Path) -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    for path in sorted(template_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in (".ilbm", ".ilb"):
            index.setdefault(path.stem.lower(), path)
    return index


def output_for_png(input_root: Path, png_file: Path, out_dir: Path, template_path: Path) -> Path:
    rel = png_file.relative_to(input_root)
    suffix = template_path.suffix if template_path.suffix else ".ILBM"
    return out_dir / rel.with_suffix(suffix)


def run_batch(png_dir: Path, template_dir: Path, out_dir: Path, image_module=None) -> Tuple[int, int, int, int]:
    if image_module is None:
        image_module = load_pillow_image()
    templates = build_template_index(template_dir)
    converted = 0
    skipped = 0
    warnings = 0
    errors = 0

    for png_file in iter_png_files(png_dir):
        template_path = templates.get(png_file.stem.lower())
        if template_path is None:
            skipped += 1
            print(f"[ERROR] {png_file.name}: matching ILBM template not found; skipped")
            continue
        out_file = output_for_png(png_dir, png_file, out_dir, template_path)
        try:
            result = convert_png_to_ilbm(png_file, template_path, out_file, image_module)
        except (PngToIlbmError, OSError) as exc:
            skipped += 1
            print(f"[ERROR] {png_file.name}: {exc}; skipped")
            continue

        converted += 1
        if result.warning:
            warnings += 1
            print(f"[WARN] {png_file.name} {result.warning}")
        print(f"[OK] {png_file} -> {out_file} ({result.width}x{result.height})")

    return converted, skipped, warnings, errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert edited PNG textures back to template-compatible ILBM files.")
    parser.add_argument("png_folder", help="folder containing edited PNG textures")
    parser.add_argument("--template-ilbm-dir", required=True, help="folder containing matching ILBM template files")
    parser.add_argument("--out-dir", required=True, help="output folder for converted ILBM files")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    png_dir = Path(args.png_folder)
    template_dir = Path(args.template_ilbm_dir)
    out_dir = Path(args.out_dir)
    if not png_dir.is_dir():
        print(f"[ERROR] PNG folder not found: {png_dir}", file=sys.stderr)
        return 2
    if not template_dir.is_dir():
        print(f"[ERROR] ILBM template folder not found: {template_dir}", file=sys.stderr)
        return 2

    try:
        image_module = load_pillow_image()
        converted, skipped, warnings, errors = run_batch(png_dir, template_dir, out_dir, image_module)
    except PngToIlbmError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print("")
    print("Summary:")
    print(f"  PNG input folder: {png_dir}")
    print(f"  ILBM template folder: {template_dir}")
    print(f"  output folder: {out_dir}")
    print(f"  converted count: {converted}")
    print(f"  skipped count: {skipped}")
    print(f"  warning count: {warnings}")
    print(f"  error count: {errors}")
    return 0 if converted else 1


if __name__ == "__main__":
    raise SystemExit(main())

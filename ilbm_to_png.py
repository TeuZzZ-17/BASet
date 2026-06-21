#!/usr/bin/env python3
"""Convert Amiga IFF ILBM texture files to PNG for OpenUA loose texture overrides.

The converter is read-only for the source ILBM. It preserves the ILBM palette for
normal indexed textures and writes a standard PNG that can be edited or copied to
Data/SetN/Loose as a PNG texture override.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


ASCII_TAG = re.compile(rb"^[\x20-\x7e]{4}$")


class IlbmToPngError(Exception):
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
class IlbmImage:
    path: Path
    width: int
    height: int
    planes: int
    masking: int
    compression: int
    transparent_color: int
    cmap: bytes
    pixels: bytes
    alpha: Optional[bytes] = None


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
        raise IlbmToPngError(f"truncated chunk header at 0x{offset:X}")

    tag = decode_tag(data[offset:offset + 4])
    size = read_u32be(data, offset + 4)
    data_start = offset + 8
    data_end = data_start + size
    padded_end = data_end + (size & 1)

    if data_end > container_end:
        raise IlbmToPngError(f"chunk {tag} at 0x{offset:X} extends past container end")
    if padded_end > len(data):
        raise IlbmToPngError(f"chunk {tag} at 0x{offset:X} padding extends past file end")

    form_type = None
    if tag == "FORM":
        if size < 4:
            raise IlbmToPngError(f"FORM at 0x{offset:X} is too small")
        form_type = decode_tag(data[data_start:data_start + 4])

    return Chunk(tag, offset, size, data_start, data_end, padded_end, form_type)


def parse_iff_chunks(data: bytes, start: int, end: int) -> Iterable[Chunk]:
    offset = start
    while offset < end:
        if offset + 8 > end:
            raise IlbmToPngError(f"trailing bytes before FORM end at 0x{offset:X}")
        chunk = parse_chunk_at(data, offset, end)
        yield chunk
        offset = chunk.padded_end


def require_form(data: bytes, expected_type: str, path: Path) -> Chunk:
    if len(data) < 12:
        raise IlbmToPngError(f"{path} is too small to be an IFF FORM")
    root = parse_chunk_at(data, 0, len(data))
    if root.tag != "FORM":
        raise IlbmToPngError(f"{path} does not start with FORM")
    if root.form_type != expected_type:
        raise IlbmToPngError(f"{path} is FORM {root.form_type}, expected FORM {expected_type}")
    return root


def find_chunk(data: bytes, form: Chunk, tag: str) -> Optional[Chunk]:
    for chunk in parse_iff_chunks(data, form.data_start + 4, form.data_end):
        if chunk.tag == tag:
            return chunk
    return None


def read_bmhd(payload: bytes) -> Bmhd:
    if len(payload) < 20:
        raise IlbmToPngError(f"BMHD is too small: {len(payload)} bytes")
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


def row_bytes_per_plane(width: int) -> int:
    return ((width + 15) // 16) * 2


def byterun1_unpack(data: bytes, expected_size: int) -> bytes:
    out = bytearray()
    pos = 0
    data_len = len(data)

    while len(out) < expected_size and pos < data_len:
        control = data[pos]
        pos += 1
        signed = control if control < 128 else control - 256

        if 0 <= signed <= 127:
            count = signed + 1
            if pos + count > data_len:
                raise IlbmToPngError("ByteRun1 literal run extends past BODY end")
            out.extend(data[pos:pos + count])
            pos += count
        elif -127 <= signed <= -1:
            count = 1 - signed
            if pos >= data_len:
                raise IlbmToPngError("ByteRun1 repeat run is missing byte value")
            value = data[pos]
            pos += 1
            out.extend([value] * count)
        else:
            # -128 is a no-op in ByteRun1.
            continue

    if len(out) < expected_size:
        raise IlbmToPngError(f"ByteRun1 decoded {len(out)} bytes, expected {expected_size}")
    if len(out) > expected_size:
        out = out[:expected_size]
    return bytes(out)


def unpack_planar_pixels(raw_body: bytes, bmhd: Bmhd) -> Tuple[bytes, Optional[bytes]]:
    width = bmhd.width
    height = bmhd.height
    planes = bmhd.planes
    row_bytes = row_bytes_per_plane(width)
    mask_planes = 1 if bmhd.masking == 1 else 0
    expected = height * (planes + mask_planes) * row_bytes
    if len(raw_body) != expected:
        raise IlbmToPngError(f"raw BODY size {len(raw_body)} does not match expected {expected}")

    pixels = bytearray(width * height)
    alpha = bytearray(width * height) if mask_planes else None
    pos = 0

    for y in range(height):
        plane_rows = []
        for _plane in range(planes):
            plane_rows.append(raw_body[pos:pos + row_bytes])
            pos += row_bytes

        mask_row = None
        if mask_planes:
            mask_row = raw_body[pos:pos + row_bytes]
            pos += row_bytes

        for x in range(width):
            bit = 0x80 >> (x & 7)
            byte_index = x >> 3
            value = 0
            for plane, row in enumerate(plane_rows):
                if row[byte_index] & bit:
                    value |= 1 << plane
            idx = y * width + x
            pixels[idx] = value
            if alpha is not None:
                alpha[idx] = 255 if mask_row is not None and (mask_row[byte_index] & bit) else 0

    if bmhd.masking == 2:
        alpha = bytearray(width * height)
        transparent = bmhd.transparent_color & 0xFF
        for idx, value in enumerate(pixels):
            alpha[idx] = 0 if value == transparent else 255

    return bytes(pixels), bytes(alpha) if alpha is not None else None


def read_ilbm(path: Path) -> IlbmImage:
    data = path.read_bytes()
    root = require_form(data, "ILBM", path)
    bmhd_chunk = find_chunk(data, root, "BMHD")
    cmap_chunk = find_chunk(data, root, "CMAP")
    body_chunk = find_chunk(data, root, "BODY")

    if bmhd_chunk is None:
        raise IlbmToPngError("ILBM is missing BMHD chunk")
    if cmap_chunk is None:
        raise IlbmToPngError("ILBM is missing CMAP chunk")
    if body_chunk is None:
        raise IlbmToPngError("ILBM is missing BODY chunk")

    bmhd = read_bmhd(data[bmhd_chunk.data_start:bmhd_chunk.data_end])
    if bmhd.width <= 0 or bmhd.height <= 0:
        raise IlbmToPngError(f"invalid ILBM size: {bmhd.width}x{bmhd.height}")
    if bmhd.planes <= 0 or bmhd.planes > 8:
        raise IlbmToPngError(f"unsupported ILBM bit depth: {bmhd.planes}")
    if bmhd.masking not in (0, 1, 2):
        raise IlbmToPngError(f"unsupported ILBM masking mode: {bmhd.masking}")
    if bmhd.compression not in (0, 1):
        raise IlbmToPngError(f"unsupported ILBM compression mode: {bmhd.compression}")

    cmap = data[cmap_chunk.data_start:cmap_chunk.data_end]
    required_cmap = (1 << bmhd.planes) * 3
    if len(cmap) < required_cmap:
        raise IlbmToPngError(f"CMAP is too small for {bmhd.planes} planes")

    row_bytes = row_bytes_per_plane(bmhd.width)
    mask_planes = 1 if bmhd.masking == 1 else 0
    expected_body_size = bmhd.height * (bmhd.planes + mask_planes) * row_bytes
    body_payload = data[body_chunk.data_start:body_chunk.data_end]
    if bmhd.compression == 1:
        raw_body = byterun1_unpack(body_payload, expected_body_size)
    else:
        if len(body_payload) < expected_body_size:
            raise IlbmToPngError(f"BODY size {len(body_payload)} is smaller than expected {expected_body_size}")
        raw_body = body_payload[:expected_body_size]

    pixels, alpha = unpack_planar_pixels(raw_body, bmhd)
    return IlbmImage(
        path=path,
        width=bmhd.width,
        height=bmhd.height,
        planes=bmhd.planes,
        masking=bmhd.masking,
        compression=bmhd.compression,
        transparent_color=bmhd.transparent_color,
        cmap=cmap,
        pixels=pixels,
        alpha=alpha,
    )


def load_pillow_image():
    try:
        from PIL import Image
    except ImportError as exc:
        raise IlbmToPngError(
            "Missing dependency: Pillow. Install it with:\npython -m pip install pillow"
        ) from exc
    return Image


def palette_for_pillow(cmap: bytes) -> List[int]:
    palette = list(cmap[:768])
    if len(palette) < 768:
        palette.extend([0] * (768 - len(palette)))
    return palette


def convert_ilbm_to_png(ilbm_path: Path, out_path: Path, image_module=None) -> IlbmImage:
    if image_module is None:
        image_module = load_pillow_image()
    ilbm = read_ilbm(ilbm_path)

    image = image_module.frombytes("P", (ilbm.width, ilbm.height), ilbm.pixels)
    image.putpalette(palette_for_pillow(ilbm.cmap))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if ilbm.alpha is not None:
        rgba = image.convert("RGBA")
        alpha_image = image_module.frombytes("L", (ilbm.width, ilbm.height), ilbm.alpha)
        rgba.putalpha(alpha_image)
        rgba.save(out_path)
        alpha_image.close()
        rgba.close()
    else:
        image.save(out_path)
    image.close()
    return ilbm


def iter_ilbm_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in (".ilbm", ".ilb", ".iff", ".lbm"):
            yield path


def output_for_batch(input_root: Path, ilbm_file: Path, out_dir: Path) -> Path:
    rel = ilbm_file.relative_to(input_root)
    return out_dir / rel.with_suffix(".png")


def run_batch(input_dir: Path, out_dir: Path, image_module=None) -> Tuple[int, int, int]:
    if image_module is None:
        image_module = load_pillow_image()
    converted = 0
    skipped = 0
    errors = 0

    for ilbm_file in iter_ilbm_files(input_dir):
        out_file = output_for_batch(input_dir, ilbm_file, out_dir)
        try:
            ilbm = convert_ilbm_to_png(ilbm_file, out_file, image_module)
        except (IlbmToPngError, OSError) as exc:
            skipped += 1
            print(f"[ERROR] {ilbm_file.name}: {exc}; skipped")
            continue
        converted += 1
        print(
            f"[OK] {ilbm_file} -> {out_file} "
            f"({ilbm.width}x{ilbm.height}, {ilbm.planes} planes, compression={ilbm.compression})"
        )

    return converted, skipped, errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert ILBM/ILB texture files to PNG.")
    parser.add_argument("input", help="input ILBM/ILB file or folder")
    parser.add_argument("--out", help="output PNG file when input is a file")
    parser.add_argument("--out-dir", help="output folder when input is a directory")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    input_path = Path(args.input)

    try:
        image_module = load_pillow_image()
        if input_path.is_file():
            if not args.out:
                parser.error("--out is required when input is a file")
            out_path = Path(args.out)
            ilbm = convert_ilbm_to_png(input_path, out_path, image_module)
            print(
                f"[OK] {input_path} -> {out_path} "
                f"({ilbm.width}x{ilbm.height}, {ilbm.planes} planes, compression={ilbm.compression})"
            )
            converted, skipped, errors = 1, 0, 0
            output_summary = str(out_path)
        elif input_path.is_dir():
            if not args.out_dir:
                parser.error("--out-dir is required when input is a directory")
            converted, skipped, errors = run_batch(input_path, Path(args.out_dir), image_module)
            output_summary = str(args.out_dir)
        else:
            parser.error(f"input path is neither file nor directory: {input_path}")
    except IlbmToPngError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    print("")
    print("Summary:")
    print(f"  input path: {input_path}")
    print(f"  output path: {output_summary}")
    print(f"  converted count: {converted}")
    print(f"  skipped count: {skipped}")
    print(f"  error count: {errors}")
    return 0 if converted else 1


if __name__ == "__main__":
    raise SystemExit(main())

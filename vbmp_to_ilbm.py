#!/usr/bin/env python3
"""Convert extracted FORM VBMP textures to standard FORM ILBM files.

This is a helper for the SET.BAS GUI. It is read-only for input files and writes
new ILBM files into a separate output folder. It does not modify SET.BAS or the
raw extracted VBMP payloads.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Tuple

import vbmp_to_png


class IlbmWriteError(Exception):
    pass


def _chunk(tag: bytes, payload: bytes) -> bytes:
    if len(tag) != 4:
        raise IlbmWriteError("IFF chunk tag must be exactly 4 bytes")
    out = bytearray()
    out += tag
    out += len(payload).to_bytes(4, "big")
    out += payload
    if len(payload) & 1:
        out += b"\0"
    return bytes(out)


def _pack_ilbm_body(indexed_pixels: bytes, width: int, height: int, planes: int = 8) -> bytes:
    expected = width * height
    if len(indexed_pixels) != expected:
        raise IlbmWriteError(f"pixel data size {len(indexed_pixels)} does not match {width}x{height}")
    if width <= 0 or height <= 0:
        raise IlbmWriteError("invalid image dimensions")
    if planes != 8:
        raise IlbmWriteError("only 8-plane ILBM output is currently supported")

    # ILBM stores each scanline as bitplanes. Each plane row is word-aligned.
    row_bytes_per_plane = ((width + 15) // 16) * 2
    body = bytearray(height * planes * row_bytes_per_plane)
    pos = 0

    for y in range(height):
        row = indexed_pixels[y * width:(y + 1) * width]
        for plane in range(planes):
            for byte_x in range(row_bytes_per_plane):
                value = 0
                base_x = byte_x * 8
                for bit_pos in range(8):
                    x = base_x + bit_pos
                    if x < width and ((row[x] >> plane) & 1):
                        value |= 0x80 >> bit_pos
                body[pos] = value
                pos += 1

    return bytes(body)


def build_ilbm_bytes(width: int, height: int, pixels: bytes, cmap: bytes) -> bytes:
    if len(cmap) < 768:
        raise IlbmWriteError(f"CMAP is too small: {len(cmap)} bytes; expected 768")
    palette = cmap[:768]

    bmhd = bytearray()
    bmhd += width.to_bytes(2, "big")
    bmhd += height.to_bytes(2, "big")
    bmhd += (0).to_bytes(2, "big", signed=True)  # x
    bmhd += (0).to_bytes(2, "big", signed=True)  # y
    bmhd += bytes([8])       # nPlanes
    bmhd += bytes([0])       # masking: none
    bmhd += bytes([0])       # compression: none
    bmhd += bytes([0])       # pad1
    bmhd += (0).to_bytes(2, "big")  # transparentColor
    bmhd += bytes([10])      # xAspect
    bmhd += bytes([10])      # yAspect
    bmhd += width.to_bytes(2, "big")
    bmhd += height.to_bytes(2, "big")

    body = _pack_ilbm_body(pixels, width, height)
    chunks = _chunk(b"BMHD", bytes(bmhd)) + _chunk(b"CMAP", palette) + _chunk(b"BODY", body)
    form_payload = b"ILBM" + chunks
    return _chunk(b"FORM", form_payload)


def convert_vbmp_to_ilbm(vbmp_path: Path, out_path: Path, palette: bytes) -> vbmp_to_png.VBMPData:
    vbmp = vbmp_to_png.read_vbmp(vbmp_path)
    ilbm_bytes = build_ilbm_bytes(vbmp.width, vbmp.height, vbmp.body, palette)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(ilbm_bytes)
    return vbmp


def output_for_batch(input_root: Path, vbmp_file: Path, out_dir: Path) -> Path:
    rel = vbmp_file.relative_to(input_root)
    return out_dir / rel.with_suffix(".ILBM")


def iter_batch_files(root: Path) -> Iterable[Path]:
    return vbmp_to_png.iter_batch_files(root)


def run_batch(input_dir: Path, out_dir: Path, palette: bytes) -> Tuple[int, int, int]:
    converted = 0
    skipped = 0
    errors = 0
    for vbmp_file in iter_batch_files(input_dir):
        out_file = output_for_batch(input_dir, vbmp_file, out_dir)
        try:
            vbmp = convert_vbmp_to_ilbm(vbmp_file, out_file, palette)
        except (vbmp_to_png.ConvertError, IlbmWriteError, OSError) as exc:
            skipped += 1
            print(f"[SKIP] {vbmp_file} {exc}")
            continue
        converted += 1
        print(f"[OK] {vbmp_file} -> {out_file} ({vbmp.width}x{vbmp.height}, unknown={vbmp.unknown})")
    return converted, skipped, errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert extracted FORM VBMP textures to standard FORM ILBM files.")
    parser.add_argument("input_path", help="FORM VBMP file or folder containing extracted VBMP files")
    parser.add_argument("--palette-ilbm", help="optional reference FORM ILBM file containing CMAP")
    parser.add_argument("--out", help="output ILBM path for single-file mode")
    parser.add_argument("--out-dir", help="output folder for batch directory mode")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    input_path = Path(args.input_path)
    if not input_path.exists():
        parser.error(f"input path does not exist: {input_path}")

    try:
        if args.palette_ilbm:
            palette_path = Path(args.palette_ilbm)
            if not palette_path.is_file():
                print(f"[ERROR] palette ILBM file not found: {palette_path}", file=sys.stderr)
                return 2
            palette = vbmp_to_png.read_ilbm_cmap(palette_path)
            palette_source = str(palette_path)
        else:
            palette = vbmp_to_png.get_builtin_cmap()
            palette_source = vbmp_to_png.BUILTIN_PALETTE_SOURCE
    except (vbmp_to_png.ConvertError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if input_path.is_file():
        if not args.out:
            parser.error("--out is required when input is a file")
        out_file = Path(args.out)
        try:
            vbmp = convert_vbmp_to_ilbm(input_path, out_file, palette)
        except (vbmp_to_png.ConvertError, IlbmWriteError, OSError) as exc:
            print(f"[ERROR] {input_path} {exc}", file=sys.stderr)
            return 1
        print(f"[OK] {input_path} -> {out_file} ({vbmp.width}x{vbmp.height}, unknown={vbmp.unknown})")
        converted, skipped, errors = 1, 0, 0
        output_summary = str(out_file)
    elif input_path.is_dir():
        if not args.out_dir:
            parser.error("--out-dir is required when input is a directory")
        converted, skipped, errors = run_batch(input_path, Path(args.out_dir), palette)
        output_summary = str(args.out_dir)
    else:
        parser.error(f"input path is neither file nor directory: {input_path}")

    print("\nSummary:")
    print(f"  input path: {input_path}")
    print(f"  output path: {output_summary}")
    print(f"  palette source: {palette_source}")
    print(f"  converted count: {converted}")
    print(f"  skipped count: {skipped}")
    print(f"  error count: {errors}")
    return 0 if converted else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Convert extracted FORM VBMP files to indexed PNG.

The converter is read-only for all inputs. It does not modify SET.BAS, engine
code, or source assets. By default it uses a built-in AIR1TXT CMAP palette;
--palette-ilbm can still override it for CLI testing.
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
BUILTIN_PALETTE_SOURCE = "built-in AIR1TXT.ILBM CMAP"
BUILTIN_AIR1TXT_CMAP = bytes.fromhex(
    "ff ff 00 ff ff ff da da da 9b 9b 9b 6d 6d 6d 49 49 49 00 00 00 9b c5 d0 00 82 ff 00 00 ff ff 00 00 ff ab 1c 00 d9 51 c8 37 b2 ff ff 88 00 89 aa"
    "00 00 00 ff ff ff a5 e8 ff 6d bd b7 60 c2 a4 60 c7 92 9b c7 ac 62 b9 c9 63 b4 dc 64 b0 ef ce ba d8 dc b9 ab f7 c5 8d ff ef 65 ff 8d 47 ff 61 61"
    "00 00 00 ee ee ee 9a d8 ee 65 b0 aa 59 b5 99 59 b9 88 90 b9 a0 5b ac bb 5c a8 cd 5d a4 df c0 ad c9 cd ac 9f e6 b7 83 ee df 5e ee 83 42 ee 5a 5a"
    "00 00 00 dd dd dd 8f c9 dd 5e a3 9e 53 a8 8e 53 ac 7e 86 ac 95 54 a0 ae 55 9c be 56 98 cf b2 a1 bb be a0 94 d6 aa 7a dd cf 57 dd 7a 3d dd 54 54"
    "00 00 00 cc cc cc 84 b9 cc 57 97 92 4c 9b 83 4c 9f 74 7c 9f 89 4e 94 a0 4f 90 af 4f 8c bf a4 94 ac af 94 88 c5 9d 70 cc bf 50 cc 70 38 cc 4d 4d"
    "00 00 00 bb bb bb 79 aa bb 4f 8a 86 46 8e 78 46 91 6b 71 91 7e 47 87 93 48 84 a1 49 81 af 97 88 9e a1 87 7d b5 90 67 bb af 4a bb 67 34 bb 47 47"
    "00 00 00 aa aa aa 6e 9a aa 48 7e 7a 40 81 6d 40 84 61 67 84 72 41 7b 86 42 78 92 42 75 9f 89 7c 90 92 7b 72 a4 83 5e aa 9f 43 aa 5e 2f aa 40 40"
    "00 00 00 99 99 99 63 8b 99 41 71 6d 39 74 62 39 77 57 5d 77 67 3a 6f 78 3b 6c 83 3b 69 8f 7b 6f 81 83 6f 66 94 76 54 99 8f 3c 99 54 2a 99 3a 3a"
    "00 00 00 88 88 88 58 7b 88 3a 64 61 33 67 57 33 6a 4d 52 6a 5b 34 62 6b 34 60 75 35 5d 7f 6d 63 73 75 62 5b 83 69 4b 88 7f 35 88 4b 25 88 33 33"
    "00 00 00 77 77 77 4d 6c 77 32 58 55 2c 5a 4c 2c 5c 44 48 5c 50 2d 56 5d 2e 54 66 2e 52 6f 60 56 64 66 56 4f 73 5b 41 77 6f 2f 77 41 21 77 2d 2d"
    "00 00 00 66 66 66 42 5c 66 2b 4b 49 26 4d 41 26 4f 3a 3e 4f 44 27 4a 50 27 48 57 27 46 5f 52 4a 56 57 4a 44 62 4e 38 66 5f 28 66 38 1c 66 26 26"
    "00 00 00 55 55 55 37 4d 55 24 3f 3d 20 40 36 20 42 30 33 42 39 20 3d 43 21 3c 49 21 3a 4f 44 3e 48 49 3d 39 52 41 2f 55 4f 21 55 2f 17 55 20 20"
    "00 00 00 44 44 44 2c 3d 44 1d 32 30 19 33 2b 19 35 26 29 35 2d 1a 31 35 1a 30 3a 1a 2e 3f 36 31 39 3a 31 2d 41 34 25 44 3f 1a 44 25 12 44 19 19"
    "00 00 00 33 33 33 21 2e 33 15 25 24 13 26 20 13 27 1d 1f 27 22 13 25 28 13 24 2b 13 23 2f 29 25 2b 2b 25 22 31 27 1c 33 2f 14 33 1c 0e 33 13 13"
    "00 00 00 22 22 22 16 1e 22 0e 19 18 0c 19 15 0c 1a 13 14 1a 16 0d 18 1a 0d 18 1d 0d 17 1f 1b 18 1c 1d 18 16 20 1a 12 22 1f 0d 22 12 09 22 0c 0c"
    "00 00 00 11 11 11 0b 0f 11 07 0c 0c 06 0c 0a 06 0d 09 0a 0d 0b 06 0c 0d 06 0c 0e 06 0b 0f 0d 0c 0e 0e 0c 0b 10 0d 09 11 0f 06 11 09 04 11 06 06"
)


class ConvertError(Exception):
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
class VBMPData:
    path: Path
    width: int
    height: int
    unknown: int
    body: bytes


def read_u16be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


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
        raise ConvertError(f"truncated chunk header at 0x{offset:X}")

    tag = decode_tag(data[offset:offset + 4])
    size = read_u32be(data, offset + 4)
    data_start = offset + 8
    data_end = data_start + size
    padded_end = data_end + (size & 1)

    if data_end > container_end:
        raise ConvertError(
            f"chunk {tag} at 0x{offset:X} declares size {size}, past container end 0x{container_end:X}"
        )
    if padded_end > len(data):
        raise ConvertError(f"chunk {tag} at 0x{offset:X} padding extends past file end")

    form_type = None
    if tag == "FORM":
        if size < 4:
            raise ConvertError(f"FORM at 0x{offset:X} is too small")
        form_type = decode_tag(data[data_start:data_start + 4])

    return Chunk(tag, offset, size, data_start, data_end, padded_end, form_type)


def parse_iff_chunks(data: bytes, start: int, end: int) -> Iterable[Chunk]:
    offset = start
    while offset < end:
        if offset + 8 > end:
            raise ConvertError(f"trailing bytes before FORM end at 0x{offset:X}")
        chunk = parse_chunk_at(data, offset, end)
        yield chunk
        offset = chunk.padded_end


def require_form(data: bytes, expected_type: str, path: Path) -> Chunk:
    if len(data) < 12:
        raise ConvertError(f"{path} is too small to be an IFF FORM")
    root = parse_chunk_at(data, 0, len(data))
    if root.tag != "FORM":
        raise ConvertError(f"{path} does not start with FORM")
    if root.form_type != expected_type:
        raise ConvertError(f"{path} is FORM {root.form_type}, expected FORM {expected_type}")
    return root


def find_chunk(data: bytes, form: Chunk, tag: str) -> Optional[Chunk]:
    for chunk in parse_iff_chunks(data, form.data_start + 4, form.data_end):
        if chunk.tag == tag:
            return chunk
    return None


def read_vbmp(path: Path) -> VBMPData:
    data = path.read_bytes()
    form = require_form(data, "VBMP", path)
    head = find_chunk(data, form, "HEAD")
    body = find_chunk(data, form, "BODY")

    if head is None:
        raise ConvertError("VBMP is missing HEAD chunk")
    if body is None:
        raise ConvertError("VBMP is missing BODY chunk")

    head_payload = data[head.data_start:head.data_end]
    if len(head_payload) < 6:
        raise ConvertError(f"HEAD chunk is too small: {len(head_payload)} bytes")

    width = read_u16be(head_payload, 0)
    height = read_u16be(head_payload, 2)
    unknown = read_u16be(head_payload, 4)
    body_payload = data[body.data_start:body.data_end]
    expected = width * height

    if len(body_payload) != expected:
        raise ConvertError(f"BODY size {len(body_payload)} does not match width*height {expected}")

    return VBMPData(path, width, height, unknown, body_payload)


def read_ilbm_cmap(path: Path) -> bytes:
    data = path.read_bytes()
    form = require_form(data, "ILBM", path)
    cmap = find_chunk(data, form, "CMAP")
    if cmap is None:
        raise ConvertError("reference ILBM is missing CMAP chunk")

    payload = data[cmap.data_start:cmap.data_end]
    if len(payload) < 768:
        raise ConvertError(f"CMAP is too small: {len(payload)} bytes; expected at least 768")
    return payload[:768]


def get_builtin_cmap() -> bytes:
    return BUILTIN_AIR1TXT_CMAP


def load_pillow_image():
    try:
        from PIL import Image
    except ImportError as exc:
        raise ConvertError(
            "Missing dependency: Pillow. Install it with:\npython -m pip install pillow"
        ) from exc
    return Image


def convert_vbmp_to_png(vbmp_path: Path, out_path: Path, palette: bytes, image_module) -> VBMPData:
    vbmp = read_vbmp(vbmp_path)
    image = image_module.frombytes("P", (vbmp.width, vbmp.height), vbmp.body)
    image.putpalette(list(palette))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return vbmp


def output_for_batch(input_root: Path, vbmp_file: Path, out_dir: Path) -> Path:
    rel = vbmp_file.relative_to(input_root)
    return out_dir / rel.with_suffix(".png")


def iter_batch_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def convert_one(vbmp_file: Path, out_file: Path, palette: bytes, image_module) -> Tuple[bool, str]:
    try:
        vbmp = convert_vbmp_to_png(vbmp_file, out_file, palette, image_module)
    except ConvertError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, str(exc)

    print(f"[OK] {vbmp_file} -> {out_file} ({vbmp.width}x{vbmp.height}, unknown={vbmp.unknown})")
    return True, ""


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Tuple[Path, Optional[Path]]:
    input_path = Path(args.input_path)
    if not input_path.exists():
        parser.error(f"input path does not exist: {input_path}")

    if input_path.is_file():
        if not args.out:
            parser.error("--out is required when input is a file")
        return input_path, Path(args.out)

    if input_path.is_dir():
        if not args.out_dir:
            parser.error("--out-dir is required when input is a directory")
        return input_path, None

    parser.error(f"input path is neither file nor directory: {input_path}")
    raise AssertionError("unreachable")


def print_summary(input_path: Path, output_path: str, palette_source: str, converted: int, skipped: int, errors: int) -> None:
    print("")
    print("Summary:")
    print(f"  input path: {input_path}")
    print(f"  output path: {output_path}")
    print(f"  palette source: {palette_source}")
    print(f"  converted count: {converted}")
    print(f"  skipped count: {skipped}")
    print(f"  error count: {errors}")


def run_single(input_file: Path, out_file: Path, palette: bytes, image_module) -> Tuple[int, int, int]:
    ok, reason = convert_one(input_file, out_file, palette, image_module)
    if ok:
        return 1, 0, 0
    print(f"[ERROR] {input_file} {reason}")
    return 0, 0, 1


def run_batch(input_dir: Path, out_dir: Path, palette: bytes, image_module) -> Tuple[int, int, int]:
    converted = 0
    skipped = 0
    errors = 0

    for vbmp_file in iter_batch_files(input_dir):
        out_file = output_for_batch(input_dir, vbmp_file, out_dir)
        ok, reason = convert_one(vbmp_file, out_file, palette, image_module)
        if ok:
            converted += 1
            continue

        skipped += 1
        print(f"[SKIP] {vbmp_file} {reason}")

    return converted, skipped, errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert extracted FORM VBMP files to indexed PNG using the built-in AIR1TXT CMAP palette."
    )
    parser.add_argument("input_path", help="FORM VBMP file or folder containing extracted VBMP files")
    parser.add_argument("--palette-ilbm", help="optional reference FORM ILBM file containing a CMAP palette")
    parser.add_argument("--out", help="output PNG path for single-file mode")
    parser.add_argument("--out-dir", help="output folder for batch directory mode")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    input_path, single_out = validate_args(parser, args)
    try:
        if args.palette_ilbm:
            palette_path = Path(args.palette_ilbm)
            if not palette_path.is_file():
                print(f"[ERROR] palette ILBM file not found: {palette_path}", file=sys.stderr)
                return 2
            palette = read_ilbm_cmap(palette_path)
            palette_source = str(palette_path)
        else:
            palette = get_builtin_cmap()
            palette_source = BUILTIN_PALETTE_SOURCE
        image_module = load_pillow_image()
    except ConvertError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if input_path.is_file():
        out_file = single_out
        assert out_file is not None
        converted, skipped, errors = run_single(input_path, out_file, palette, image_module)
        output_summary = str(out_file)
        return_code = 0 if converted else 1
    else:
        out_dir = Path(args.out_dir)
        converted, skipped, errors = run_batch(input_path, out_dir, palette, image_module)
        output_summary = str(out_dir)
        return_code = 0 if converted > 0 else 1

    print_summary(input_path, output_summary, palette_source, converted, skipped, errors)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Extract raw EMRS payloads from a local Urban Assault/OpenUA SET.BAS file.

This tool is intentionally read-only for SET.BAS. It does not decode VBMP/ILBM
graphics and does not modify game data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple


ASCII_TAG = re.compile(rb"^[\x20-\x7e]{4}$")
DEFAULT_CLASS = "ilbm.class"


class ExtractError(Exception):
    pass


@dataclass
class Chunk:
    tag: str
    offset: int
    size: int
    data_start: int
    data_end: int
    padded_end: int
    container_end: int
    depth: int
    path: str
    form_type: Optional[str] = None

    @property
    def full_bytes_start(self) -> int:
        return self.offset

    @property
    def full_bytes_end(self) -> int:
        return self.data_end


@dataclass
class EmrsRecord:
    index: int
    class_name: str
    resource_name: str
    emrs_offset: int
    emrs_size: int
    payload: Optional[Chunk]
    payload_source: str
    error: Optional[str] = None


def read_u32be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def decode_tag(raw: bytes) -> str:
    if len(raw) != 4:
        return "????"
    if ASCII_TAG.match(raw):
        return raw.decode("ascii")
    return "".join(chr(b) if 32 <= b <= 126 else "." for b in raw)


def c_string(data: bytes, pos: int, limit: int) -> Tuple[str, int]:
    end = data.find(b"\0", pos, limit)
    if end < 0:
        raise ExtractError("unterminated C string")
    text = data[pos:end].decode("latin-1", errors="replace")
    return text, end + 1


def parse_chunk_at(data: bytes, offset: int, container_end: int, depth: int, path: str) -> Chunk:
    if offset + 8 > container_end:
        raise ExtractError(f"truncated chunk header at 0x{offset:X}")

    tag = decode_tag(data[offset:offset + 4])
    size = read_u32be(data, offset + 4)
    data_start = offset + 8
    data_end = data_start + size
    padded_end = data_end + (size & 1)

    if data_end > container_end:
        raise ExtractError(
            f"chunk {tag} at 0x{offset:X} declares size {size}, past container end 0x{container_end:X}"
        )
    if padded_end > len(data):
        raise ExtractError(f"chunk {tag} at 0x{offset:X} padding extends past file end")

    form_type = None
    if tag == "FORM":
        if size < 4:
            raise ExtractError(f"FORM at 0x{offset:X} is too small")
        form_type = decode_tag(data[data_start:data_start + 4])

    return Chunk(
        tag=tag,
        offset=offset,
        size=size,
        data_start=data_start,
        data_end=data_end,
        padded_end=padded_end,
        container_end=container_end,
        depth=depth,
        path=path,
        form_type=form_type,
    )


def iter_container_chunks(data: bytes, start: int, end: int, depth: int, path: str) -> Iterable[Chunk]:
    offset = start
    index = 0
    while offset < end:
        if offset + 8 > end:
            raise ExtractError(f"trailing bytes before container end at 0x{offset:X}")
        chunk = parse_chunk_at(data, offset, end, depth, f"{path}/{index}")
        yield chunk
        offset = chunk.padded_end
        index += 1


def parse_form_children(data: bytes, form: Chunk) -> List[Chunk]:
    if form.tag != "FORM":
        return []
    return list(iter_container_chunks(data, form.data_start + 4, form.data_end, form.depth + 1, form.path))


def parse_inline_payload_after_strings(data: bytes, emrs: Chunk, pos: int) -> Optional[Chunk]:
    while pos < emrs.data_end and data[pos] == 0:
        pos += 1
    if pos + 8 > emrs.data_end:
        return None
    try:
        chunk = parse_chunk_at(data, pos, emrs.data_end, emrs.depth + 1, emrs.path + "/inline")
    except ExtractError:
        return None
    return chunk


def parse_emrs_record(data: bytes, emrs: Chunk, next_sibling: Optional[Chunk], index: int) -> EmrsRecord:
    try:
        class_name, pos = c_string(data, emrs.data_start, emrs.data_end)
        resource_name, pos = c_string(data, pos, emrs.data_end)
    except ExtractError as exc:
        return EmrsRecord(index, "", "", emrs.offset, emrs.size, None, "none", str(exc))

    inline_payload = parse_inline_payload_after_strings(data, emrs, pos)
    if inline_payload is not None:
        return EmrsRecord(index, class_name, resource_name, emrs.offset, emrs.size, inline_payload, "inline")

    if next_sibling is None:
        return EmrsRecord(index, class_name, resource_name, emrs.offset, emrs.size, None, "none", "missing payload")

    return EmrsRecord(index, class_name, resource_name, emrs.offset, emrs.size, next_sibling, "next_sibling")


def walk_for_emrs(data: bytes, container_start: int, container_end: int, depth: int, path: str, records: List[EmrsRecord]) -> None:
    chunks = list(iter_container_chunks(data, container_start, container_end, depth, path))
    for i, chunk in enumerate(chunks):
        if chunk.tag == "EMRS":
            next_sibling = chunks[i + 1] if i + 1 < len(chunks) else None
            records.append(parse_emrs_record(data, chunk, next_sibling, len(records)))

        if chunk.tag == "FORM":
            walk_for_emrs(data, chunk.data_start + 4, chunk.data_end, depth + 1, chunk.path, records)


def load_setbas(path: Path) -> Tuple[bytes, Chunk]:
    data = path.read_bytes()
    if len(data) < 12:
        raise ExtractError("file is too small to be an IFF FORM")
    root = parse_chunk_at(data, 0, len(data), 0, "root")
    if root.tag != "FORM":
        raise ExtractError("SET.BAS root is not an IFF FORM")
    return data, root


def sanitize_component(component: str) -> str:
    component = component.replace("\\", "/")
    component = component.strip()
    component = re.sub(r"[:*?\"<>|]", "_", component)
    component = re.sub(r"[\x00-\x1f]", "_", component)
    component = component.strip(" .")
    return component or "_"


def safe_resource_path(resource_name: str) -> PurePosixPath:
    normalized = resource_name.replace("\\", "/")
    parts = []
    for part in PurePosixPath(normalized).parts:
        if part in ("", ".", ".."):
            continue
        parts.append(sanitize_component(part))
    if not parts:
        parts = ["unnamed_resource"]
    return PurePosixPath(*parts)


def safe_class_dir(class_name: str) -> str:
    return sanitize_component(class_name.replace("/", "_").replace("\\", "_"))


def friendly_raw_dir(class_name: str) -> str:
    """Return the user-facing raw output folder for a known EMRS class.

    These names are convenience folders for extracted payload types, not original
    SET.BAS directory names. Unknown classes keep a sanitized class-based name.
    """
    mapping = {
        "ilbm.class": "VBMP",
        "sklt.class": "SKLT",
        "bmpanim.class": "ANM",
    }
    return mapping.get(class_name, safe_class_dir(class_name))


def flattened_resource_name(resource_name: str) -> str:
    """Return only the resource filename, dropping logical subfolders like Skeleton/."""
    rel = safe_resource_path(resource_name)
    return rel.name or "unnamed_resource"


def unique_output_path(raw_root: Path, class_name: str, resource_name: str, seen: Dict[Tuple[str, str], int]) -> Tuple[Path, int]:
    class_dir = friendly_raw_dir(class_name)
    file_name = flattened_resource_name(resource_name)
    key = (class_dir, file_name)
    duplicate_index = seen[key]
    seen[key] += 1

    rel = Path(file_name)
    if duplicate_index:
        rel = rel.with_name(f"{rel.stem}__dup{duplicate_index:03d}{rel.suffix}")

    return raw_root / class_dir / rel, duplicate_index


def should_extract(record: EmrsRecord, selected_class: str, all_classes: bool) -> bool:
    if record.error:
        return False
    return all_classes or record.class_name == selected_class


def payload_bytes(data: bytes, chunk: Chunk) -> bytes:
    return data[chunk.full_bytes_start:chunk.full_bytes_end]


def payload_form_type(chunk: Optional[Chunk]) -> str:
    if chunk is None:
        return ""
    return chunk.form_type or ""


def manifest_row(record: EmrsRecord, output_rel: str, dumped: bytes, duplicate_index: int, extracted: bool) -> Dict[str, object]:
    payload = record.payload
    sha1 = hashlib.sha1(dumped).hexdigest() if dumped else ""
    return {
        "index": record.index,
        "class_name": record.class_name,
        "resource_name": record.resource_name,
        "emrs_offset": record.emrs_offset,
        "emrs_size": record.emrs_size,
        "payload_source": record.payload_source,
        "payload_tag": payload.tag if payload else "",
        "payload_form_type": payload_form_type(payload),
        "payload_declared_size": payload.size if payload else 0,
        "payload_offset_start": payload.offset if payload else 0,
        "payload_offset_end": payload.data_end if payload else 0,
        "payload_sha1": sha1,
        "output_path": output_rel,
        "duplicate_index": duplicate_index,
        "extracted": extracted,
        "error": record.error or "",
    }


def write_manifest_json(path: Path, rows: List[Dict[str, object]], source: Path, root_form_type: str) -> None:
    payload = {
        "source": str(source),
        "root_form_type": root_form_type,
        "resources": rows,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_manifest_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    fields = [
        "index",
        "class_name",
        "resource_name",
        "emrs_offset",
        "emrs_size",
        "payload_source",
        "payload_tag",
        "payload_form_type",
        "payload_declared_size",
        "payload_offset_start",
        "payload_offset_end",
        "payload_sha1",
        "output_path",
        "duplicate_index",
        "extracted",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def print_summary(
    total: int,
    extracted: int,
    skipped_by_class: Counter,
    payload_counts: Counter,
    duplicate_count: int,
    errors: int,
    warnings: int,
) -> None:
    print("")
    print("Summary:")
    print(f"  total EMRS found: {total}")
    print(f"  extracted count: {extracted}")
    print("  skipped count by class:")
    if skipped_by_class:
        for class_name, count in sorted(skipped_by_class.items()):
            print(f"    {class_name}: {count}")
    else:
        print("    <none>")
    print("  payload form counts:")
    if payload_counts:
        for form_name, count in sorted(payload_counts.items()):
            print(f"    {form_name}: {count}")
    else:
        print("    <none>")
    print(f"  duplicate count: {duplicate_count}")
    print(f"  errors/warnings count: {errors}/{warnings}")


def extract(args: argparse.Namespace) -> int:
    source = Path(args.setbas).resolve()
    out_dir = Path(args.out).resolve()
    manifest_json = out_dir / args.manifest_json if args.manifest_json else None
    manifest_csv = out_dir / args.manifest_csv if args.manifest_csv else None
    raw_root = out_dir / "raw"

    if not source.exists():
        print(f"error: SET.BAS not found: {source}", file=sys.stderr)
        return 2
    if not source.is_file():
        print(f"error: SET.BAS path is not a file: {source}", file=sys.stderr)
        return 2

    try:
        data, root = load_setbas(source)
        records: List[EmrsRecord] = []
        walk_for_emrs(data, root.data_start + 4, root.data_end, 1, "root", records)
    except ExtractError as exc:
        print(f"error: parse failed: {exc}", file=sys.stderr)
        return 1

    rows: List[Dict[str, object]] = []
    seen: Dict[Tuple[str, str], int] = defaultdict(int)
    skipped_by_class: Counter = Counter()
    payload_counts: Counter = Counter()
    extracted_count = 0
    duplicate_count = 0
    errors = 0
    warnings = 0

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_root.mkdir(parents=True, exist_ok=True)

    for record in records:
        if record.error:
            if not args.all_classes and record.class_name and record.class_name != args.class_name:
                skipped_by_class[record.class_name] += 1
                rows.append(manifest_row(record, "", b"", 0, False))
            else:
                errors += 1
                rows.append(manifest_row(record, "", b"", 0, False))
                if args.verbose:
                    print(f"[ERROR] EMRS at 0x{record.emrs_offset:X}: {record.error}")
            continue

        payload = record.payload
        if payload is not None:
            count_key = payload.form_type if payload.tag == "FORM" and payload.form_type else payload.tag
            payload_counts[count_key] += 1

        if not should_extract(record, args.class_name, args.all_classes):
            skipped_by_class[record.class_name] += 1
            rows.append(manifest_row(record, "", b"", 0, False))
            continue

        if payload is None:
            errors += 1
            rows.append(manifest_row(record, "", b"", 0, False))
            if args.verbose:
                print(f"[ERROR] {record.resource_name}: missing payload")
            continue

        out_path, duplicate_index = unique_output_path(raw_root, record.class_name, record.resource_name, seen)
        duplicate_count += 1 if duplicate_index else 0
        dumped = payload_bytes(data, payload)
        output_rel = str(out_path.relative_to(out_dir)).replace("\\", "/")

        if not args.dry_run:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(dumped)

        rows.append(manifest_row(record, output_rel, dumped, duplicate_index, True))
        extracted_count += 1
        if args.verbose:
            form_info = f" {payload.form_type}" if payload.tag == "FORM" and payload.form_type else ""
            action = "WOULD EXTRACT" if args.dry_run else "EXTRACT"
            print(f"[{action}] {record.class_name} {record.resource_name} -> {output_rel} ({payload.tag}{form_info})")

    if args.dry_run:
        warnings += 1
        print("dry-run: no payload files or manifests written")
    else:
        if manifest_json is not None:
            write_manifest_json(manifest_json, rows, source, root.form_type or "")
            print(f"wrote {manifest_json}")
        if manifest_csv is not None:
            write_manifest_csv(manifest_csv, rows)
            print(f"wrote {manifest_csv}")

    print_summary(len(records), extracted_count, skipped_by_class, payload_counts, duplicate_count, errors, warnings)
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract raw EMRS payload chunks from a local OpenUA/Urban Assault SET.BAS.",
    )
    parser.add_argument("setbas", metavar="path_to_SET.BAS", help="local SET.BAS file to parse read-only")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--class", dest="class_name", default=DEFAULT_CLASS, help="EMRS class to extract (default: ilbm.class)")
    parser.add_argument("--all-classes", action="store_true", help="extract every EMRS class")
    parser.add_argument("--manifest-json", default="manifest.json", help="manifest JSON filename (default: manifest.json)")
    parser.add_argument("--manifest-csv", default="", help="optional manifest CSV filename (disabled by default)")
    parser.add_argument("--dry-run", action="store_true", help="parse and report without writing payload files or manifests")
    parser.add_argument("--verbose", action="store_true", help="print each extracted resource")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return extract(args)


if __name__ == "__main__":
    raise SystemExit(main())

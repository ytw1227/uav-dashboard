#!/usr/bin/env python3
"""Pack or restore the immutable data shipped with the Render dashboard.

Build data-000.part, data-001.part, ... and manifest.json::

    python tools/render_data.py --pack --source-data /original/project/data

Render build command (verifies all pieces and restored files)::

    python tools/render_data.py

The source-data directory defines the allowed file names only. Their contents
are read from the current project's data directory, including its compacted
50-region HTML. Runtime-created files absent from the original inventory are
never included. All operations use the Python standard library.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile


PROJECT = Path(__file__).resolve().parents[1]
PART_BYTES = 2_097_152
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
PART_NAME = re.compile(r"data-[0-9]{3,}\.part\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def relative_path(value):
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value,
            f"Invalid relative path: {value!r}")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(part not in ("", ".", "..") for part in value.split("/")),
            f"Unsafe archive path: {value!r}")
    require(path.as_posix() == value, f"Non-canonical archive path: {value!r}")
    return path


def plain_path(root, relative, *, file_required=False):
    """Reject symlinks anywhere underneath the selected data root."""
    require(not root.is_symlink(), f"Symlink data root is not allowed: {root}")
    if root.exists():
        require(root.is_dir(), f"Data root is not a directory: {root}")
    path = root
    parts = relative_path(relative).parts
    for index, part in enumerate(parts):
        path = path / part
        require(not path.is_symlink(), f"Symlink is not allowed: {path}")
        if path.exists() and index < len(parts) - 1:
            require(path.is_dir(), f"Non-directory parent: {path}")
    if file_required:
        require(path.is_file() and stat.S_ISREG(path.stat().st_mode), f"Missing regular file: {path}")
    elif path.exists():
        require(path.is_file(), f"Destination is not a regular file: {path}")
    return path


def hash_file(path):
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            total += len(chunk)
    return total, digest.hexdigest()


def original_inventory(source_data):
    require(source_data.is_dir() and not source_data.is_symlink(),
            f"Original data directory is required: {source_data}")
    names = []
    for path in sorted(source_data.rglob("*"), key=lambda item: item.relative_to(source_data).as_posix()):
        require(not path.is_symlink(), f"Source inventory contains a symlink: {path}")
        if path.is_dir():
            continue
        require(path.is_file() and stat.S_ISREG(path.stat().st_mode),
                f"Source inventory contains a special file: {path}")
        name = path.relative_to(source_data).as_posix()
        relative_path(name)
        names.append(name)
    require(names and len(names) == len(set(names)), "Original data inventory is empty or duplicated")
    return names


def pack(source_data, data_dir, parts_dir):
    names = original_inventory(source_data)
    files = []
    for name in names:
        path = plain_path(data_dir, name, file_required=True)
        size, digest = hash_file(path)
        files.append({"path": name, "bytes": size, "sha256": digest})
    require(not parts_dir.is_symlink(), "Parts directory cannot be a symlink")
    if parts_dir.exists():
        require(parts_dir.is_dir(), "Parts destination is not a directory")
        require(all(path.is_file() and not path.is_symlink()
                    and (path.name == "manifest.json" or PART_NAME.fullmatch(path.name))
                    for path in parts_dir.iterdir()), "Parts destination contains unowned files")

    # The complete archive lives only in an external temporary directory.
    with tempfile.TemporaryDirectory(prefix="uav-render-pack-") as scratch:
        scratch = Path(scratch)
        archive_path = scratch / "data.tar.gz"
        with archive_path.open("wb") as compressed:
            with gzip.GzipFile(filename="", mode="wb", fileobj=compressed,
                               compresslevel=6, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                    for record in files:
                        path = plain_path(data_dir, record["path"], file_required=True)
                        info = tarfile.TarInfo(record["path"])
                        info.size = record["bytes"]
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        info.mtime = 0
                        info.mode = 0o644
                        info.type = tarfile.REGTYPE
                        info.pax_headers = {}
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)

        staging = scratch / "parts"
        staging.mkdir()
        parts = []
        archive_hash = hashlib.sha256()
        total = 0
        with archive_path.open("rb") as stream:
            while block := stream.read(PART_BYTES):
                name = f"data-{len(parts):03d}.part"
                (staging / name).write_bytes(block)
                parts.append({"name": name, "bytes": len(block),
                              "sha256": hashlib.sha256(block).hexdigest()})
                archive_hash.update(block)
                total += len(block)
        require(parts, "Empty data archive")
        manifest = {
            "schema_version": 1,
            "format": "uav-render-data-parts-v1",
            "part_bytes": PART_BYTES,
            "archive": {"format": "tar.gz", "bytes": total, "sha256": archive_hash.hexdigest()},
            "unpacked_bytes": sum(record["bytes"] for record in files),
            "parts": parts,
            "files": files,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        parts_dir.mkdir(parents=True, exist_ok=True)
        for old in parts_dir.iterdir():
            old.unlink()
        for path in staging.iterdir():
            shutil.copyfile(path, parts_dir / path.name)
    summary = {"operation": "pack", "files": len(files), "parts": len(parts),
               "part_bytes": PART_BYTES, "archive_bytes": total,
               "archive_sha256": manifest["archive"]["sha256"],
               "unpacked_bytes": manifest["unpacked_bytes"], "parts_dir": str(parts_dir)}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return manifest


def read_manifest(parts_dir):
    manifest_path = parts_dir / "manifest.json"
    require(not manifest_path.is_symlink() and manifest_path.is_file(), "Missing ordinary manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 1
            and manifest.get("format") == "uav-render-data-parts-v1"
            and manifest.get("part_bytes") == PART_BYTES, "Unsupported data manifest")
    archive = manifest.get("archive", {})
    require(archive.get("format") == "tar.gz"
            and isinstance(archive.get("bytes"), int) and archive["bytes"] > 0
            and isinstance(archive.get("sha256"), str) and SHA256.fullmatch(archive["sha256"]),
            "Invalid archive metadata")
    parts = manifest.get("parts")
    require(isinstance(parts, list) and parts, "Missing archive parts")
    for index, record in enumerate(parts):
        require(record.get("name") == f"data-{index:03d}.part", "Part names are not contiguous")
        size = record.get("bytes")
        require(isinstance(size, int) and 0 < size <= PART_BYTES
                and (index == len(parts) - 1 or size == PART_BYTES), "Invalid part size")
        require(isinstance(record.get("sha256"), str) and SHA256.fullmatch(record["sha256"]),
                "Invalid part checksum")
    require(sum(record["bytes"] for record in parts) == archive["bytes"], "Archive byte count mismatch")
    files = manifest.get("files")
    require(isinstance(files, list) and files, "Missing unpacked file inventory")
    names = set()
    for record in files:
        name = record.get("path")
        relative_path(name)
        require(name not in names, f"Duplicate file path: {name}")
        names.add(name)
        require(isinstance(record.get("bytes"), int) and record["bytes"] >= 0, "Invalid file size")
        require(isinstance(record.get("sha256"), str) and SHA256.fullmatch(record["sha256"]),
                "Invalid file checksum")
    require(sum(record["bytes"] for record in files) == manifest.get("unpacked_bytes"),
            "Unpacked byte count mismatch")
    return manifest


def restore(parts_dir, data_dir):
    require(parts_dir.is_dir() and not parts_dir.is_symlink(), "Missing ordinary parts directory")
    manifest = read_manifest(parts_dir)
    require(not data_dir.is_symlink(), "Destination data directory cannot be a symlink")
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    expected = {record["path"]: record for record in manifest["files"]}
    # A sibling staging directory permits same-filesystem final replacements.
    with tempfile.TemporaryDirectory(prefix=".uav-restore-", dir=data_dir.parent) as scratch:
        scratch = Path(scratch)
        archive_path = scratch / "data.tar.gz"
        archive_digest = hashlib.sha256()
        archive_size = 0
        with archive_path.open("wb") as archive_stream:
            for record in manifest["parts"]:
                part = parts_dir / record["name"]
                require(part.is_file() and not part.is_symlink(), f"Missing ordinary part: {part.name}")
                digest = hashlib.sha256()
                total = 0
                with part.open("rb") as stream:
                    for block in iter(lambda: stream.read(256 * 1024), b""):
                        digest.update(block)
                        total += len(block)
                        archive_digest.update(block)
                        archive_size += len(block)
                        archive_stream.write(block)
                require(total == record["bytes"] and digest.hexdigest() == record["sha256"],
                        f"Part checksum/size mismatch: {part.name}")
        require(archive_size == manifest["archive"]["bytes"]
                and archive_digest.hexdigest() == manifest["archive"]["sha256"],
                "Combined archive checksum/size mismatch")

        contents = scratch / "contents"
        contents.mkdir()
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            found = set()
            for member in members:
                relative_path(member.name)
                require(member.isfile() and not member.issym() and not member.islnk(),
                        f"Archive links/directories/special members are not allowed: {member.name}")
                require(member.name in expected and member.name not in found,
                        f"Unexpected or duplicate archive member: {member.name}")
                require(member.size == expected[member.name]["bytes"],
                        f"Unpacked member size mismatch: {member.name}")
                found.add(member.name)
            require(found == set(expected), "Archive does not contain the complete file inventory")
            archive.extractall(path=contents, members=members, filter="data")

        # Verify the complete staging tree before changing any destination file.
        for name, record in expected.items():
            path = plain_path(contents, name, file_required=True)
            size, digest = hash_file(path)
            require(size == record["bytes"] and digest == record["sha256"],
                    f"Restored file checksum/size mismatch: {name}")
            plain_path(data_dir, name)
        data_dir.mkdir(parents=True, exist_ok=True)
        for name in expected:
            destination = plain_path(data_dir, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(plain_path(contents, name, file_required=True), destination)

    summary = {"operation": "restore", "verified_parts": len(manifest["parts"]),
               "verified_files": len(expected), "archive_bytes": manifest["archive"]["bytes"],
               "archive_sha256": manifest["archive"]["sha256"],
               "unpacked_bytes": manifest["unpacked_bytes"], "data_dir": str(data_dir)}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", action="store_true", help="Create deterministic deployment pieces")
    parser.add_argument("--source-data", type=Path,
                        default=PROJECT.parent / "source/uav-dashboard/data",
                        help="Original data directory used only as a file-name inventory")
    parser.add_argument("--data-dir", type=Path, default=PROJECT / "data")
    parser.add_argument("--parts-dir", type=Path, default=PROJECT / "deploy_data")
    args = parser.parse_args()
    if args.pack:
        pack(args.source_data.absolute(), args.data_dir.absolute(), args.parts_dir.absolute())
    else:
        restore(args.parts_dir.absolute(), args.data_dir.absolute())


if __name__ == "__main__":
    main()

"""Offline backup/restore. Stop the Web process before using these commands."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup(source: Path, archive: Path) -> None:
    source = source.resolve(strict=True)
    archive = archive.resolve()
    if archive.is_relative_to(source):
        raise ValueError("backup must be stored outside the data directory")
    database = source / "accounts.sqlite3"
    if database.is_symlink() or not database.is_file():
        raise ValueError("account database missing or invalid")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 1 or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("database is corrupt or uses an unsupported version")
    files = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("backup refuses symbolic links")
        if path.is_file() and path.name != "maintenance.flag":
            files.append(path)
        elif not path.is_file() and not path.is_dir():
            raise ValueError("backup accepts regular files only")
    manifest = {"format": 1, "database_version": version, "release": os.environ.get("LARIO_RELEASE", "development"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "files": {path.relative_to(source).as_posix(): digest(path) for path in files}}
    archive.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output, tarfile.open(fileobj=output, mode="w:gz") as tar:
            data = json.dumps(manifest, ensure_ascii=False).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            info.mode = 0o600
            tar.addfile(info, BytesIO(data))
            for path in files:
                tar.add(path, arcname="data/" + path.relative_to(source).as_posix(), recursive=False)
    except BaseException:
        archive.unlink(missing_ok=True)
        raise


def restore(archive: Path, target: Path) -> None:
    if target.is_symlink():
        raise ValueError("restore target must not be a symbolic link")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = target.resolve()
    if any(target.iterdir()):
        raise ValueError("restore requires an empty target; never overwrite live data")
    with tarfile.open(archive, "r:gz") as tar:
        first = tar.next()
        if first is None or first.name != "manifest.json" or not first.isfile() or first.size > 10_000_000:
            raise ValueError("backup manifest is missing or too large")
        manifest_file = tar.extractfile(first)
        if manifest_file is None:
            raise ValueError("backup manifest is invalid")
        with manifest_file:
            manifest = json.load(manifest_file)
        if manifest.get("format") != 1 or manifest.get("database_version") != 1 or not isinstance(manifest.get("files"), dict):
            raise ValueError("unsupported backup version")
        remaining = dict(manifest["files"])
        total = 0
        count = 0
        while (member := tar.next()) is not None:
            count += 1
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or ".." in path.parts or
                    not path.parts or path.parts[0] != "data" or len(path.parts) < 2 or
                    "\\" in member.name or ":" in member.name or count > 100_000):
                raise ValueError("unsafe backup member")
            relative = path.relative_to("data").as_posix()
            expected = remaining.pop(relative, None)
            if expected is None:
                raise ValueError("unexpected or duplicate backup member")
            total += member.size
            if total > 20_000_000_000:
                raise ValueError("backup exceeds restore size limit")
            destination = target.joinpath(*path.parts[1:])
            if not destination.resolve().is_relative_to(target):
                raise ValueError("backup path escapes restore target")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            source = tar.extractfile(member)
            if source is None:
                raise ValueError("invalid backup file")
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with source, os.fdopen(descriptor, "wb") as output:
                shutil.copyfileobj(source, output)
            if digest(destination) != expected:
                raise ValueError("backup checksum mismatch")
        if remaining or "accounts.sqlite3" not in manifest["files"]:
            raise ValueError("backup is incomplete")
    with closing(sqlite3.connect(target / "accounts.sqlite3")) as db, db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 1 or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("restored database failed integrity check")
        db.execute("DELETE FROM logins")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backup", "restore"):
        command = commands.add_parser(name)
        command.add_argument("source", type=Path)
        command.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    (backup if args.command == "backup" else restore)(args.source, args.destination)
    print(f"{args.command} complete")


if __name__ == "__main__":
    main()

"""Encrypted, resumable SQLite snapshots on a dedicated markets-data branch."""

import argparse
from contextlib import closing
from datetime import date
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

from cryptography.fernet import Fernet
from root.calendar import week_sunday
from .session import git


BLOCK = 4 * 1024 * 1024


class Checkpoints:
    def __init__(self, checkout, database, branch):
        self.root = checkout.resolve()
        self.database = database
        self.branch = branch
        self.folder = self.root / "paper-trading" / "capture-backup"
        remote = git(self.root, "remote", "get-url", "--push", "--all", "origin").lower().removesuffix(".git")
        if remote != "git@github.com:davisai1974/markets-data":
            raise ValueError("capture backups require the markets-data deploy key")
        self.cipher = Fernet(os.environ["PAPER_CAPTURE_KEY"].encode())

    def initialize(self):
        ref = "refs/heads/" + self.branch
        result = subprocess.run(["git", "-C", self.root, "ls-remote", "--exit-code", "origin", ref],
                                stdout=subprocess.DEVNULL)
        if result.returncode == 0:
            git(self.root, "fetch", "--depth=1", "origin", ref)
            git(self.root, "switch", "-C", self.branch, "FETCH_HEAD")
        elif result.returncode == 2:
            git(self.root, "switch", "-c", self.branch)
        else:
            raise RuntimeError("cannot read the capture branch")
        self.folder.mkdir(parents=True, exist_ok=True)
        manifest = self.folder / "checkpoint.enc"
        if not manifest.exists():
            return
        blocks = json.loads(self.cipher.decrypt(manifest.read_bytes()))
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.database.open("xb") as output:
            for digest in blocks:
                if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("invalid checkpoint block name")
                data = gzip.decompress(self.cipher.decrypt((self.folder / digest).read_bytes()))
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("invalid checkpoint block")
                output.write(data)

    def publish(self):
        if not self.database.is_file():
            return
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "capture.sqlite"
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(snapshot)) as target:
                    source.backup(target)
            blocks = []
            with snapshot.open("rb") as data:
                while block := data.read(BLOCK):
                    digest = hashlib.sha256(block).hexdigest()
                    blocks.append(digest)
                    destination = self.folder / digest
                    if not destination.exists():
                        destination.write_bytes(self.cipher.encrypt(gzip.compress(block, compresslevel=1)))
        manifest = self.folder / "checkpoint.enc"
        if not manifest.exists() or json.loads(self.cipher.decrypt(manifest.read_bytes())) != blocks:
            manifest.write_bytes(self.cipher.encrypt(json.dumps(blocks).encode()))
        # Stage even an unchanged manifest. A prior attempt may have stopped
        # between writing the encrypted files, staging them and committing.
        git(self.root, "add", "--", self.folder.relative_to(self.root).as_posix())
        staged = git(self.root, "diff", "--cached", "--name-only")
        if staged:
            git(self.root, "-c", "user.name=NG capture", "-c", "user.email=paper@users.noreply.github.com",
                "commit", "-qm", "Checkpoint captured MBO deliveries")
        # A prior push may have failed after the local commit succeeded.
        git(self.root, "push", "origin", "HEAD:refs/heads/" + self.branch)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["restore", "publish", "follow"])
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--trade-day", type=date.fromisoformat, required=True)
    parser.add_argument("--source", choices=["purchased", "live"], required=True)
    args = parser.parse_args()
    week = week_sunday(args.trade_day).isoformat()
    db = args.data.resolve() / "paper-trading" / "captures" / "nymex-ng" / week / (args.source + ".sqlite")
    store = Checkpoints(args.checkout, db, f"mbo-captures/nymex-ng/{week}/{args.source}")
    if args.operation == "restore":
        store.initialize()
    elif args.operation == "publish":
        store.publish()
    else:
        while True:
            time.sleep(60)
            store.publish()


if __name__ == "__main__":
    main()

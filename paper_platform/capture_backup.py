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
        self.cipher = Fernet(os.environ["PAPER_CAPTURE_KEY"].encode())

    def initialize(self, *, existing=False):
        origin = git(self.root, "remote", "get-url", "origin").lower().removesuffix(".git")
        if origin not in ("https://github.com/davisai1974/markets-data", "git@github.com:davisai1974/markets-data"):
            raise ValueError("restore capture checkpoints from markets-data")
        ref = "refs/heads/" + self.branch
        result = subprocess.run(["git", "-C", self.root, "ls-remote", "--exit-code", "origin", ref],
                                stdout=subprocess.DEVNULL)
        if result.returncode == 0:
            git(self.root, "fetch", "--depth=1", "origin", ref)
            git(self.root, "switch", "-C", self.branch, "FETCH_HEAD")
        elif result.returncode == 2 and not existing:
            # A new capture branch starts with no parent. Built on the checkout's commit, its first push
            # would have to carry that whole commit once markets-data's branch had moved on, and the
            # checkout holds none of those files.
            git(self.root, "switch", "--orphan", self.branch)
        else:
            raise RuntimeError("cannot read the capture branch")
        self.folder.mkdir(parents=True, exist_ok=True)
        manifest = self.folder / "checkpoint.enc"
        if not manifest.exists():
            if existing:
                raise ValueError("missing recorded capture checkpoint")
            return
        checkpoint = json.loads(self.cipher.decrypt(manifest.read_bytes()))
        blocks = checkpoint if isinstance(checkpoint, list) else checkpoint["blocks"]
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.database.parent) as tmp:
            restored = Path(tmp) / "restored.sqlite"
            with restored.open("xb") as output:
                for digest in blocks:
                    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                        raise ValueError("invalid checkpoint block name")
                    data = gzip.decompress(self.cipher.decrypt((self.folder / digest).read_bytes()))
                    if hashlib.sha256(data).hexdigest() != digest:
                        raise ValueError("invalid checkpoint block")
                    output.write(data)
            # Publish only the complete database. Never replace a live recorder.
            os.link(restored, self.database)

    def publish(self, push_seconds=None):
        remote = git(self.root, "remote", "get-url", "--push", "--all", "origin").lower().removesuffix(".git")
        if remote != "git@github.com:davisai1974/markets-data":
            raise ValueError("capture backups require the markets-data deploy key")
        if not self.database.is_file():
            return
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "capture.sqlite"
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(snapshot)) as target:
                    source.backup(target)
                    if not target.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='frames'").fetchone():
                        return
                    last = target.execute("SELECT id,connection_id,received_ns,kind FROM frames ORDER BY id DESC LIMIT 1").fetchone()
                    last_mbo, = target.execute("SELECT MAX(position) FROM frames").fetchone()
            blocks = []
            with snapshot.open("rb") as data:
                while block := data.read(BLOCK):
                    digest = hashlib.sha256(block).hexdigest()
                    blocks.append(digest)
                    destination = self.folder / digest
                    if not destination.exists():
                        destination.write_bytes(self.cipher.encrypt(gzip.compress(block, compresslevel=1)))
        manifest = self.folder / "checkpoint.enc"
        checkpoint = dict(version=2, blocks=blocks, last_mbo_position=last_mbo,
                          last_delivery=dict(zip(("id", "connection_id", "received_ns", "kind"), last)) if last else None)
        if not manifest.exists() or json.loads(self.cipher.decrypt(manifest.read_bytes())) != checkpoint:
            manifest.write_bytes(self.cipher.encrypt(json.dumps(checkpoint).encode()))
        # Stage even an unchanged manifest. A prior attempt may have stopped
        # between writing the encrypted files, staging them and committing.
        git(self.root, "add", "--", self.folder.relative_to(self.root).as_posix())
        staged = git(self.root, "diff", "--cached", "--name-only")
        if staged:
            git(self.root, "-c", "user.name=NG capture", "-c", "user.email=paper@users.noreply.github.com",
                "commit", "-qm", "Checkpoint captured MBO deliveries")
        # A prior push may have failed after the local commit succeeded.
        subprocess.run(["git", "-C", str(self.root), "push", "origin", "HEAD:refs/heads/" + self.branch],
                       check=True, timeout=push_seconds)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["restore", "restore-recording", "publish", "follow"])
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--trade-day", type=date.fromisoformat, required=True)
    parser.add_argument("--source", choices=["purchased", "live"], required=True)
    args = parser.parse_args()
    week = week_sunday(args.trade_day).isoformat()
    db = args.data.resolve() / "paper-trading" / "captures" / "nymex-ng" / week / (args.source + ".sqlite")
    store = Checkpoints(args.checkout, db, f"mbo-captures/nymex-ng/{week}/{args.source}")
    if args.operation in ("restore", "restore-recording"):
        store.initialize(existing=args.operation == "restore-recording")
        if args.operation == "restore-recording":
            print(db)
    elif args.operation == "publish":
        store.publish()
    else:
        while True:
            time.sleep(60)
            # A push that hangs would leave the recorder running with no new checkpoint and no word of it.
            # Under the limit it ends this process, and the recorder is told the publication failed.
            store.publish(push_seconds=55)


if __name__ == "__main__":
    main()

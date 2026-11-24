"""Publish ordered prefixes of a paper run to its markets-data branch."""

from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import subprocess
import threading
import time


@dataclass(frozen=True)
class JournalCut:
    trigger: str
    sequence: int
    sizes: dict[str, int]


class GitJournal:
    def __init__(self, session, publication):
        self.session = session
        self.git = publication.command
        self.push_url = publication.push_url
        self.ref = "refs/heads/paper-runs/" + session.run
        self.path = session.folder.relative_to(session.data_root).as_posix()
        self.temporary = TemporaryDirectory(prefix="paper-git-")
        self.mirror = Path(self.temporary.name)
        publication.env["GIT_INDEX_FILE"] = str(self.mirror / "index")
        self.staged_sizes = {}
        self.commit = self.tree = self.pushed = None
        self.queue = Queue()
        self.progress = threading.Condition()
        self.required_sequence = self.published_sequence = -1
        self.failed_cut = self.error = None
        self.next_minute = time.monotonic() + 60
        self.worker = threading.Thread(target=self._work, name="markets-data-journal")

    def _enqueue(self, trigger):
        # Called with the session lock. Only immutable byte boundaries cross to
        # the Git worker. Git, file copying and network I/O happen outside it.
        s = self.session
        if not s.receives.closed:
            s.receives.flush()
        names = ("events.jsonl", "receive-ns.u64le", "start.json")
        if trigger == "final":
            names = tuple(path.name for path in s.folder.iterdir() if path.is_file())
        sizes = {name: (s.folder / name).stat().st_size for name in names}
        cut = JournalCut(trigger, s.events - 1, sizes)
        self.queue.put(cut)
        return cut.sequence

    def start(self):
        with self.session.lock:
            self.required_sequence = self._enqueue("start")
        self.worker.start()

    def on_event(self, event):
        if event["kind"] in ("paper order", "fill"):
            self.required_sequence = self._enqueue(event["kind"])

    def minute_if_due(self):
        # The watchdog calls this independently of both network destinations.
        now = time.monotonic()
        if now >= self.next_minute:
            with self.session.lock:
                if self.session.state == "active":
                    self._enqueue("minute")
            self.next_minute += (int((now - self.next_minute) // 60) + 1) * 60

    def wait_for_orders(self):
        # Backpressure is between ticks. A slow push cannot hold the session
        # lock or prevent the watchdog from cancelling and flattening.
        target = self.required_sequence
        with self.progress:
            self.progress.wait_for(lambda: self.error is not None or self.published_sequence >= target)
            if self.error is not None:
                raise RuntimeError("markets-data publication failed") from self.error

    def _stage_prefix(self, name, size):
        if self.staged_sizes.get(name) == size:
            return
        source = self.session.folder / name
        dest = self.mirror / ("blob-" + name)
        with source.open("rb") as reader, dest.open("ab") as writer:
            offset = writer.tell()
            if offset > size:
                raise RuntimeError("paper journal publication boundary moved backwards")
            reader.seek(offset)
            while offset < size:
                data = reader.read(min(1024 * 1024, size - offset))
                if not data:
                    raise RuntimeError("paper journal ended before its publication boundary")
                writer.write(data)
                offset += len(data)
        blob = self.git("hash-object", "-w", "--no-filters", str(dest), read_output=True)
        self.git("update-index", "--add", "--cacheinfo", f"100644,{blob},{self.path}/{name}")
        self.staged_sizes[name] = size

    def _push_commit(self):
        if self.tree is not None and self.pushed != self.commit:
            for attempt in range(3):
                try:
                    self.git("push", self.push_url, self.commit + ":" + self.ref)
                    self.pushed = self.commit
                    return
                except (RuntimeError, subprocess.TimeoutExpired):
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)

    def _publish(self, cut):
        # An ambiguous push may already have reached GitHub. Retry its exact
        # commit before staging any later prefix, always using a fast-forward.
        self._push_commit()
        if self.commit is None:
            # The run branch starts with no parent and holds only the run's own folder. The data
            # checkout holds no file contents outside its sparse folder, so a first commit built on its
            # commit makes git fetch every file of that commit, and the first push then carries them all.
            # The receipt records the data commit.
            self.git("read-tree", "--empty")
            self.commit = ""
        for name, size in cut.sizes.items():
            self._stage_prefix(name, size)
        tree = self.git("write-tree", read_output=True)
        if tree != self.tree:
            message = f"Paper {cut.trigger} through journal sequence {cut.sequence} [skip ci]"
            self.commit = self.git("-c", "user.name=NG Paper Recorder", "-c",
                                   "user.email=paper@users.noreply.github.com", "commit-tree",
                                   tree, *(("-p", self.commit) if self.commit else ()), "-m", message,
                                   read_output=True)
            self.tree = tree
        self._push_commit()
        with self.progress:
            self.published_sequence = cut.sequence
            self.progress.notify_all()

    def _work(self):
        cut = None
        try:
            while (cut := self.queue.get()) is not None:
                self._publish(cut)
        except Exception as error:
            with self.progress:
                self.failed_cut, self.error = cut, error
                self.progress.notify_all()
            self.session.stop("markets-data publication failed")

    def finish(self):
        # Session.stop() has closed the journal and written its receipt. Include
        # administrative orders and fills queued during shutdown before the receipt.
        try:
            try:
                with self.session.lock:
                    self._enqueue("final")
            finally:
                self.queue.put(None)
                self.worker.join()
            if self.error is not None:
                self._publish(self.failed_cut)
                while (cut := self.queue.get_nowait()) is not None:
                    self._publish(cut)
        finally:
            self.temporary.cleanup()

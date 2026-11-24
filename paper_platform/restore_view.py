"""Rebuild the Render read model from a completed, published markets-data journal."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import requests

from .api import wire


def batches(folder, receipt, size=128):
    batch = []
    events = 0
    with (folder / "events.jsonl").open(encoding="utf-8") as journal:
        for line in journal:
            event = json.loads(line)
            if (event["run"] != folder.name or event["market"] != receipt["market"]
                    or event["sequence"] != events):
                raise ValueError("journal sequence, market or run differs from its receipt")
            events += 1
            final = events == receipt["events"]
            state = dict(run=folder.name, market=receipt["market"], events=events,
                         status="active", code_commit=receipt["code_commit"])
            if final:
                if event["kind"] != "status" or event.get("reason") != receipt["status"]:
                    raise ValueError("journal is missing the terminal status from its receipt")
                state.update(status=receipt["status"], cursor=receipt["delivered"] - 1)
                if "remaining_paper_inventory" in receipt:
                    state["paper_inventory"] = receipt["remaining_paper_inventory"]
            batch.append(dict(event=wire(event), status=wire(state)))
            if len(batch) == size:
                yield batch
                batch = []
    if events != receipt["events"] or not events:
        raise ValueError("journal event count differs from its receipt")
    if batch:
        yield batch


def publish(http, url, batch):
    for attempt in range(4):
        try:
            response = http.post(url, json={"packets": batch}, timeout=(10, 90), allow_redirects=False)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 3:
                raise
        else:
            if response.status_code == 200:
                if response.json().get("next") != batch[-1]["event"]["sequence"] + 1:
                    raise ValueError("relay acknowledged a different journal sequence")
                return
            if response.status_code not in (429, 502, 503, 504) or attempt == 3:
                raise RuntimeError(f"relay refused the journal batch with HTTP {response.status_code}")
        time.sleep(2 ** attempt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{32}", args.run):
        parser.error("supply the paper run ID")
    folder = args.data / "paper-trading" / args.run
    receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
    with (folder / "events.jsonl").open("rb") as journal:
        if receipt["run"] != args.run or hashlib.file_digest(journal, "sha256").hexdigest() != receipt["files"]["events.jsonl"]:
            raise ValueError("journal does not match the published receipt")

    url = os.environ["PAPER_RELAY_URL"].rstrip("/")
    target = urlsplit(url)
    if target.scheme != "https" or not target.hostname or target.username or target.query or target.fragment:
        parser.error("configure the Render HTTPS origin")
    with requests.Session() as http:
        http.headers["Authorization"] = "Bearer " + os.environ["PAPER_RELAY_TOKEN"]
        for batch in batches(folder, receipt):
            publish(http, url + "/ingest/batch", batch)
    print(f"Restored {receipt['events']} journal events for {args.run}")


if __name__ == "__main__":
    main()

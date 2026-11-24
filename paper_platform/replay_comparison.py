"""Compare strategy decisions in two completed markets-data journals."""

import argparse
import hashlib
from itertools import zip_longest
import json
from pathlib import Path


def read_receipt(folder):
    receipt = json.loads((folder / "receipt.json").read_text(encoding="utf8"))
    if receipt["run"] != folder.name:
        raise ValueError("receipt run ID differs from its directory")
    return receipt


def decisions(folder, receipt):
    # An opening signal defines the identity used by later closes, observations
    # and managers. Its ordinal is stable across independently named sessions.
    openings = {}
    journal_hash = hashlib.sha256()
    count = 0
    last = None
    with (folder / "events.jsonl").open("rb") as journal:
        for raw in journal:
            journal_hash.update(raw)
            event = json.loads(raw)
            seq = event["sequence"]
            if event["run"] != receipt["run"] or seq != count:
                raise ValueError(f"{folder.name}: journal discontinuity at {count}")
            count += 1
            last = event
            if event["kind"] != "signal":
                continue
            if event["action"] == "open":
                key = event["trade_id"]
                if key in openings:
                    raise ValueError(f"{folder.name}: repeated trade opening at sequence {seq}")
                openings[key] = len(openings)
            signal = dict(event)
            for field in ("run", "recorded_ns", "sequence"):
                del signal[field]
            for field in ("trade_id", "source_trade_id"):
                if signal.get(field) is not None:
                    parent, _, manager = signal[field].partition("/")
                    if parent not in openings:
                        raise ValueError(f"{folder.name}: unknown opening trade at sequence {seq}")
                    signal[field] = (openings[parent], manager)
            yield seq, signal
    if (count != receipt["events"] or last is None or last["kind"] != "status"
            or last.get("reason") != receipt["status"]
            or journal_hash.hexdigest() != receipt["files"]["events.jsonl"]):
        raise ValueError(f"{folder.name}: journal does not match its final receipt")


def compare(live_folder, replay_folder):
    live, replay = read_receipt(live_folder), read_receipt(replay_folder)
    source, replay_source = live["inputs"][0], replay["inputs"][0]
    if (source.get("mode") != "follow recorder" or replay_source.get("mode") != "stored replay"
            or replay_source.get("original_run") != live["run"] or replay["run"] == live["run"]
            or source["capture"] != replay_source["capture"]):
        raise ValueError("select the original live run and its receipt-based replay")
    for field in ("market", "trade_day", "code_commit", "rule_files", "execution_plan_sha256", "order_book"):
        if live[field] != replay[field]:
            raise ValueError(f"receipts differ in {field}")

    # Consume both journals even after a difference so a truncated journal can
    # never produce a decision comparison reported as complete.
    first_difference = None
    live_count = replay_count = 0
    pairs = zip_longest(decisions(live_folder, live), decisions(replay_folder, replay))
    for ordinal, (original, reproduced) in enumerate(pairs, start=1):
        live_count += original is not None
        replay_count += reproduced is not None
        if first_difference is not None:
            continue
        if original is not None and reproduced is not None and original[1] == reproduced[1]:
            continue
        fields = []
        locations = {}
        for name, entry in (("live", original), ("replay", reproduced)):
            if entry is not None:
                seq, signal = entry
                locations[name] = dict(sequence=seq, rule=signal["rule"], action=signal["action"],
                                       reference=signal.get("reference"))
        if original is not None and reproduced is not None:
            left, right = original[1], reproduced[1]
            fields = sorted(key for key in left.keys() | right.keys()
                            if key not in left or key not in right or left[key] != right[key])
        first_difference = dict(decision=ordinal, locations=locations, differing_fields=fields)

    window_differences = [
        key for key in ("delivered", "first_live_decision_cursor", "last_source", "status", "capture_end")
        if live[key] != replay[key]
    ]
    if live.get("timers_after_last_input_ns") != replay.get("timers_after_last_input_ns"):
        window_differences.append("terminal strategy timers")
    if live["files"]["receive-ns.u64le"] != replay["files"]["receive-ns.u64le"]:
        window_differences.append("recorded receive times")
    live_frames = live.get("capture_frame_hashes_sha256")
    replay_frames = replay.get("capture_frame_hashes_sha256")
    if live_frames != replay_frames:
        window_differences.append("ordered capture frames")
    return dict(live=live["run"], replay=replay["run"], window_differences=window_differences,
                frame_fingerprints_available=live_frames is not None and replay_frames is not None,
                eligible_live_input=live["first_live_decision_cursor"] is not None,
                live_decisions=live_count, replay_decisions=replay_count, first_difference=first_difference)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("live", type=Path)
    parser.add_argument("replay", type=Path)
    args = parser.parse_args()
    report = compare(args.live, args.replay)
    print(json.dumps(report, indent=2))
    if report["window_differences"] or report["first_difference"] is not None:
        raise SystemExit("The run windows or strategy decisions differ.")
    if not report["eligible_live_input"] or not report["live_decisions"]:
        raise SystemExit("The journals contain no eligible strategy decision to establish parity.")


if __name__ == "__main__":
    main()

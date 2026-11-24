"""Assemble a purchased NG execution plan from the two committed family decisions."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile

from .execution_policy import normalize_order_legs


PACKAGE = Path(__file__).resolve().parent
SOURCE = ("tasks/family-research-20261022/research-union-current/"
          "paper-trading-20261123/item17-20261124")
DEFINITION = "NG_FUT_definition_20231001.dbn.zst"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], encoding="utf-8")


def read_source(repo, commit, filename):
    return json.loads(git(repo, "show", f"{commit}:{SOURCE}/{filename}"))


def family_decisions(answers, family, catalogue):
    pending = {rule for rule in catalogue if rule.startswith(family)}
    policies = {}
    for answer in answers:
        rule = answer["rule"]
        if rule not in pending:
            raise ValueError(f"{family} answer contains a duplicate or unexpected rule: {rule}")
        pending.remove(rule)
        if answer["decision"] == "no decision":
            reason = answer.get("missing", "").strip()
            if not reason:
                raise ValueError(f"{rule} needs the reason its decision is open")
            print(f"{rule}: {reason}", file=sys.stderr)
            continue
        if answer["decision"] != "decided":
            raise ValueError(f"{rule} has an unknown decision status")
        policy = {field: answer[field] for field in ("contracts", "open", "close")}
        policy = normalize_order_legs(rule, policy)
        if catalogue[rule] == "manager":
            parents = answer["entry_rules"]
            if (not isinstance(parents, list) or not parents or len(parents) != len(set(parents))
                    or any(p not in catalogue or catalogue[p] == "manager" for p in parents)
                    or answer["on_parent_close"] not in ("exit", "hold")):
                raise ValueError(f"{rule} needs its parent entries and parent-close decision")
            policy.update(entry_rules=parents, on_parent_close=answer["on_parent_close"])
        policies[rule] = policy
    if pending:
        raise ValueError(f"{family} answer is missing {', '.join(sorted(pending))}")
    return policies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markets_data", type=Path, help="checkout of DavisAI1974/markets-data")
    parser.add_argument("revision", help="markets-data commit containing both family answers")
    args = parser.parse_args()
    commit = git(args.markets_data, "rev-parse", "--verify", "--end-of-options",
                 args.revision + "^{commit}").strip()
    catalogue = {}
    for path in sorted((PACKAGE / "rules").glob("*.json")):
        rule = json.loads(path.read_text(encoding="utf-8"))
        catalogue[rule["id"]] = rule["role"]
    policies = {}
    for family in ("CL", "CX"):
        answers = read_source(args.markets_data, commit, f"{family}-answer.json")
        policies.update(family_decisions(answers, family, catalogue))
    if not policies:
        parser.exit(message="All decisions remain open. No executable plan was created.\n")
    definitions = read_source(args.markets_data, commit, "ngx3-nautilus-definitions.json")
    instrument, = [definition for definition in definitions[DEFINITION]
                   if definition["id"] == "NGX3.GLBX"]
    plan = {"instrument": instrument, "databento_instrument_id": 150611, "rules": policies}
    destination = PACKAGE / "execution_plans" / f"ngx3-{commit}.json"
    content = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with NamedTemporaryFile(dir=destination.parent, delete=False) as staged:
        temp_path = Path(staged.name)
    try:
        temp_path.write_bytes(content)
        try:
            os.link(temp_path, destination)
        except FileExistsError:
            if destination.read_bytes() != content:
                raise ValueError(f"Refusing to replace existing plan {destination.name}") from None
    finally:
        temp_path.unlink()
    print(destination.name)


if __name__ == "__main__":
    main()

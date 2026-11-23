"""Follow each CX decision's actual print reference independently of paper orders."""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Response:
    rule: str
    parent: str
    q: dict
    side: str
    entry_rule: str
    action: str
    changed: bool = False
    exited: bool = False

    def details(self):
        return {
            "reference_price": self.q["price"],
            "q_reference": self.q["reference"],
            "source_trade_id": self.parent,
            "entry_rule": self.entry_rule,
            "side": self.side,
        }


class Responses:
    def __init__(self):
        self.pending = {}

    def start(self, key, rule, parent, q, side, entry_rule, action):
        self.pending[key] = Response(rule, parent, q, side, entry_rule, action, exited=action == "exit")

    def exit(self, key):
        current = self.pending.get(key)
        if current:
            if current.changed:
                del self.pending[key]
            else:
                self.pending[key] = replace(current, exited=True)

    def prints(self, keys, tick, event):
        if not tick["trade"]:
            return []
        events = []
        for key in keys:
            current = self.pending.get(key)
            if current is None:
                continue
            price, reference = tick["price"], current.q["price"]
            if current.changed:
                response = "after first change"
            elif price == reference:
                response = "equal"
            else:
                predicts_up = current.side == "buy"
                if current.action in ("exit", "suspend additions"):
                    predicts_up = not predicts_up
                response = "with" if (price > reference) == predicts_up else "against"
                if current.exited or current.action == "suspend additions":
                    del self.pending[key]
                else:
                    self.pending[key] = replace(current, changed=True)
            events.append(
                event(
                    current.rule,
                    "print observed",
                    "Print after the CX decision",
                    tick,
                    key,
                    print_price=price,
                    price_response=response,
                    **current.details(),
                )
            )
        return events

    def end(self, key, reason, tick, event):
        current = self.pending.pop(key, None)
        if current is None or current.changed:
            return []
        return [
            event(
                current.rule,
                "price response unresolved",
                reason,
                tick,
                key,
                price_response="unresolved",
                **current.details(),
            )
        ]

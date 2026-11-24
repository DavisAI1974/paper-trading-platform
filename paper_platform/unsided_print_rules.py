"""CL16 and CL17 use the best bid and offer observed at each trade print."""
from root.calcs.quote_runs import FACT_NAMES, STEP_BACK, QuoteRuns


class UnsidedPrintRules:
    def __init__(self):
        self.quotes = QuoteRuns()
        self.facts = []
        self.first_run = {}
        self.last_step = {"bid": None, "ask": None}
        self.entered_run = {}
        self.taken_level = {}
        self.after_gap = False

    def at(self, tick):
        self.facts = []
        if not tick["trade"]:
            return []
        if tick["bid"] is None or tick["ask"] is None:
            self.quotes = QuoteRuns()
            self.first_run.clear()
            self.entered_run.clear()
            self.taken_level.clear()
            self.last_step = {"bid": None, "ask": None}
            self.after_gap = True
            return []
        position = tick["position"]
        _, rows = self.quotes.tick(position, tick["received"], tick["bid"], tick["ask"])
        signals = []
        eligible = tick["aggressor"] is None and not tick["settlement"]
        for row in rows:
            fact = dict(zip(FACT_NAMES, row))
            quote = fact["quote"].split()[-1]
            direction = fact["direction"]
            start = fact["run_start_position"]
            self.facts.append((quote, direction, start, fact["fact"]))
            if fact["fact"] == STEP_BACK:
                continue
            self.last_step[quote] = position
            # The start of the first observed run may precede the tape window.
            first = self.first_run.setdefault(quote, start)
            if eligible and start != first and self.entered_run.get(quote) != start:
                self.entered_run[quote] = start
                signals.append(dict(rule="CL16", side="buy" if direction == "up" else "sell",
                                    identity=(quote, start), reason=f"unsided print showed {quote} step {direction}",
                                    conditions=dict(quote=quote, run=start)))
        if eligible and tick["bid"] != tick["ask"]:
            for quote, side in (("ask", "buy"), ("bid", "sell")):
                if tick["price"] != tick[quote]:
                    continue
                step = self.last_step[quote]
                if self.after_gap and step is None:
                    continue
                if quote in self.taken_level and self.taken_level[quote] == step:
                    continue
                self.taken_level[quote] = step
                signals.append(dict(rule="CL17", side=side, identity=(quote, step),
                                    reason=f"first unsided print at the {quote} since its last step",
                                    conditions=dict(quote=quote)))
        return signals

    def close(self, idea):
        tracked = idea["conditions"]
        away = "up" if idea["side"] == "buy" else "down"
        for quote, direction, start, kind in self.facts:
            if tracked["quote"] != quote:
                continue
            if idea["rule"] == "CL16":
                if start != tracked["run"]:
                    continue
                step_back = kind == STEP_BACK
            else:
                if kind == STEP_BACK:
                    continue
                step_back = direction != away
            if step_back:
                prior = "after a further step" if idea.get("stepped_further") else "before a further step"
                return f"first {quote} step back {prior}"
            idea["stepped_further"] = True
        return None

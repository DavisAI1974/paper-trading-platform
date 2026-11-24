"""Bind the agents' NG order policies to the contract delivered on the live MBO stream."""
from decimal import Decimal
import hashlib
from pathlib import Path
import time

from databento_dbn import InstrumentDefMsg, MBOMsg, Metadata, Schema, SecurityUpdateAction, SType
import zstandard

from .capture import CaptureFailure


class LiveContract:
    def __init__(self, path, dataset):
        self.path = path
        self.dataset = dataset
        self.instrument_id = None
        self.definitions = {}

    def observe(self, record):
        if isinstance(record, MBOMsg):
            if self.instrument_id is None:
                self.instrument_id = record.instrument_id
            elif record.instrument_id != self.instrument_id:
                raise CaptureFailure("NG contract changed within the captured week")
        elif isinstance(record, InstrumentDefMsg):
            if record.security_update_action == SecurityUpdateAction.DELETE:
                self.definitions.pop(record.instrument_id, None)
                if record.instrument_id == self.instrument_id:
                    raise CaptureFailure("exchange deleted the captured NG futures contract")
            else:
                self.definitions[record.instrument_id] = record
        # The file is written once the stream has given both the MBO instrument and its definition. It
        # does not wait on a count of "replay completed" messages, which the gateway decides.
        if self.instrument_id is not None and self.instrument_id in self.definitions and not self.path.exists():
            self.publish()

    def publish(self):
        record = self.definitions.get(self.instrument_id)
        if record is None:
            raise CaptureFailure("weekly replay did not supply a definition for the captured NG MBO instrument")
        # The combined stream has mixed-schema metadata. Keep the exchange record intact
        # and give the Nautilus loader a single-schema DBN header for this one definition.
        header = Metadata(dataset=self.dataset, start=record.ts_event, stype_in=SType.INSTRUMENT_ID,
                          stype_out=SType.INSTRUMENT_ID, schema=Schema.DEFINITION)
        pending = self.path.with_suffix(".tmp")
        # NautilusTrader's Databento loader opens every file as zstd (`Decoder::from_zstd_file`), so the
        # file is compressed. The Databento client reads either form.
        pending.write_bytes(zstandard.ZstdCompressor().compress(bytes(header) + bytes(record)))
        pending.replace(self.path)


def bind_live_plan(plan, source, path: Path):
    import databento as db
    from nautilus_trader.adapters.databento.loaders import DatabentoDataLoader
    from nautilus_trader.model.instruments import FuturesContract

    record, = db.DBNStore.from_file(path)
    instrument, = DatabentoDataLoader().from_dbn_file(path)
    if not isinstance(instrument, FuturesContract):
        raise ValueError("live NG execution requires an outright futures contract")
    definition = FuturesContract.to_dict(instrument)
    if (definition["underlying"] != "NG" or definition["exchange"] != "XNYM"
            or definition["raw_symbol"] != record.raw_symbol
            or not definition["activation_ns"] <= time.time_ns() < definition["expiration_ns"]):
        raise ValueError("captured definition is not an active NYMEX natural gas future")
    if Decimal(definition["maker_fee"]) or Decimal(definition["taker_fee"]):
        # No trade cost enters a paper fill. The purchased tape's definition carries zero fees.
        raise ValueError("the live contract definition carries a fee; a paper fill carries no trade cost")
    return dict(instrument=definition, databento_instrument_id=record.instrument_id, rules=plan["rules"],
                live_definition=dict(policy_source=source, raw_symbol=record.raw_symbol,
                                     ts_event=record.ts_event, sha256=hashlib.sha256(bytes(record)).hexdigest()))

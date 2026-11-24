# Raw readers of a 7e recording, for checking the runner by hand

These four small programs read the raw DXLink messages of a `sandbox-stream-*.jsonl` recording and print what is
in them. None imports the runner, ROOT or `crypto_ng`: they exist so that a ledger line can be checked against the
recording without using the engine's own facts. They send nothing and write nothing.

    python3 -I tasty/checks/raw_print_table.py RECORDING        every trade print, with a plain count of the
                                                                 size bought and sold in the last 20 and 60 seconds
    python3 -I tasty/checks/raw_in_arrival_order.py RECORDING FROM_MSG TO_MSG
                                                                 prints, quote and day-volume messages as they arrived
    python3 -I tasty/checks/raw_facts.py RECORDING              how many prints trade at their own bid or offer
    python3 -I tasty/checks/raw_sequence_steps.py RECORDING     the step of the sequence number from print to print,
                                                                 and the day volume beside the print sizes
    python3 -I tasty/checks/raw_no_side_prints.py RECORDING     what the prints with no side look like, and what
                                                                 they are the first to show
    python3 -I tasty/checks/fills_beside_the_market.py RUN_DIR LATER_FEED_DIR OUT.json
                                                                 each sandbox fill of a run beside the feed's own
                                                                 prints at the market time it filled

First used for step 0 of handoff V (markets-data `paper-trading-20261123/step0-20261124/STEP0_TRACE_20261124.md`).

# Agent execution plans

Frankie and the Ccode-managed agents can commit a JSON plan in this directory and select its filename in either manual paper workflow. The receipt records the plan, its fingerprint and the platform commit. Inline JSON remains available for a single authorized run.

Supply the contract's actual Nautilus futures instrument definition, its Databento instrument ID and a `rules` object. Each participating strategy specifies its contract quantity and its opening and closing instructions. Unselected rules continue to emit research signals.

| Instruction | Paper order |
| --- | --- |
| `post` | Post-only limit at the current same-side quote. |
| `cross` | Immediate-or-cancel market order against displayed depth. |
| `cross_limit` | Immediate-or-cancel limit at the current opposite quote. |

A leg can be an object with `style` and a nonnegative integer `offset_ticks`. For a post, the offset moves the price further from the other side. For a crossing limit, it sets how far beyond the opposite quote the order may take liquidity. A market order accepts no price offset. The matcher reports each execution separately.

A manager can list originating strategy IDs in `entry_rules`. Its direction selects matching entry signals. It submits its own opening order in its own venue, even when the originating strategy only records research signals. Set `on_parent_close` to `exit` to act when the parent closes, or `hold` to keep the manager's position and orders under its own instructions until its exit or the run stop. Each exit cancels outstanding orders for that trade and sizes its replacement to actual filled inventory. The run stop cancels working orders and attempts to flatten the remainder.

A strategy plan sets execution behavior. A paper run still requires Greg's explicit go.

## Assemble the purchased NGX3 plan

The [committed family-agent plan](ngx3-12be6ea6e40347def5f574b1c2f36093bba99200.json) covers CL01–CL17 and CX01–CX15 on the purchased NGX3 tape. The earlier file `ngx3-599eb8c0d48926292bbb4ef83fd518d5d2753daf.json` is superseded: it was built from 30 decisions, has no entry for CL16 or CL17, and its managers list 20 parents. It is kept because a plan is never edited or removed once written. Do not select it for a run: the matcher accepts it, and CL16 and CL17 would then write signals and place no order. The [CL decision agent's answers](https://github.com/DavisAI1974/markets-data/blob/12be6ea6e40347def5f574b1c2f36093bba99200/tasks/family-research-20261022/research-union-current/paper-trading-20261123/item17-20261124/CL-answer.json) and [CX decision agent's answers](https://github.com/DavisAI1974/markets-data/blob/12be6ea6e40347def5f574b1c2f36093bba99200/tasks/family-research-20261022/research-union-current/paper-trading-20261123/item17-20261124/CX-answer.json) attribute each choice and identify its source text. Each agent chose one contract per opening order. The plan includes parent entries and parent-close decisions for the six CX managers. Frankie's input remains pending.

For item 17, both family agents record one answer per strategy in `CL-answer.json` and `CX-answer.json` beside the [markets-data decision briefs](https://github.com/DavisAI1974/markets-data/tree/ccr-349e2ffe-07p8od/tasks/family-research-20261022/research-union-current/paper-trading-20261123/item17-20261124). Commit both answers to markets-data. From the platform repository root, build the plan from that commit.

```sh
python -m paper_platform.assemble_execution_plan ../markets-data <markets-data-commit>
```

The output is `paper_platform/execution_plans/ngx3-<full-markets-data-commit>.json`. The revision in its name identifies the original answers, their attributions and the October 1 NGX3 definition. The assembler reports explicitly undecided rules and their reasons. If all decisions remain open, it leaves the plan directory untouched. A new set of decisions gets a new filename. Select the committed filename in the manual workflow after Greg authorizes a paper session.

## A plan with the give-back exit

`ngx3-12be6ea6e40347def5f574b1c2f36093bba99200-giveback20.json` is the committed family-agent plan above with one entry added, `"exit": {"give_back_fraction": "0.2"}`, on Greg's word of November 24. Every rule's placement, size and manager entries are the agents', unchanged. With the entry, each paper trade is closed when its open profit falls back by that part of its highest, and a rule's own close sends no order. What that leaves open is written under item 17 of the checklist. The plan without the entry is still the one to select for a run of the rules' own closes.

`ngx3-12be6ea6e40347def5f574b1c2f36093bba99200-giveback20-both.json` is the same again with `"also_before_profit": true`, on Greg's word the same day: the 20% is also taken off the best open result of a trade that has never been in profit, so such a trade is closed too.

## The plan a loop's trial changes

`loop-trial.json` is the one file a trial of the keep-if-better loop may change (control file: DavisAI1974/Markets `tools/ratchet/programs/platform_exit.md`). On this branch it is a byte copy of `ngx3-12be6ea6e40347def5f574b1c2f36093bba99200-giveback20.json`, Greg's hold rule in force on November 24: hold until the trade gives back 20% of its highest open profit. A trial changes it only in the loop's own worktree, on the branch `ratchet/platform-exit`, one change a trial, and every trial is recorded with its commit; the copy on this branch is where every loop starts and is not edited by a trial. The committed `ngx3-*.json` plans are not touched by a loop. Running a trial puts the stored tape through the engine and needs Greg's explicit go.

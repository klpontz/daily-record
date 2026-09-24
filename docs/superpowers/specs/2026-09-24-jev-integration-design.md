# Daily Record — TypeSafe/Jev Integration

**Date:** 2026-09-24
**Status:** Design — revised after adversarial review (2026-09-24), pending final user review
**Author:** Kevin Rocci (with Claude)

## Motivation

Three places in the daily-record → career-coach pipeline currently do a classification job by asking a full generative Claude call to both judge *and* write prose, or do the classification entirely by hand (Kevin, reading logs):

1. **Asana filtering** (`asana_filter.md` prompt) asks Claude to decide which completed tasks/comments are "real work" vs. noise, *and* format the survivors as bullets — in one free-text call, over data that's already fully structured.
2. **The daily review's Breakdown percentages** (Product/QA vs. Ops-transferable vs. Ops-noise) are eyeballed by whoever writes the review, reading the whole day holistically. There's no per-item record of why an item landed in a bucket.
3. **Carry-forward tracking** ("this item has been open since Sep 16") is done by hand, rereading the last 7-10 logs' Review sections and matching reworded lines by eye. Wording drift ("Call Sam Rivera" vs. "Follow up with Jordan's email") makes this slow and easy to miss.

[TypeSafe's Jev](https://docs.typesafe.ai/introduction) returns typed judgments (a yes/no probability, a category, a score) instead of generated text. All three problems above are judgment calls over already-structured or already-written text — a better fit for a typed primitive than a free-text prompt-and-parse call.

## Revision history

**2026-09-24, revised after adversarial review.** An Opus 5.5 agent reviewed the first draft against the real codebase and, where it could, against the live TypeSafe API using a real key. It found one test that could not pass against its own implementation, a date bug and a data-loss bug in the carry-forward tracker, and a design problem where the Breakdown would count bullets instead of weighted work. It also flagged, as unverified, whether Jev reliably resolves an item referenced by array index inside a larger shared `state` blob (`items[17]` in a 30-item list) — the pattern every one of the plan's TDD tests mocked around instead of proving.

**That assumption was tested for real before revising this document** — see **Verification**, below. Every other finding is addressed in the sections it touches; changes are called out inline.

## Verification

Before locking in a request shape, two patterns were run live against `api.typesafe.ai` (not mocked), using `jev-latest`, with a constructed test where the correct answer depends on exact positional identity — not on which item merely *sounds* right:

- 20 items, each stating a number (`"Log entry: task reference number is {N}."`). For half, `N` matches the item's true array index; for the other half it's deliberately wrong. The question per item was *"does the number stated in `items[i]` equal i"* — a direct probe of whether the model resolves position `i` correctly, since guessing or answering "on vibes" would score far below 20/20 on ground truth built to have no other signal.
- **Index-path pattern** (`state = {"items": [...]}`, question text references `items[i]` by number): **20/20** correct.
- **Structured-instructions pattern** (each question's `instructions` is an object carrying that item's own text directly, no array lookup required): **20/20** correct.

Both work. **This design uses structured instructions anyway** — not because the index-path pattern failed, but because it's the pattern [TypeSafe's own build guide](https://docs.typesafe.ai/concepts/how-to-build-with-system-one) recommends for exactly this shape of problem ("part of the question comes from your code... put it in its own field instead of splicing it into a string template"), and because a question that carries its own data is trivially checkable in a unit test by asserting on the question object directly, instead of asserting on array-length bookkeeping that a future edit could silently misalign.

Every `Noul`/`Choice` question in this design therefore takes the shape:

```python
Noul(instructions={"item": item_text, "question": "..."})
```

with a minimal or empty shared `state` (the API requires *some* `state` value — `{}` is valid, `None` is not; confirmed live, an earlier draft of this doc assumed `None` worked and was wrong).

## Goals

- Replace the Asana filter's free-text judgment with a **Noul** (yes/no) call per item, and format survivors in code — no LLM needed for formatting since the data is already structured.
- Add a `classify_log.py` tool that runs a **Choice** (3-way) call per `### Done` bullet in a day's log, and prints a markdown breakdown with computed percentages and per-item confidence.
- Add a `carry_forward.py` tool that matches today's `### Tomorrow` checklist against a running list of previously-open items via **Choice**, tracking `first_seen`/`last_seen` per item in a small state file, and prints a days-open report with the match decisions shown, not hidden.

## Non-Goals

- **No change to the Slack summarizer.** Attributing a multi-person, multi-turn thread into coherent prose is genuine synthesis. Jev returns typed answers, not generated sentences — wrong tool for that job.
- **No automation of the "Honest take" narrative.** The reasoning, the connections across days, the direct advice — that's the coach's job (Claude, in conversation, with full context), not a classifier's.
- **No policy decisions baked into code.** `carry_forward.py` reports days-open per item; it does not decide what counts as "concerning." That threshold judgment stays in the narrative, same as it is today.
- **No intra-batch deduplication of identical Tomorrow lines.** If the same exact line appears twice in one day's `### Tomorrow` and neither matches an existing open item, both are tracked as separate new entries. Accepted as a rare, low-cost edge case rather than added complexity.
- **No fix to `Slack`/`Asana` sections' contribution to the Breakdown.** (Revised from the first draft — see Part 2.) Classifying those too would double- and triple-count the same underlying work (a debugging thread often produces one `### Done` bullet *and* a `### Slack` bullet describing the same exchange), and Slack bullets are frequently about *other people's* activity, not Kevin's. The Breakdown classifies `### Done` only.

---

## Part 1 — Asana Jev Filter (`daily-record`)

Replace `summarize(..., "asana_filter.md", ...)` with a new module `asana_filter.py`:

- `filter_items(api_key, tasks, comments) -> tuple[list[dict], list[dict]]` — one Noul question per task and per comment, all in a single TypeSafe API call. Short-circuits to `([], [])` with **no API call** when both lists are empty.
  - Each question uses the structured-instructions shape confirmed above: `Noul(instructions={"item": {...}, "question": "..."})`.
  - **Revised instruction wording** (dropped the "not a duplicate entry" clause the review flagged): a Noul judges one item at a time and cannot compare it against a sibling, so it can never actually detect a duplicate — asking it to try just adds noise to a single yes/no judgment that should be about one thing. *"Is this substantive work worth surfacing in a career log — not a trivially small task or routine admin note?"* Deduplication, if it's ever needed, is a deterministic code-side check (exact name match) — not built in this pass, since it wasn't a real problem in the sample logs reviewed.
  - Threshold: keep items where `noul > 0.5` (module-level constant `NOISE_THRESHOLD = 0.5`, tunable).
  - **New: visibility into what's dropped.** Every filtered-out item is printed to stdout with its score (`"dropped (noul=0.31): Replied to email"`), so a day where everything happens to score low doesn't look identical to a day with no Asana activity at all — the review flagged that `_No notable Asana activity today_` currently means both.
  - **New: explicit request timeout.** `TypeSafeClient(api_key=..., model="jev-latest", timeout=60.0)` — the SDK's default is 10 seconds, and TypeSafe's own cookbook for batches of parallel questions uses 120 seconds. A day with a dozen Asana items comfortably exceeds a 10-second default.
- `format_filtered(tasks, comments) -> str` — pure Python, no LLM. Tasks format as `- **{project}** — {name}: marked complete.`; comments format as `- **{project}** — {task}: {comment}`, with `comment` whitespace-collapsed and truncated to 200 characters first — the review found that raw Asana story text can span multiple paragraphs or contain its own `- ` list, which would corrupt the log's bullet structure and confuse `classify_log.py`'s section parser. Falls back to `_No notable Asana activity today_` when both filtered lists are empty (and to `_No notable Asana activity today_ (N items filtered as noise)` when the input wasn't empty but everything was filtered out — closing the visibility gap above).

**Design call flagged for review (unchanged from the first draft, still open):** this drops the paraphrased "action description" the old prompt invented (e.g. "published help article" for a task literally named "Student Kiosk Feature"). Comments keep their real text and read fine as-is; task bullets become more literal ("marked complete" rather than an inferred verb phrase). **Decide this before Task 2, not after seeing output** — the review specifically flagged that the first draft treated this as a look-at-it-after-building check, which is backwards for a decision that changes what Task 2 and Task 3 actually build. Two options:
  1. **(Default, as specced.)** Pure code formatting, no second LLM call, as above.
  2. A second, much smaller Claude call that *only* formats the Jev-filtered survivors (no filtering judgment left in that prompt) — closer to the old output's readability, at the cost of a second API dependency for a part of the pipeline that usually has 0-3 surviving items.

`pull_daily.py`'s Asana branch calls `filter_items` then `format_filtered` instead of `summarize`; on exception it writes `_Filtering failed — run manually_` (parallel to the existing `_Summarization failed — run manually_` marker for Slack). `prompts/asana_filter.md` is deleted — it's dead once this ships, and a stale prompt file next to a live one invites confusion about which is real.

## Part 2 — Breakdown Classifier (`daily-record`, reads `career-coach/logs/*.md`)

**Revised scope:** classifies `### Done` bullets only (the first draft classified Done + Slack + Asana together). The review's reasoning holds: the same underlying work often produces a `### Done` bullet *and* a `### Slack` bullet, so classifying both double-counts it; Slack bullets are frequently about someone else's activity, not Kevin's; and once Part 1 strips Asana's paraphrased verb ("marked complete." instead of "published help article"), there's too little signal left in an Asana line for a category judgment to mean anything. `### Done` entries are already synthesized, one-bullet-per-accomplishment prose — the cleanest ground truth available in the log.

New file `classify_log.py`:

- `read_section(text, section_name) -> str` (added to `log_writer.py`) — extracts a section's raw content by name. **Rewritten from the first draft's regex**, which reused `overwrite_section`'s lazy-match-plus-lookahead pattern; the review confirmed that pattern mis-captures when a section has literally zero characters before the next header (it backtracks past the empty section and swallows the *next* section's content instead). The new implementation locates every `### ` header by position first, then slices strictly between two consecutive header positions — there's no backtracking case left for it to get wrong.
- `extract_bullets(log_text) -> list[str]` — pulls every non-empty `- ` line from `### Done` only.
- `classify_items(api_key, items) -> list[dict]` — one **Choice** question per item (structured-instructions shape), single call, `timeout=60.0`. 3 options per question:

  | Option | Criteria text |
  |---|---|
  | `product_qa` | Technical or product work: debugging with a named root cause, a shipped fix, a spec or design doc, documented UX/product feedback, a SQL/data finding, AI-prototyping or eval work, a decision made directly with product leadership, or a portfolio-worthy technical finding. |
  | `ops_transferable` | Operations or account work that is formally documented, process-improving, or transferable: coaching or managing a report with a written structure, a documented policy/process decision, an account resolution with stated reasoning, delegation with tracked follow-through. |
  | `ops_noise` | Routine account administration, inbox/channel monitoring, task routing with no decision attached, or a brief approval with no stated reasoning. |

  Returns `[{"text": ..., "category": ..., "confidence": ...}, ...]`.

  > **Flagged for review:** this criteria wording is my synthesis of the pattern across ~15 days of your prior reviews. Every future day's percentage depends on this wording — read it closely before Task 7 locks it in.

- `format_breakdown(classified_items) -> str` — pure function. Groups by category **in a fixed order** (`product_qa`, `ops_transferable`, `ops_noise`) and **prints all three even at 0%** (the first draft silently dropped an empty category, which the review flagged as losing the expected three-line shape a human reader is used to scanning for). Computes percentages (`count / total`, rounded — approximate by design, hence the `~`; the review confirmed rounding can land on 99% or 101% across the three lines and judged the existing `~` prefix sufficient rather than adding largest-remainder logic for a personal tool). Header format is `- {label} (~{pct}%):` — **fixed from the first draft**, which the review caught producing `Ops (transferable) (~25%):` instead of the log's actual convention, `Ops (transferable, ~25%):` (verified against `career-coach/logs/2026-09-22.md`). Per-item confidence under 0.6 gets a `(low confidence — check by hand)` suffix — **noted as a starting threshold to recalibrate against real output**, not a calibrated number; Task 9's manual run step is where that happens. The block opens with one line making the counting method explicit: `"(N Done entries, by count — not effort-weighted)"`, so the percentages are never mistaken for the same kind of judgment a hand-written review makes.
- CLI: `python3 classify_log.py <path-to-log.md>` prints the markdown block to stdout. No file is written. Calls `load_dotenv()` on startup (the first draft assumed `TYPESAFE_API_KEY` was already exported; in practice it lives in `.env`, same as every other key this project uses). Wraps the classify call in a try/except — on failure, prints one clear line to stderr and exits 1, rather than a raw traceback.

## Part 3 — Carry-Forward Tracker (`daily-record`, reads/writes `career-coach/logs/`)

New file `carry_forward.py`, state file `<LOG_DIR>/.carry-forward.json` (git-tracked in career-coach):

```json
{
  "items": [
    {"id": "a1b2c3d4", "text": "Call Sam Rivera", "first_seen": "2026-09-16", "last_seen": "2026-09-23", "resolved": false, "resolved_date": null}
  ]
}
```

**Three fixes to real bugs the review found, all load-bearing:**

1. **The date comes from the log's filename, not the system clock.** The first draft used `date.today()` inside `main()`, which the review's own manual-test instructions would have silently gotten wrong (running the tool a day after the log it's reading was written dates everything one day off). `today` is now `date.fromisoformat(Path(log_path).stem)`. The CLI additionally refuses to run — prints an error, exits 1 — if the log's date is earlier than the newest `last_seen` already in the state file, since processing an old log after a newer one has already updated state would corrupt `first_seen`/`resolved_date` for no benefit.
2. **An empty or unfilled `### Tomorrow` no longer resolves every open item.** The first draft's `update_state` marked anything not matched today as resolved — including the case where `today_items` is empty because the log hasn't been filled in yet, which would silently wipe every tracked item's history and re-add them all tomorrow as "new," resetting every day-count to zero. The resolution sweep now only runs when `today_items` is non-empty. (The review also caught that the test meant to cover this — `test_main_no_tomorrow_items_reports_existing_open_items` — only asserted `exit_code == 0` and never checked the state, so it passed while the bug shipped. The rewritten test in Task 13 asserts the actual state.)
3. **Recurring/standing items are excluded from tracking, not just reported without judgment.** The review pointed out that real `### Tomorrow` lists include recurring entries ("Monitor hiring inbox and #training channel," "Sales and Ops meeting") that would otherwise accumulate an "open 40 days" count with no way to distinguish them from a genuinely stuck item — making the report's top lines noise every day. Before a *new* item is added to the state file, one extra Noul asks *"is this a recurring or standing task, or a scheduled meeting, rather than a one-off carry-forward item?"* — items scoring `noul > 0.5` are logged (`"skipped as recurring: Sales and Ops meeting"`) and never tracked. This costs one Noul per genuinely-new item only, not per already-tracked item, so it doesn't grow with state size.

Functions:

- `load_state(path) -> dict` / `save_state(path, state) -> None` — missing file loads as `{"items": []}`.
- `extract_tomorrow_items(log_text) -> list[str]` — unchecked `- [ ]` lines under `### Tomorrow` (reuses `read_section`), skipping blank checkboxes.
- `filter_recurring(api_key, candidate_items) -> tuple[list[str], list[str]]` — new (fix 3, above). Returns `(non_recurring, recurring)`; only called with items that didn't match anything existing, since recurring-ness only needs judging once per new item, not every day.
- `match_items(api_key, today_items, open_items) -> dict[str, dict]` — one **Choice** per today item (structured-instructions shape), single call, `timeout=60.0`. Options = every currently-unresolved `open_items` entry (`{id: text}`) plus a fixed `"new"` option. **Revised return shape** — the first draft returned just the matched id, which the review noted meant a wrong match could silently merge two different tasks with no way to catch it. Now returns `{today_text: {"matched_id": id_or_None, "confidence": float}}`. A match with `confidence < 0.5` is treated as `matched_id: None` (i.e. "new") rather than trusted — low-confidence guesses shouldn't silently merge history.
- `update_state(state, today, today_items, matches, non_recurring_new_texts) -> dict` — pure function (and now actually pure: the first draft mutated the input state's item dicts in place despite being documented as pure; the rewrite copies each item dict before modifying it). For each `today_items` entry: if matched (post confidence-floor), bump `last_seen`. If unmatched *and* in `non_recurring_new_texts`, add a new entry. If unmatched and *not* in `non_recurring_new_texts` (i.e. it was filtered as recurring), skip it entirely — never tracked. Resolution sweep (marking untouched open items resolved) only runs when `today_items` is non-empty, per fix 2.
- `format_carry_report(state, today, match_log) -> str` — every currently-unresolved item, sorted oldest-`first_seen`-first, as `"{text} — open N day(s) (since {first_seen})"` (singular/plural fixed — the first draft always printed "days," including "1 days"). Prints the match decisions first (`"matched: '{today text}' -> '{tracked text}' (confidence {c})"`) so a wrong merge is visible in the same output, not hidden — closing the review's visibility gap.
- CLI: `python3 carry_forward.py <path-to-log.md> [--state PATH]`. `--state` defaults to `<LOG_DIR>/.carry-forward.json`, `LOG_DIR` read from `.env` via `load_dotenv()` — the first draft required `--state` unconditionally, which the review noted contradicted this doc's own "next to LOG_DIR" description. Wraps the whole run in try/except — clear one-line error, exit 1, on any failure (network, missing key, bad log path).

## Shared Setup

- New dependency: `typesafe-sdk` (confirmed via `pip install --dry-run` during review: pulls in `httpx2`, `httpcore2`, `tenacity`, `truststore`, `idna`; no conflict with the existing `anthropic==0.49.0` or `pydantic>=2.12`, which is already satisfied).
- New required config key: `TYPESAFE_API_KEY` (added to `.env.example`, `REQUIRED_KEYS` in `pull_daily.py`, `load_config()`'s returned dict as `typesafe_api_key`, **and actually added to the real `.env` file** — the first draft required the key without ever adding it anywhere, which the review noted would break `pull-daily` immediately once Task 1 lands, until someone notices and fixes it by hand).
- Every `TypeSafeClient(...)` construction across all three modules passes `timeout=60.0` explicitly.
- `classify_log.py` and `carry_forward.py` both call `load_dotenv()` at the start of `main()` and wrap their API-calling logic in a top-level try/except that prints one clear line and exits 1 on failure — neither CLI relied on this in the first draft, which the review confirmed would surface as a raw traceback.

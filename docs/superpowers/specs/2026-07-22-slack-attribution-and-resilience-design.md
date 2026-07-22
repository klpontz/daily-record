# Daily Record — Slack Attribution + Network Resilience

**Date:** 2026-07-22
**Status:** Design — approved, pending spec review
**Author:** Kevin Rocci (with Claude)

## Motivation

Two independent problems in the daily pull:

1. **Fragility.** `pull-daily` failed three times in a row during a transient network window (Slack `IncompleteRead`, Asana `SSL: UNEXPECTED_EOF_WHILE_READING`, a read timeout). The HTTP calls have no retries and no timeouts, so a one-second blip becomes a total failure with nothing written.
2. **Coworker context is discarded.** The Slack fetcher only keeps the user's own words — thread replies are filtered to `user == user_id`, and messages are never attributed to a named person (user IDs are never resolved to names). Kevin wants the Slack summary to record **who was involved and what they said** as the raw material for a future coworker knowledge graph.

## Goals

- Make the pull survive transient network drops (retry with backoff + explicit timeouts) on both Slack and Asana.
- Capture and attribute what coworkers said in threads/DMs the user participated in, and carry those names through into the Slack summary as attributed prose.

## Non-Goals

- No structured/graph-DB output this phase. Phase 1 is attributed **prose** — the readable Slack section with names and positions woven in. The graph is built from that later.
- No broadening of capture scope. Only threads/DMs the user already participates in — not all channel chatter.
- No Asana attribution. Asana gets the resilience fix only; who-commented-on-shared-tasks is out of scope this phase.
- No change to the partial-write behavior (already correct: `run()` writes whichever source succeeds; only both-failed skips).

---

## Part 1 — Network Resilience

### Asana (`asana_client.py`, uses `requests`)

- Introduce one module-level helper that returns a configured `requests.Session`:
  - Mount an `HTTPAdapter` on `https://` with
    `urllib3.util.retry.Retry(total=4, connect=4, read=4, status=4, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=frozenset(["GET"]), respect_retry_after_header=True, raise_on_status=False)`.
  - urllib3's connect/read retries cover the `IncompleteRead` / `SSLError` / timeout class; backoff is exponential (~1·2·4·8s).
- Add an explicit `timeout=(10, 30)` (connect, read) to **every** `session.get(...)` call. The missing timeout is why one attempt hung on a stalled read.
- Replace the bare `requests.get(...)` calls in `_fetch_completed_tasks`, `_get_my_gid`, and `_fetch_my_comments` with `session.get(...)`.

### Slack (`slack_client.py`, uses `slack_sdk`)

- Construct the `WebClient` with:
  - `timeout=30`
  - `retry_handlers=[ConnectionErrorRetryHandler(max_retry_count=4), RateLimitErrorRetryHandler(max_retry_count=3)]`
    (from `slack_sdk.http_retry.builtin_handlers`; backoff included).
- No other change to Slack request logic for resilience.

---

## Part 2 — Slack Attribution

### (a) New module: `user_directory.py`

A small, isolated ID→name resolver.

- **Interface:** `resolve(client, user_id) -> str` and `resolve_many(client, ids) -> dict[str, str]`, plus a `SlackUserDirectory` class holding the cache and the user's own ID.
- **Bulk load:** on first use, page through `users.list` and build `{user_id: name}`.
- **Disk cache:** `.cache/slack_users.json` (relative to the project root; add `.cache/` to `.gitignore`). Refresh the bulk list if the cache file is missing or older than **7 days**.
- **Lazy fallback:** for any ID not in the cache (new hire, bot, guest), call `users.info` once and add it to the in-memory + disk cache.
- **Name preference:** `profile.real_name` → `profile.display_name` → `name` → the raw ID (never crash on a missing field).
- **Self:** the configured `SLACK_USER_ID` renders as `"You"`.
- **Resilience:** uses the same retry-configured `WebClient`, so it inherits the Part 1 handlers.

### (b) Fetch changes (`slack_client.py`)

- **Participation gate unchanged.** Only threads/DMs where the user posted or replied qualify — identical to today.
- **Within a qualifying thread, capture every message**, not just the user's:
  - Result shape per thread:
    `{"channel_id": str, "parent": {"user_id": str, "text": str}, "replies": [{"user_id": str, "text": str}, ...], "is_mine": bool}`
  - `replies` preserves chronological order and includes coworkers' messages.
- **DMs capture both sides:** each DM result becomes
  `{"channel_id": str, "partner_id": str, "messages": [{"user_id": str, "text": str}, ...]}`
  — the partner's messages and the user's, in order, instead of user-only.

### (c) Formatter changes (`format_for_prompt`)

- Accept the user directory (or a resolved `{id: name}` map) in addition to the channel-name map.
- Emit attributed lines, parent then replies in order:

  ```
  ## Channel Threads
  [#training] Helen Wang: I don't see how syncing next-grade is useful for us.
    You: Agreed it's not a reference point on its own…
    Juan Chen: what about pre-enrolled specifically?

  ## Direct Messages
  [DM with Anand Puthumanappully]
    Anand Puthumanappully: Fresno wants 300+ sign-ins bulk-deleted
    You: let me confirm with the expanded-learning team first
  ```
- Removes the `YOU` / `CONTEXT` / `> YOU` prefixes in favor of real names; "You" for the user.

### (d) Prompt rewrite (`prompts/slack_summarize.md`)

- Rewrite so the summarizer **names each participant and what they said/decided/asked**, rather than collapsing to "a decision was made."
- Update the input-format description (attributed `Name: text` lines, no more `YOU:`/`CONTEXT:`).
- New example output demonstrating attributed prose, e.g.:
  `**#training** — Helen Wang questioned whether next-grade helps pre-enrolled students; you traced the mechanism, conceded it doesn't fix the year-mismatch, and redirected to district enter date. Juan Chen asked about summer-session handling.`
- Keep: drop pure acknowledgements/emoji, group by thread/topic, factual (no coaching), the no-activity sentinel.

---

## Data Flow (unchanged shape)

`fetch_today_slack` → richer attributed dict → `format_for_prompt` (resolves names via `user_directory`) → `summarize` (Haiku, attributed prompt) → `write_log`. The orchestration in `pull_daily.py:run()` is untouched except passing the directory into the formatter.

## Testing

- **`test_slack_client.py`** — update fixtures/assertions: fetch now returns coworkers' thread messages; formatter emits real names for parent, replies, and both DM sides.
- **`test_user_directory.py`** (new) — bulk load, cache hit, cache-miss `users.info` fallback, stale-cache refresh, own-id→"You", missing-field fallback chain, all against a fake client.
- **`test_asana_client.py`** — assert calls go through the retry session with a timeout (mock the session).
- Run the full suite (`pytest`) before completion; all green.

## Risks / Flags

- **Privacy:** summaries are committed to the `career-coach` git repo, so coworkers' paraphrased words enter version control. Acceptable given the repo is private; noted explicitly.
- **Tokens:** full threads enlarge the Haiku input. Output is capped at 1024 and summaries stay short. If a very large thread ever truncates, cap replies-per-thread in the fetcher rather than raising `max_tokens`.
- **`users.list` scope:** requires the `users:read` Slack scope. If absent, resolver falls back to raw IDs and prints a one-line scope hint (mirrors the existing DM `missing_scope` handling).

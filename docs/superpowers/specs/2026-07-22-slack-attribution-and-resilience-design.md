# Daily Record — Slack Attribution + Network Resilience

**Date:** 2026-07-22
**Status:** Design — hardened after adversarial review (2026-07-22), pending final user review
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

> **Revised after adversarial review (2026-07-22).** The naive "add urllib3 Retry / add ConnectionErrorRetryHandler" plan does NOT retry the observed failures, verified against the installed libs (urllib3 2.6.3, requests 2.32.3, slack-sdk 3.35.0):
> - **Slack:** `ConnectionErrorRetryHandler`'s default `error_types` are `URLError` / `ConnectionResetError` / `RemoteDisconnected`. `IncompleteRead` (an `HTTPException`) and `SSLEOFError` (an `SSLError`/`OSError`) match none of them, so `can_retry()` returns `False` and the call raises with **zero retries**.
> - **Asana:** `requests` streams the body (`preload_content=False`), so a truncated-body `IncompleteRead` is raised at `.json()` — *after* `session.get()` has returned and `urllib3.Retry` is done. It surfaces as `requests.exceptions.ChunkedEncodingError`, outside the adapter's Retry scope. The `HTTPAdapter`/`Retry` only guards connect + header exchange.
>
> The design below fixes both at the layer that actually sees the error.

### Shared retry constants

Both clients target the same transport-error class. Define once (module-level, e.g. in a small `retry_util.py` or inline per client — implementer's call):

- **Transport error types:** `http.client.IncompleteRead`, `ssl.SSLError` (covers `SSLEOFError`), `requests.exceptions.ChunkedEncodingError`, `requests.exceptions.ConnectionError`, `requests.exceptions.Timeout`.
- **Backoff:** exponential with jitter — ~1·2·4·8s, `max_retries=4`.

### Asana (`asana_client.py`, uses `requests`)

- Introduce one module-level `_session()` returning a configured `requests.Session`:
  - Mount an `HTTPAdapter` on `https://` with
    `urllib3.util.retry.Retry(total=4, connect=4, read=4, status=4, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=frozenset(["GET"]), respect_retry_after_header=True, raise_on_status=False)`.
  - **This covers connect failures, header-read failures, and 429/5xx — NOT the mid-body read.** (`total=4` is the real ceiling; the per-category counts are each bounded by it.)
- **App-level retry (this is the C2 fix):** wrap each `get + .json()` in a small retry helper that catches the transport error types above and retries with backoff. The body read must be *inside* the retried scope, because that is where `IncompleteRead`/`ChunkedEncodingError` is raised. Replace each bare `requests.get(...)` in `_fetch_completed_tasks`, `_get_my_gid`, `_fetch_my_comments` with `session.get(...)` **called through** this helper.
- Add an explicit `timeout=(10, 30)` (connect, read) to **every** `session.get(...)`. The missing timeout is why one attempt hung on a stalled read.
- **Note the N+1 cost:** `_fetch_my_comments` issues one `/stories` call per task; under a broad outage, 4 retries × backoff per call can stack into minutes. Acceptable, but keep `total=4` (not higher) and consider a per-story shorter budget.

### Slack (`slack_client.py`, uses `slack_sdk`)

- Construct the `WebClient` with:
  - `retry_handlers=[ConnectionErrorRetryHandler(max_retry_count=4, error_types=[URLError, ConnectionResetError, RemoteDisconnected, IncompleteRead, ssl.SSLError]), RateLimitErrorRetryHandler(max_retry_count=3)]`
    (handlers from `slack_sdk.http_retry.builtin_handlers`; error classes imported from `urllib.error`, `http.client`, `ssl`). **The explicit `error_types` is the C1 fix** — the SDK's retry loop *does* wrap the body read, so once the types match, `IncompleteRead`/`SSLEOFError` retry correctly.
  - Do **not** bother setting `timeout=30` — that is already the slack-sdk default (no-op).
- No other change to Slack request logic for resilience.

---

## Part 2 — Slack Attribution

### (a) New module: `user_directory.py`

A small, isolated ID→name resolver — one class, one caller (the formatter resolves a known set of IDs).

- **Interface:** `SlackUserDirectory(client, own_id, cache_path)` with `resolve_many(ids) -> dict[str, str]`. No standalone `resolve` module function (YAGNI — single caller resolves a batch).
- **Bulk load:** on first use, page through `users.list` and build `{user_id: name}`.
- **Disk cache:** `.cache/slack_users.json` (relative to the project root; add `.cache/` to `.gitignore`). Refresh the bulk list if the cache file is missing or older than **7 days**.
  - **Atomic write (M5 fix):** write to a temp file in the same dir + `os.replace` — never a partial write that bricks the next run. Guard the load with `try/except (json.JSONDecodeError, OSError)` → treat corrupt/unreadable as a cache miss and refresh.
- **Lazy fallback:** for any ID not in the cache (new hire, bot, guest), call `users.info` once and add it to the in-memory + disk cache.
- **Name preference:** `profile.real_name` → `profile.display_name` → `name` → the raw ID (never crash on a missing field).
- **Self:** the configured `SLACK_USER_ID` renders as `"You"`.
- **Graceful degrade (M4 fix):** the bulk-load and lazy-fallback paths **must catch `SlackApiError` internally** (especially `missing_scope` when `users:read` is absent) and fall back to returning raw IDs. The resolver must NOT let a Slack error propagate — otherwise it escapes `format_for_prompt`, sets `slack_content = None`, and silently drops the *entire* Slack section instead of degrading to IDs. On `missing_scope`, print the one-line scope hint (mirrors the existing DM handling).
- **Accepted limitation (M6):** a name that is stale-but-present in the cache (coworker renamed or departed) is not corrected until the 7-day refresh — the lazy fallback only fires for IDs *absent* from the cache. Acceptable for prose; documented, not fixed.
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
- **Ordering (H3 fix):** `conversations.history` returns messages **newest-first**; `conversations.replies` returns **oldest-first**. Reverse the `history`-sourced lists (DM `messages`, and the channel top-level scan) so attributed transcripts read chronologically. Thread `replies` from `conversations_replies` are already oldest-first — leave them. Without this, DM transcripts narrate backward and the summarizer misreads the conversation flow.

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

**Behavior tests for the retry fix (M7 — these are the tests that would have caught C1/C2; they are mandatory, not optional):**

- **Asana:** a fake transport that raises `ChunkedEncodingError` (and separately `ConnectionError`, `Timeout`) on the first N calls then succeeds — assert the fetch retried and ultimately returned data, and that after exhausting retries it raises. Raising at `.json()`/body time, not just at `get()`, to exercise the actual C2 path.
- **Slack:** a fake `WebClient`/transport that raises `IncompleteRead` then `SSLEOFError` — assert the configured handler retries (not "assert a handler is attached"). At minimum, a unit test asserting `ConnectionErrorRetryHandler.can_retry()` returns `True` for `IncompleteRead` and `SSLError` given our `error_types`, which directly pins the C1 fix.

**Attribution tests:**

- **`test_slack_client.py`** — **~7 existing tests are rewrites, not tweaks**: current assertions expect the old shape (`thread == ["my reply"]`, `is_mine`, keys `text`/`thread`) and will all fail against the new `parent`/`replies` shape. Rewrite them, plus: fetch now returns coworkers' thread messages; DM `messages` include both sides and are chronologically ordered (H3); formatter emits real names for parent, replies, and both DM sides.
- **`test_user_directory.py`** (new) — bulk load, cache hit, cache-miss `users.info` fallback, stale-cache refresh, own-id→"You", missing-field fallback chain, **`SlackApiError`/`missing_scope` → raw-ID degrade without raising (M4)**, corrupt cache file → treated as miss (M5), all against a fake client.

**Plumbing tests:**

- **`test_asana_client.py`** — existing tests patch `asana_client.requests.get`; the switch to `session.get` breaks that mock — **repoint to the session**. Assert every call passes a `timeout`.

- Run the full suite (`pytest`) before completion; all green.

## Risks / Flags

- **Privacy:** summaries are committed to the `career-coach` git repo, so coworkers' paraphrased words enter version control. Acceptable given the repo is private; noted explicitly.
- **Tokens:** full threads enlarge the Haiku input. Output is capped at 1024 and summaries stay short. If a very large thread ever truncates, cap replies-per-thread in the fetcher rather than raising `max_tokens`.
- **`users.list` scope:** requires the `users:read` Slack scope. If absent, resolver degrades to raw IDs and prints a one-line scope hint (see M4 handling above). **Pre-build check:** confirm the app has `users:read` at api.slack.com so we're not building against a permission wall.
- **Prompt hallucination:** the summarizer must attribute only names present in the input and pass through raw `Uxxxx` IDs verbatim when resolution fell back — the prompt (d) states this explicitly to avoid invented or misattributed names.
- **urllib3 pin (L10):** the design imports `urllib3.util.retry.Retry` directly, but `requirements.txt` pins only `requests==2.32.3` (urllib3 arrives transitively at 2.6.3). Pin `urllib3` explicitly to lock the `Retry` / `allowed_methods` semantics this design depends on.

## Adversarial review resolution (2026-07-22)

This spec was hardened after an adversarial review verified against the installed libraries. Blockers **C1** (Slack handler didn't retry `IncompleteRead`/`SSLEOFError`) and **C2** (Asana Retry didn't cover the mid-body read) are fixed in Part 1. **H3** (history ordering), **M4** (scope degrade), **M5** (atomic cache), **M6** (stale-name limitation), **M7** (retry-behavior tests), **L9** (single resolver interface), and **L10** (urllib3 pin) are folded in above. **L8** (redundant `timeout=30`) dropped. Partial-write behavior and the `replies[0]`-is-parent assumption were confirmed correct and left unchanged.

# Lammot — Project Status

Last updated: 2026-11-20
Verified against: `PR #27` (2026-11-20)
Maintained by: Project Manager / Architect

> Read this first for current state. Use [ARCHITECTURE.md](../ARCHITECTURE.md)
> for system design and [UI_SPEC.md](../UI_SPEC.md) for UI behavior. This file
> records only current work, known issues, recent decisions, and recent
> completions.

## Current focus

- Verify `PREFETCH-001`/`PREFETCH-002` in production after deploy: check
  `api_stream resolve_ms` and `audio_relay first_chunk_ms`.
- Search is now explicit-submit only: typing no longer calls `/api/search` or
  starts prefetch.
- Parallel prefetch mode (`SEARCH_PREFETCH_PARALLEL=1`) warms the top three
  results concurrently; watch bot checks, container memory, and tap latency.

## In progress

| Task | Owner | State | Next step | Updated |
|---|---|---|---|---|
| Search-prefetch latency and log-hygiene fix (`PREFETCH-001`, `PREFETCH-002`) | Developer (fix-prefetch worktree) | Follow-up implemented, tests green; pending deploy/verification | Deploy and repeat the production-timing check | 2026-11-20 |
| Parallel prefetch of top-3 search results (`SEARCH_PREFETCH_PARALLEL`) | Developer (parallel-prefetch worktree) | Implemented with red/green tests; flag defaults off | Enable the flag in Railway and measure `search_prefetch resolved`, `bot_check`, memory, and tap `resolve_ms` | 2026-11-20 |

## Known bugs

| ID | Severity | Symptom | Area | Workaround | Next step | Updated |
|---|---|---|---|---|---|---|
| PREFETCH-001 | High | After a search, waiting several seconds and tapping the top result can still take ~4–11 s to start audio. The final query’s prefetch can be blocked behind a stale in-flight job, and a tap can then wait for a cold resolve that competes with background extraction for slots. | Search prefetch / resolver | Use `SEARCH_PREFETCH_ENABLED=0` as an operational fallback if prefetch makes latency worse; otherwise retry | Add a settle window; give prefetch its own 1-slot sequential extraction path; add correlation/timing logs and regression tests; verify production `api_stream resolve_ms` and `audio_relay first_chunk_ms` | 2026-11-20 |
| PREFETCH-002 | Medium | Parallel yt-dlp extractions can truncate or rewrite the shared cookies file, producing `does not look like a Netscape format cookies file` and risking permanent cookie corruption. | Resolver / cookies | Re-upload cookies with `/cookies` if errors persist | Make extraction read-only for cookies; keep `/cookies` as the only atomic writer; add a test | 2026-11-20 |

## Known limitations / tech debt

| Item | Impact | Next step | Updated |
|---|---|---|---|
| `tests/js/player-time.test.js` is required by `AGENTS.md` but is not run by `.github/workflows/tests.yml` | Player-time regressions can pass CI | Add it to CI (alongside the pytest run that already invokes `tests/audio-format.test.js` via Node) | 2026-11-20 |
| `stream_cache` and `track_resolution` exist in SQLite but are unused | Schema contains dead tables and unused helper functions | Decide whether to remove them or wire up a persistent cache | 2026-11-20 |
| In-memory cache, rate limiter, and resolver state require a single process | Multiple replicas would split state, multiply yt-dlp load, and contend on SQLite | Keep one replica, or redesign shared state before scaling out | 2026-11-20 |
| Resolver depends on Deno and a fresh `yt-dlp` | YouTube changes can break resolution until dependencies are updated | Keep `yt-dlp[default]` and Deno current; watch the canary and `/health` | 2026-11-20 |
| Cold yt-dlp extraction is roughly 2.7–5.4 s in production | A cold track cannot reliably start in under one second; only a completed prefetch or cache hit can | Set expectations from measured p50/p90, not a fixed “instant” promise | 2026-11-20 |
| Parallel prefetch raises the extract/resolve cap to 4 (3 prefetch + 1 tap) | Four concurrent yt-dlp/Deno processes may increase container memory and bot-check risk | Watch memory and `bot_check`; set `SEARCH_PREFETCH_PARALLEL=0` to roll back | 2026-11-20 |

## Recent decisions

| Date | Decision | Why | Evidence |
|---|---|---|---|
| 2026-11-17 | Background search prefetch is enabled by default: top 3 results, queue size 12, one worker, stale searches dropped | Warm the resolver cache without delaying search responses or queueing a tap behind prefetch work | `app/prefetch.py`, `app/main.py`, PR #18–#19 |
| 2026-11-17 | iOS uses AAC; track metadata duration is authoritative; doubled media duration is ignored and a forced track end is used | Old iOS webviews cannot play WebM/Opus reliably and some report roughly doubled duration | `frontend/audio-format.js`, `frontend/player-time.js`, PR #20–#21 |
| Current | Audio is relayed through the server; audio files are not stored | googlevideo URLs only work from the resolving IP, and the server must not store media | `app/main.py` `/audio`, `ARCHITECTURE.md` section 1 |
| 2026-11-20 | Implementation work stays off `main` and follows the documented TDD cycle | Keep `main` deployable and prove every fix with a regression test | `AGENTS.md`, PR #22 |
| 2026-11-20 | Prefetch settle is interruptible and defaults to 1 s; a background prefetch does not wait for a per-key lock already held by a tap | Production logs showed the final track's prefetch 3.4 s behind a stale job while the tap cold-resolved for 4.3 s | `app/prefetch.py`, `app/resolver.py`, Railway logs 17:54 |
| 2026-11-20 | Search is explicit-submit only; typing does not call `/api/search` or start prefetch | Intermediate keystroke queries enqueued unnecessary prefetch work and could delay the final query | `frontend/search-query.js`, `frontend/app.js`, PR #27 |

## Recently completed

| Date | Change | Commit / PR |
|---|---|---|
| 2026-11-17 | Background prefetch of top search results | `66f6929` (#18) |
| 2026-11-17 | Prefetch survives pre-roll gates and drops stale searches | `e5ed6b2` (#19) |
| 2026-11-17 | iOS track duration from metadata is authoritative | `5f3f392` (#20) |
| 2026-11-17 | Ignore doubled iOS media duration and end tracks on time | `f2e5d4f` (#21) |
| 2026-11-20 | Multi-agent workflow and TDD rules in `AGENTS.md` | `1056314` (#22) |
| 2026-11-20 | Explicit-submit search: no search or prefetch while typing | PR #27 |

## Next up

- Fix `PREFETCH-001`: stale in-flight prefetch and extraction-slot contention.
- Fix `PREFETCH-002`: concurrent cookie writes during yt-dlp extraction.
- Reduce log noise: expected INFO must not appear as Railway `[err]`.
- Close the JS test coverage gap in CI: run `tests/js/player-time.test.js`.
- Decide whether to remove or wire up the unused SQLite cache tables.
- Keep docs and this status file current after behavior changes.

## How to update this file

1. Update it in the same PR as any behavior change, or at the end of a work
   session.
2. Always update `Last updated` and `Verified against`.
3. Add durable decisions and notable completions; do not log routine refactors.
4. When a bug is fixed, move it from **Known bugs** to **Recently completed**.
5. Keep the file short; archive or drop entries older than the last 10
   decisions/completions.
6. Never include secrets, tokens, cookies, Telegram ids, or personal data.
7. Do not duplicate architecture or UI details; link to the dedicated
   documents.

## References

- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [UI_SPEC.md](../UI_SPEC.md)
- [AGENTS.md](../AGENTS.md)
- [docs/bugfix-workflow.md](bugfix-workflow.md)
- [docs/eu-migration.md](eu-migration.md)

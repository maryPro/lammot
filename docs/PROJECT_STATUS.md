# Lammot — Project Status

Last updated: 2026-11-20
Verified against: `main @ 1056314`
Maintained by: Project Manager / Architect

> Read this first for current state. Use [ARCHITECTURE.md](../ARCHITECTURE.md)
> for system design and [UI_SPEC.md](../UI_SPEC.md) for UI behavior. This file
> records only current work, known issues, recent decisions, and recent
> completions.

## Current focus

- Documentation refresh: bring `ARCHITECTURE.md` (v1.2) and `UI_SPEC.md`
  (v1.1) in line with `main`, and start this status journal.
- After this work merges, replace this section with the next confirmed task or
  “None recorded”.

## In progress

| Task | Owner | State | Next step | Updated |
|---|---|---|---|---|
| Documentation refresh (`ARCHITECTURE.md` v1.2, `UI_SPEC.md` v1.1, `docs/PROJECT_STATUS.md`) | Developer (docs worktree) | In review pending merge | Merge, then replace Current focus | 2026-11-20 |

## Known bugs

| ID | Severity | Symptom | Area | Workaround | Next step | Updated |
|---|---|---|---|---|---|---|

None recorded as of this commit.

## Known limitations / tech debt

| Item | Impact | Next step | Updated |
|---|---|---|---|
| `tests/js/player-time.test.js` is required by `AGENTS.md` but is not run by `.github/workflows/tests.yml` | Player-time regressions can pass CI | Add it to CI (alongside the pytest run that already invokes `tests/audio-format.test.js` via Node) | 2026-11-20 |
| `stream_cache` and `track_resolution` exist in SQLite but are unused | Schema contains dead tables and unused helper functions | Decide whether to remove them or wire up a persistent cache | 2026-11-20 |
| In-memory cache, rate limiter, and resolver state require a single process | Multiple replicas would split state, multiply yt-dlp load, and contend on SQLite | Keep one replica, or redesign shared state before scaling out | 2026-11-20 |
| Resolver depends on Deno and a fresh `yt-dlp` | YouTube changes can break resolution until dependencies are updated | Keep `yt-dlp[default]` and Deno current; watch the canary and `/health` | 2026-11-20 |

## Recent decisions

| Date | Decision | Why | Evidence |
|---|---|---|---|
| 2026-11-17 | Background search prefetch is enabled by default: top 3 results, queue size 12, one worker, stale searches dropped | Warm the resolver cache without delaying search responses or queueing a tap behind prefetch work | `app/prefetch.py`, `app/main.py`, PR #18–#19 |
| 2026-11-17 | iOS uses AAC; track metadata duration is authoritative; doubled media duration is ignored and a forced track end is used | Old iOS webviews cannot play WebM/Opus reliably and some report roughly doubled duration | `frontend/audio-format.js`, `frontend/player-time.js`, PR #20–#21 |
| Current | Audio is relayed through the server; audio files are not stored | googlevideo URLs only work from the resolving IP, and the server must not store media | `app/main.py` `/audio`, `ARCHITECTURE.md` section 1 |
| 2026-11-20 | Implementation work stays off `main` and follows the documented TDD cycle | Keep `main` deployable and prove every fix with a regression test | `AGENTS.md`, PR #22 |

## Recently completed

| Date | Change | Commit / PR |
|---|---|---|
| 2026-11-17 | Background prefetch of top search results | `66f6929` (#18) |
| 2026-11-17 | Prefetch survives pre-roll gates and drops stale searches | `e5ed6b2` (#19) |
| 2026-11-17 | iOS track duration from metadata is authoritative | `5f3f392` (#20) |
| 2026-11-17 | Ignore doubled iOS media duration and end tracks on time | `f2e5d4f` (#21) |
| 2026-11-20 | Multi-agent workflow and TDD rules in `AGENTS.md` | `1056314` (#22) |

## Next up

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

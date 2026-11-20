# Lammot — Project Status

Last updated: 2026-11-20
Verified against: `cold-start` branch (pending PR)
Maintained by: Project Manager / Architect

> Read this first for current state. Use [ARCHITECTURE.md](../ARCHITECTURE.md)
> for system design and [UI_SPEC.md](../UI_SPEC.md) for UI behavior. This file
> records only current work, known issues, recent decisions, and recent
> completions.

## Current focus

- Cold-start reduction: taps race the combined `web + web_embedded` set with
  fallbacks from the start (gate-free muxed 18 plus audio-only 251/140). The
  preprocessed player cache is enabled on the persistent volume, so later
  tracks only solve the new n/sig challenge. A gated fast response no longer
  delays the fallback retry.
- Verify in production after deploy: check `api_stream resolve_ms` for
  `clients=parallel(web,web_embedded)` vs `parallel(web)`, and whether the
  player cache is being reused/pruned.
- Search is now explicit-submit only: typing no longer calls `/api/search` or
  starts prefetch.
- Parallel prefetch (`SEARCH_PREFETCH_PARALLEL=1`) is **off in production**:
  three concurrent prefetches delayed the first tapped result (6.6 s + gate
  instead of ~2-3 s). Sequential prefetch prioritizes the first result.

## In progress

| Task | Owner | State | Next step | Updated |
|---|---|---|---|---|
| Cold-start fast path (`cold-start`) | Developer (cold-start worktree) | Implemented, tests green; pending deploy/verification | Deploy and compare `resolve_ms`; watch player-cache reuse and prune logs | 2026-11-20 |
| Search-prefetch latency and log-hygiene fix (`PREFETCH-001`, `PREFETCH-002`) | Developer (fix-prefetch worktree) | Follow-up implemented, tests green; pending deploy/verification | Deploy and repeat the production-timing check | 2026-11-20 |

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
| First track after a player version change pays the full EJS analysis | The fast path is ~2 s for later tracks but can be ~3 s for the first one; a fresh container has no player cache | Keep the cache on the volume; prefetch warms it after deploys | 2026-11-20 |
| The fast pass may use muxed itag 18 when audio-only is gated | Playback starts immediately, but it relays a 360p MP4 with AAC instead of audio-only 251/140, using more bandwidth and a lower audio bitrate | Keep the `_MAX_AVAILABILITY_WAIT` trade-off; use a PO token provider if audio-only without a gate becomes available | 2026-11-20 |
| The preprocessed player cache is ~4.4 MB per player version | Without rotation it would fill the 434 MB volume | `_cleanup_player_cache()` keeps the newest 3 files; watch volume usage and `pruned` logs | 2026-11-20 |
| Parallel prefetch is off in production (`SEARCH_PREFETCH_PARALLEL=0`) | Enabling it makes three prefetches compete; the tapped first result can wait 6.6 s + gate instead of ~2-3 s | Keep it off; consider a staggered prefetch that starts the first result alone | 2026-11-20 |

## Recent decisions

| Date | Decision | Why | Evidence |
|---|---|---|---|
| 2026-11-17 | Background search prefetch is enabled by default: top 3 results, queue size 12, one worker, stale searches dropped | Warm the resolver cache without delaying search responses or queueing a tap behind prefetch work | `app/prefetch.py`, `app/main.py`, PR #18–#19 |
| 2026-11-17 | iOS uses AAC; track metadata duration is authoritative; doubled media duration is ignored and a forced track end is used | Old iOS webviews cannot play WebM/Opus reliably and some report roughly doubled duration | `frontend/audio-format.js`, `frontend/player-time.js`, PR #20–#21 |
| Current | Audio is relayed through the server; audio files are not stored | googlevideo URLs only work from the resolving IP, and the server must not store media | `app/main.py` `/audio`, `ARCHITECTURE.md` section 1 |
| 2026-11-20 | Implementation work stays off `main` and follows the documented TDD cycle | Keep `main` deployable and prove every fix with a regression test | `AGENTS.md`, PR #22 |
| 2026-11-20 | Prefetch settle is interruptible and defaults to 1 s; a background prefetch does not wait for a per-key lock already held by a tap | Production logs showed the final track's prefetch 3.4 s behind a stale job while the tap cold-resolved for 4.3 s | `app/prefetch.py`, `app/resolver.py`, Railway logs 17:54 |
| 2026-11-20 | Search is explicit-submit only; typing does not call `/api/search` or start prefetch | Intermediate keystroke queries enqueued unnecessary prefetch work and could delay the final query | `frontend/search-query.js`, `frontend/app.js`, PR #27 |
| 2026-11-20 | Top search results prefetch in parallel behind `SEARCH_PREFETCH_PARALLEL=1`: three workers, no settle, three prefetch slots plus one tap slot, audio-only client set first | Sequential warm-up took 14–22 s for three results and a 5 s tap waited for the first in-flight resolve | `app/prefetch.py`, `app/resolver.py`, PR #28 |
| 2026-11-20 | Taps/prefetch use `web + web_embedded` in one extraction and enable the preprocessed player cache; visionos is not usable from the Railway IP | Production diagnostics: visionos returns HTTP 400 with cookies and bot-check without; web_embedded works but re-analyzes the player every track. With the cache, tracks resolve in ~1.7–2.4 s | `app/resolver.py`, `tests/test_resolver.py` |
| 2026-11-20 | Taps race the combined fast set with fallbacks from the start; `_MAX_TAP_GATE_WAIT` raised to 5 s; parallel prefetch disabled in production | A gated fast pass made taps wait 4.6–5.1 s, and parallel prefetch pushed the first tapped track to 6.6 s + gate. The race returns ~2–2.9 s; 5 s covers observed 4–5 s gates | `app/resolver.py`, `tests/test_resolver.py`, Railway logs 23:10 |

## Recently completed

| Date | Change | Commit / PR |
|---|---|---|
| 2026-11-17 | Background prefetch of top search results | `66f6929` (#18) |
| 2026-11-17 | Prefetch survives pre-roll gates and drops stale searches | `e5ed6b2` (#19) |
| 2026-11-17 | iOS track duration from metadata is authoritative | `5f3f392` (#20) |
| 2026-11-17 | Ignore doubled iOS media duration and end tracks on time | `f2e5d4f` (#21) |
| 2026-11-20 | Multi-agent workflow and TDD rules in `AGENTS.md` | `1056314` (#22) |
| 2026-11-20 | Explicit-submit search: no search or prefetch while typing | PR #27 |
| 2026-11-20 | Parallel prefetch of top-3 search results behind a flag | PR #28 |
| 2026-11-20 | Combined `web + web_embedded` fast path and preprocessed player cache | `cold-start` branch (pending PR) |

## Next up

- Deploy the tap-race fix and confirm `api_stream resolve_ms` is ~2–3 s for
  `clients=parallel(web,web_embedded)` / `parallel(web)`; confirm the player
  cache is reused and pruned on the volume.
- Keep `SEARCH_PREFETCH_PARALLEL=0`; revisit with a staggered prefetch that
  starts the first result alone.
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

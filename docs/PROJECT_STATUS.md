# Lammot — Project Status

Last updated: 2026-11-21
Verified against: `artist` worktree (artist profile feature)
Maintained by: Project Manager / Architect

> Read this first for current state. Use [ARCHITECTURE.md](../ARCHITECTURE.md)
> for system design and [UI_SPEC.md](../UI_SPEC.md) for UI behavior. This file
> records only current work, known issues, recent decisions, and recent
> completions.

## Current focus

- Artist profile, discography and artist links: the Artists shelf in Songs,
  artist pages (Popular / Albums / Singles and EPs), album screens, and
  tappable artist names in track rows and the full-screen player.
- Production verification of recently merged playback work: trimming
  (#41/#42), favourite hearts (#44/#45), next/previous behavior (#46),
  Extended search (#38/#39), and the player loading state (#34).

## In progress

| Task | Owner | State | Next step | Updated |
|---|---|---|---|---|
| Artist profile, discography and links (`artist`) | Developer | Implemented, non-integration tests green; pending merge/manual verification | Verify the Artists shelf, artist and album screens, artist links, Back behavior and the `artist_id` migration in a real Telegram WebView; confirm the second open of an artist logs `cache=hit` | 2026-11-21 |
| Extended search production verification | PM/Developer | Merged in #38/#39; manual verification pending | Search `kirman lost` in production; confirm the 9:29 remix is playable, the `Video` badge is visible, video radio works, and long videos stay out of the queue; then close | 2026-11-21 |
| Cold-start fast path (`cold-start`) | Developer (cold-start worktree) | Implemented, tests green; pending deploy/verification | Deploy and compare `resolve_ms`; watch player-cache reuse and prune logs | 2026-11-20 |
| Search-prefetch latency and log-hygiene fix (`PREFETCH-001`, `PREFETCH-002`) | Developer (fix-prefetch worktree) | Follow-up implemented, tests green; pending deploy/verification | Deploy and repeat the production-timing check | 2026-11-20 |
| Player loading state: remove the dimmed intermediate Pause (`PLAYER-001`) | Developer (remove-pause worktree) | Merged in PR #34; manual verification pending | Verify in a real Telegram WebView; confirm spinner, no dimmed Pause, and Pause only after playback | 2026-11-21 |
| Playback trimming — non-destructive start/end (`edit-track`) | Developer (edit-track worktree) | Merged in #41/#42; manual verification pending | Verify trim sheet, preview, trimmed progress/seek, and shared-playlist isolation in a real Telegram WebView | 2026-11-21 |
| Full-player quick actions and borderless hearts (`fix-player-meta-actions`) | Developer | Implemented, tests green; pending merge/manual verification | Verify in a real Telegram WebView: hearts are accent outlines with no circle and fill when saved in search, Favorites, shared playlists, and the player; the close chevron is top-right, `♡` and `⋯` sit beside the title, and the transport row holds only prev/play/next | 2026-11-21 |

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
| Favorites and playlist rows saved before `artist_id` existed have no artist link | Their artist names stay plain text until the track is saved again; shared playlists expose `artist_id` only for rows that have it | Accept for now; new saves and all new search/radio/album tracks carry links | 2026-11-21 |
| Artist metadata is not prefetched and is cached in-process only | A cold artist/album page costs one ytmusicapi request; a restart empties the cache | Keep the 5 min / 15 min / 1 h TTLs; add prefetch only if production latency shows a need | 2026-11-21 |

## Recent decisions

| Date | Decision | Why | Evidence |
|---|---|---|---|
| 2026-11-21 | Search rows use the borderless `♡`/`♥` again instead of the filled `＋`/`✓`; artist links are underlined in every track list; and when a track has no duration metadata on the iOS AAC relay, the doubled AVFoundation media estimate is halved | The plus read as a cross and split the favourite control from every other list; artist links looked like plain text in Favorites and playlists; the artist Popular shelf can omit durations, which let the player show 7:07 for a 3:34 track. Halving is gated on the known Apple media stack plus the AAC relay, so other platforms keep the raw media duration | `frontend/app.js`, `frontend/player-time.js`, `frontend/audio-format.js`, `frontend/style.css`, `tests/js/player-time.test.js`, `tests/js/track-row-layout.test.js`, `tests/js/favorite-heart.test.js` |
| 2026-11-21 | Track rows use one vertically centred flex line (cover, title + artist text block, duration, actions). Search rows show a filled accent `+`/`✓`; other lists keep the borderless heart. The artist page drops the duplicate in-app header and header Play, relying on Telegram's BackButton, and backfills missing popular durations from the artist's top-songs shelf | The two-row grid and duplicate artist header broke alignment and repeated the name; the reference design expects one compact row and standard back navigation. The shelf backfill restores duration labels without failing the page when upstream omits them | `frontend/app.js`, `frontend/style.css`, `app/artist.py`, `tests/js/track-row-layout.test.js`, `tests/test_artist.py` |
| 2026-11-21 | Tracks gain an optional `artist_id` (YouTube Music browse id); it is persisted only in `favorites` and `playlist_tracks`, with an idempotent `ALTER TABLE` migration. Artist metadata comes only from ytmusicapi; no second provider or new dependency | Make artist names tappable without duplicating search, and keep old databases compatible | `app/artist.py`, `app/search.py`, `app/database.py`, `tests/test_artist.py`, `tests/test_database.py` |
| 2026-11-21 | Songs search issues `/api/search` and `/api/artists` in parallel with one abort controller; an artist failure hides only the shelf | The artist shelf must never slow down or break song results | `frontend/app.js`, `frontend/artist-view.js` |
| 2026-11-21 | Favorite hearts are borderless accent glyphs everywhere: the accent draws the outline and the accent fill marks the saved state. Favorites rows show the filled heart to unlike. In the full-screen player the close chevron stays in the top-right, `♡` and `⋯` sit together beside the track title, and the centred bottom row holds only Previous, Play/Pause, and Next | Keep the heart readable against wallpapers and cards without an extra circle, keep transport controls the loudest element in the player, and keep the close where the user expects it | `frontend/index.html`, `frontend/style.css`, `frontend/app.js`, `tests/js/favorite-heart.test.js`, `tests/js/full-player-actions.test.js` |
| 2026-11-21 | Playback trims are per-user metadata (`track_trims`), not media edits; playback starts at Start and advances at End, and shared playlists do not share trims | Let users cut trailing silence or pick a favourite part without downloading, copying, or re-encoding audio | `app/database.py`, `app/main.py`, `frontend/trim-range.js`, `frontend/app.js` |
| 2026-11-21 | Favorites use heart buttons: outlined `♡` adds, filled `♥` removes; the Favorites tab and search menus follow the same state | Make the add/remove favourite action explicit and consistent across screens | `frontend/app.js`, `frontend/style.css`, `tests/js/favorite-heart.test.js`, #44/#45 |
| 2026-11-21 | Next/previous stops the current track and warms the next one early | Avoid overlapping playback and reduce the gap before the next track | `frontend/app.js`, `tests/js/radio-skip.test.js`, #46 |
| 2026-11-21 | Songs stays the default search; Extended is opt-in video search with a 45–900 s duration window, and video radio is seeded by the selected video | Remixes and fan uploads are regular YouTube videos that `filter="songs"` never returns; duration is a reliable contract where title heuristics are not | `app/search.py`, `app/radio.py`, `frontend/search-tabs.js` |
| 2026-11-21 | Playback icon is driven by actual `playing` state, with a spinner during load | The `play` event fires before audio is audible; an explicit state avoids the dimmed Pause stage | `frontend/playback-view.js`, `frontend/app.js`, PR #34 |
| 2026-11-17 | Background search prefetch is enabled by default: top 3 results, queue size 12, one worker, stale searches dropped | Warm the resolver cache without delaying search responses or queueing a tap behind prefetch work | `app/prefetch.py`, `app/main.py`, PR #18–#19 |
| 2026-11-17 | iOS uses AAC; track metadata duration is authoritative; doubled media duration is ignored and a forced track end is used | Old iOS webviews cannot play WebM/Opus reliably and some report roughly doubled duration | `frontend/audio-format.js`, `frontend/player-time.js`, PR #20–#21 |
| Current | Audio is relayed through the server; audio files are not stored | googlevideo URLs only work from the resolving IP, and the server must not store media | `app/main.py` `/audio`, `ARCHITECTURE.md` section 1 |
| 2026-11-20 | Implementation work stays off `main` and follows the documented TDD cycle | Keep `main` deployable and prove every fix with a regression test | `AGENTS.md`, PR #22 |
| 2026-11-20 | Prefetch settle is interruptible and defaults to 1 s; a background prefetch does not wait for a per-key lock already held by a tap | Production logs showed the final track's prefetch 3.4 s behind a stale job while the tap cold-resolved for 4.3 s | `app/prefetch.py`, `app/resolver.py`, Railway logs 17:54 |
| 2026-11-20 | Search is explicit-submit only; typing does not call `/api/search` or start prefetch | Intermediate keystroke queries enqueued unnecessary prefetch work and could delay the final query | `frontend/search-query.js`, `frontend/app.js`, PR #27 |
| 2026-11-20 | Top search results prefetch in parallel behind `SEARCH_PREFETCH_PARALLEL=1`: three workers, no settle, three prefetch slots plus one tap slot, audio-only client set first | Sequential warm-up took 14–22 s for three results and a 5 s tap waited for the first in-flight resolve | `app/prefetch.py`, `app/resolver.py`, PR #28 |
| 2026-11-20 | Taps/prefetch use `web + web_embedded` in one extraction and enable the preprocessed player cache; visionos is not usable from the Railway IP | Production diagnostics: visionos returns HTTP 400 with cookies and bot-check without; web_embedded works but re-analyzes the player every track. With the cache, tracks resolve in ~1.7–2.4 s | `app/resolver.py`, `tests/test_resolver.py` |
| 2026-11-20 | Taps race the combined fast set with fallbacks from the start; `_MAX_TAP_GATE_WAIT` raised to 5 s; parallel prefetch disabled in production | A gated fast pass made taps wait 4.6–5.1 s, and parallel prefetch pushed the first tapped track to 6.6 s + gate. The race returns ~2–2.9 s; 5 s covers observed 4–5 s gates | `app/resolver.py`, `tests/test_resolver.py`, Railway logs 23:10 |
| 2026-11-20 | iOS lock screen uses previous/next, and the system timeline is fed by `setPositionState` with the metadata duration | iOS ignores action handlers registered before playback starts and otherwise uses its doubled media duration; `seekbackward`/`seekforward` would replace prev/next | `frontend/app.js`, `frontend/player-time.js`, `tests/js/player-time.test.js` |

## Recently completed

| Date | Change | Commit / PR |
|---|---|---|
| 2026-11-21 | Player duration, artist-link underline, and search hearts (`fix-2-artist` worktree) | (pending PR) |
| 2026-11-21 | Track row alignment, search `+` button, and artist page header/duration/heart fix (`fix-artist` worktree) | (pending PR) |
| 2026-11-21 | Artist profile, discography and artist links (`artist` worktree) | (pending PR) |
| 2026-11-21 | Full-screen player: `⋯` track actions and `♡` favorite toggle | PR #49 |
| 2026-11-21 | Next/previous stops the current track and warms the next one early | `2bc0774` (#46) |
| 2026-11-21 | Favorites polish: larger heart glyph and red Remove from Favorites action | `a047725` (#45) |
| 2026-11-21 | Favorites use heart buttons: outlined `♡` to add, filled `♥` to remove | `4ca18a8` (#44) |
| 2026-11-21 | Clearing the search field resets Extended back to Songs | `f33284b` (#43) |
| 2026-11-21 | Show saved trim durations and keep playback when opening trim | `7304398` (#42) |
| 2026-11-21 | Playback trimming: per-user start/end, preview, trimmed progress/seek/MediaSession | `6ba8107` (#41) |
| 2026-11-21 | Extended tabs centered and Video badge placed before duration | `783b41d` (#39) |
| 2026-11-21 | Extended search: Songs/Extended tabs, video search, video radio | `56ad376` (#38) |
| 2026-11-21 | iOS lock-screen controls: previous/next and correct timeline | `364e9e8` (#35) |
| 2026-11-21 | Player loading state: spinner during load; Pause only after actual playback | `f181d9d` (#34) |

## Next up

- Manually verify the artist feature: `Земфира` in Songs shows up to 3 artists,
  Extended shows no shelf, artist and album screens load with empty shelves
  hidden, artist names in rows and the player open the profile, Back keeps the
  search intact, album Play stops after the last track, and the second open of
  an artist logs `cache=hit`.
- Manually verify playback trimming in a real Telegram WebView: set Start/End,
  preview stops at End, progress/seek use the trimmed length, the next track
  starts at End, Reset restores the full track, shared playlists do not expose
  the owner’s trim, and **Delete all my data** removes trims.
- Manually verify favourite hearts and next/previous behavior: heart toggle
  across search/favourites, red Remove action, current-track stop, and early
  warm-up of the next track.
- Validate Extended search in production: search `kirman lost`, confirm the
  9:29 remix is playable, the `Video` badge is visible, video radio works, and
  long videos stay out of the queue.
- Consider an “Include long mixes” toggle only if users ask for it.
- Deploy the tap-race fix and confirm `api_stream resolve_ms` is ~2–3 s for
  `clients=parallel(web,web_embedded)` / `parallel(web)`; confirm the player
  cache is reused and pruned on the volume.
- Manually verify the merged player loading-state fix (`PLAYER-001`) in a real
  Telegram WebView (iOS/Android); confirm the spinner, no dimmed Pause, and
  Pause only after playback starts.
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

# LammotBot — Architecture

**Telegram Mini App Music Player**
Version: 1.2 | Stack: Python + FastAPI + SQLite + HTML/CSS/JS | Deploy: Docker on Railway

Last verified against: `main @ 1056314` (2026-11-20)
Project status: [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)

---

## 1. System Overview

```
User (Telegram) → Mini App (HTML/CSS/JS)
                     │  search, history, favorites, playlists
                     ▼
                FastAPI backend ─┬→ ytmusicapi (search, radio)
                     ▲           ├→ yt-dlp + Deno (resolve CDN URL)
                     │           ├→ SQLite (favorites, history, playlists)
                     │           └→ SearchPrefetcher (top search results)
                     │
      /audio relay   │  ← googlevideo CDN
      (Range)        └──────────────────────────────
```

The server **never stores audio files**. It resolves a CDN URL with yt-dlp and
relays the bytes to the browser, which plays them with the HTML5 `<audio>` tag.

**Why a relay and not the CDN URL directly:** a googlevideo URL only works from
the IP address that resolved it. In production that is the server, not the
user's phone, so the browser would get 403. The relay costs egress traffic
(~5 MB per song) but is the only reliable option.

Key behaviors spread across backend and client:

- **Background prefetch of search results** — after `/api/search` answers,
  `app/prefetch.py` resolves the top 3 results in the background so a tap hits
  a warm resolver cache. A newer search from the same user supersedes older
  queued prefetches.
- **Client-side prefetch of the next queue track** — the frontend asks for the
  next track's stream about 15 seconds before the current one ends.
- **Opus/AAC selection on the client** — `frontend/audio-format.js` chooses the
  format. iOS/iPadOS always gets `aac`; other platforms get Opus only when
  `canPlayType('audio/webm; codecs="opus"')` answers `"probably"`. A failed
  Opus stream is retried once as AAC.
- **iOS duration normalization** — `frontend/player-time.js` treats track
  metadata duration as authoritative and ignores the roughly doubled media
  duration some iOS webviews report. It also forces the track end when the
  element keeps playing through the phantom tail.
- **`/audio` relay** — forwards CDN bytes with `Range` support so seeking works,
  and re-resolves once when the CDN answers 403/410.

---

## 2. Project Structure

```
lammot/
├── app/
│   ├── main.py          # FastAPI app: API routes, audio relay, webhook, security headers,
│   │                    #   canary loop, rate limiters, search prefetch wiring
│   ├── auth.py          # Telegram initData verification, signed audio links
│   ├── bot.py           # Bot API: setWebhook, menu button, /start reply, /cookies parsing
│   ├── search.py        # ytmusicapi search (official songs only)
│   ├── resolver.py      # yt-dlp stream resolver: format choice, gate race, cookies,
│   │                    #   concurrency limits, resolver state
│   ├── cache.py         # In-memory TTL cache for stream URLs and headers
│   ├── prefetch.py      # Background worker that warms the top search results
│   ├── radio.py         # Infinite radio queue algorithm
│   ├── migration.py     # SQLite backup/verify CLI for volume moves (WAL-safe)
│   └── database.py      # SQLite async operations, schema, retention
├── frontend/
│   ├── index.html       # App shell, mini-player, full-screen player, sheet, toast
│   ├── style.css        # Design tokens, themes, accents, wallpaper, layout
│   ├── app.js           # All UI logic: search, screens, player, queue, MediaSession
│   ├── audio-format.js  # Pure Opus/AAC choice, loaded before app.js
│   └── player-time.js   # Pure duration/progress helpers (iOS normalization)
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_auth.py
│   ├── test_bot.py
│   ├── test_database.py
│   ├── test_migration.py
│   ├── test_radio.py
│   ├── test_resolver.py
│   ├── test_search.py           # integration marker (real YouTube)
│   ├── test_search_prefetch.py
│   ├── test_audio_format.py     # Runs audio-format.test.js through Node
│   ├── audio-format.test.js
│   └── js/player-time.test.js
├── docs/
│   ├── bugfix-workflow.md       # Bugfix process and branch rules
│   ├── eu-migration.md          # EU volume move runbook (Russian)
│   └── PROJECT_STATUS.md        # Living status journal
├── .github/workflows/tests.yml  # CI: pytest -m "not integration"
├── Dockerfile                   # Python 3.12 + Deno, non-root user
├── docker-entrypoint.sh         # Hands the volume to the app user, starts uvicorn
├── railway.json                 # Dockerfile build, /healthz healthcheck
├── requirements.txt             # runtime
├── requirements-dev.txt         # runtime + pytest
├── .env.example                 # all settings, documented (real .env is never committed)
├── ARCHITECTURE.md
└── UI_SPEC.md
```

`app/migration.py` is a CLI (`python -m app.migration`) that uses the SQLite
backup API, so it captures rows still in the WAL file. `docs/eu-migration.md`
describes how it is used for the EU volume move.

---

## 3. Stream Resolver (`app/resolver.py`)

Converts a YouTube `video_id` into a playable audio stream. Blocking yt-dlp
calls run in the FastAPI thread pool; the resolver itself is synchronous.

### Format choice

`get_stream_url(video_id, prefer="opus", ...)` validates the id and keeps a
separate cache entry per format:

- `prefer="opus"`: itag **251** (WebM/Opus, ~160 kbps), then itag **140**
  (M4A/AAC, ~128 kbps).
- `prefer="aac"`: itag **140** first, then itag **251**.
- itag **18** (muxed 360p MP4/AAC) is a gate-free fallback. When the audio-only
  stream is behind a pre-roll gate and itag 18 is usable at least 1.5 s sooner,
  the resolver returns itag 18 instead.

The client picks the requested format (see `frontend/audio-format.js`) and
passes it as `?format=opus|aac`.

### Extraction and pre-roll gates

YouTube assigns a pre-roll gate per player response and exposes it as
`available_at`; the CDN answers 403 before that moment. The resolver:

- `_resolve_parallel()` races several client sets for taps and prefetch:
  `("web",)`, `("web_embedded", "default")`, `("web_embedded",)`. The first set
  that yields a usable format wins, which often avoids a long gate because a
  different client receives an ungated duplicate. The extraction race is capped
  by `_MAX_TAP_EXTRACT_WAIT` (12 s).
- If every set is gated, their formats are merged and the earliest gate is
  waited for, bounded by `_MAX_TAP_GATE_WAIT` (3 s).
- `_resolve_serial()` is the serial path used by the canary and cookie
  validation (`dedicated=True`): try the configured first-pass client(s)
  (`YTDLP_PLAYER_CLIENTS`, default `web`), then one fallback pass with
  `("web_embedded", "default")` when the first pass has no format usable within
  `_MAX_AVAILABILITY_WAIT` (2 s).
- `_wait_until_available()` sleeps for the remaining gate. A gate longer than
  the allowed wait raises `TransientResolveError`, not `UnavailableError`, so
  the track is not negative-cached.

### Concurrency and state

- One `threading.Lock` per cache key, so concurrent `/api/stream` and `/audio`
  requests for the same track share one extraction.
- `_RESOLVE_SEMAPHORE` limits interactive resolves (default 2,
  `YTDLP_RESOLVE_CONCURRENCY`).
- `_EXTRACT_SEMAPHORE` limits all yt-dlp extractions, including the serial
  canary and cookie validation (default 3, `YTDLP_EXTRACT_CONCURRENCY`).
- `_EXTRACT_POOL` bounds background extraction threads.
- `_CANARY_SEMAPHORE` gives the canary its own single slot.
- `_PREFETCH_GUARD` allows at most one prefetch to occupy a resolve slot.
  Other prefetches wait without taking a slot, so a user tap is never queued
  behind background work.
- `YTDLP_MIN_RESOLVE_INTERVAL` optionally paces extractions.
- `app/main.py` keeps a shared `httpx.AsyncClient` and warms the CDN with a
  1-byte Range request after `/api/stream` answers.

### Cookies and bot checks

- `YTDLP_COOKIES_FILE` is the primary account; `YTDLP_RESERVE_COOKIES_FILE` is
  the reserve account.
- `_resolve_with_accounts()` tries the active account first. Only
  `BotCheckError` switches to the other file; transient network errors do not.
- A recent bot check gates fresh extractions for `_BOT_CHECK_COOLDOWN` (60 s).
  Cached URLs still play during the cooldown.
- Resolver state (`status`: `unknown|ok|bot_check|degraded`, `active_account`,
  `transition_seq`, `last_error_class`, `reason`) is in memory and exposed by
  `GET /health`. `transition_seq` lets `app/main.py` alert the owner only on
  meaningful state changes.

### Negative cache and canary

- `VideoUnavailableError` and `FormatUnavailableError` negative-cache the cache
  key for 1 hour (`_NEGATIVE_TTL`). `invalidate()` clears the URL, headers and
  negative marker after a CDN 403/410.
- `CANARY_VIDEO_IDS` (up to 5 ids, at least 60 s interval) are probed in the
  background by `_canary_loop()`. The canary updates resolver status and is
  also used to validate replacement cookies before they are installed.

### Prefetch mode

`get_stream_url(..., prefetch=True)` uses the same client race as a tap, but
enters `_PREFETCH_GUARD` before taking a resolve slot. A tap starts its own
interactive request instead of joining a background warm-up, so the tap never
waits for queued prefetch work.

---

## 4. Infinite Radio Algorithm (`app/radio.py`)

Source: `ytmusicapi.get_watch_playlist(videoId=...)`.

### Server-side queue post-processing

- **Seed exclusion** — the track already playing is never returned.
- **History exclusion** — `/api/radio` builds the exclusion list from the
  client's queue plus the user's last 50 played tracks (`RADIO_HISTORY`).
- **Client queue exclusion** — the client sends up to 50 `exclude` ids
  (`MAX_EXCLUDE`).
- **Diversity filter** — at most one track per artist in any 4 consecutive
  tracks (`_DIVERSITY_WINDOW = 3`, so the last 3 artists are checked).

### Client-side behavior

- The client also excludes its last 20 local history entries when requesting
  radio.
- It resolves the next queue track about 15 seconds before the current track
  ends (`PREFETCH_SECONDS`).
- Search results use **radio mode**: the queue refills with similar songs.
- Favorites and playlists use **list mode**: next track in the list only, and
  playback stops after the last track.

---

## 5. API Endpoints (`app/main.py`)

The user id is **never** in the URL: it always comes from verified initData.
Success responses use the standard envelope `{"ok": true, "data": ...}`.
Tracks always have one shape:
`video_id, title, artist, duration_seconds, thumbnail_url`.

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/me` | ✓ | Current user + `share_base` URL for share links |
| `DELETE` | `/api/me?confirm=true` | ✓ | Delete all of the user's data (204) |
| `GET` | `/api/search?q=...&limit=10&format=opus\|aac` | ✓ | Search songs (rate limit 60/min). `format` selects the prefetch warm-up format; `limit` is 1–25, default 10 |
| `GET` | `/api/stream/{video_id}?format=opus\|aac&prefetch=1` | ✓ | Resolve and return a signed `/audio/...` link (60/min). `prefetch=1` marks a background warm-up |
| `GET` | `/audio/{video_id}?t=...` | signed link | Audio relay with Range support (not in OpenAPI schema) |
| `GET` | `/api/radio/{video_id}?exclude=...&limit=5` | ✓ | Next radio tracks; up to 50 exclude ids, limit 1–20, default 5 |
| `GET` | `/api/favorites` | ✓ | User's favorites, newest first |
| `POST` | `/api/favorites` | ✓ | Add track `{video_id, title, artist, duration_seconds, thumbnail_url}` |
| `DELETE` | `/api/favorites/{video_id}` | ✓ | Remove from favorites |
| `GET` | `/api/history` | ✓ | `data` = `{searches: [string], played: [track]}`: last 10 distinct searches and last 20 plays |
| `POST` | `/api/history` | ✓ | `{kind: "search", query}` or `{kind: "play", track}` |
| `DELETE` | `/api/history?type=search\|play\|all` | ✓ | Clear history (204) |
| `GET` | `/api/playlists` | ✓ | User's playlists with track counts |
| `POST` | `/api/playlists` | ✓ | Create `{name}` (max 100 per user) |
| `GET` | `/api/playlists/shared/{share_token}` | public | Read-only shared playlist (no owner data) |
| `GET` | `/api/playlists/{playlist_id}` | ✓ | Playlist + tracks (404 if not yours) |
| `DELETE` | `/api/playlists/{playlist_id}` | ✓ | Delete playlist (204) |
| `POST` | `/api/playlists/{playlist_id}/tracks` | ✓ | Add track |
| `DELETE` | `/api/playlists/{playlist_id}/tracks/{video_id}` | ✓ | Remove track |
| `POST` | `/telegram/webhook` | secret header | Bot updates from Telegram |
| `GET` | `/healthz` | public | Healthcheck for Railway |
| `GET` | `/health` | public | Last canary/resolver state (never resolves) |

The frontend is mounted last at `/` with `StaticFiles(html=True)`.

### Error contract

- Resolver failures from `/api/stream` use
  `{"ok": false, "detail": "...", "code": "..."}`:
  - `bot_check` → HTTP 503, "Music service is temporarily unavailable"
  - `unavailable` → HTTP 404, "This track is not available"
  - `upstream_error` → HTTP 502, "Stream service unavailable"
- The frontend maps `bot_check` to a friendly user message and treats other
  failures as skippable track errors.
- Rate limiting returns HTTP 429, invalid input HTTP 422, and failed auth
  HTTP 401.

### Authentication

- Header format: `Authorization: tma <initData>`
- HMAC-SHA256 checked with `hmac.compare_digest` (constant time)
- `auth_date` older than `INIT_DATA_MAX_AGE` (default 24 h) is rejected
- The app refuses to start without `BOT_TOKEN`
- Audio links: `<audio>` cannot send headers, so `/api/stream` returns a link
  signed with HMAC over video_id + format + user + expiry (6 h)
- Webhook: Telegram sends `X-Telegram-Bot-Api-Secret-Token`; the secret is
  derived from the bot token and checked in constant time
- All keys are derived from `BOT_TOKEN`; revoking the token in BotFather
  invalidates every link and the webhook secret

### Other protections

- Someone else's playlist returns 404 (same as a missing one)
- Content-Security-Policy, `nosniff`, `no-referrer`; `/api/*` is `no-store`
- httpx request logging is off, so the bot token (part of Bot API URLs) never reaches logs
- Upstream error details are logged, never sent to the client

---

## 6. Database Schema (`app/database.py`)

**SQLite** via `aiosqlite` (async). File path from `DATABASE_PATH` (on Railway:
`/data/lammot.db` on a volume). `PRAGMA foreign_keys = ON` is set on every
connection; WAL mode and the schema are set once in `init_db()`.

Retention: the newest 50 searches and 200 plays are kept per user; older rows
are deleted in the same transaction as the insert. Users can clear history or
delete all their data from Settings.

```sql
CREATE TABLE users (
    telegram_user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL
        REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    track_id TEXT NOT NULL,
    title TEXT,
    artist TEXT,
    duration INTEGER,              -- API maps this to duration_seconds
    thumbnail_url TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(telegram_user_id, track_id)
);
CREATE INDEX idx_favorites_user ON favorites(telegram_user_id, id);

CREATE TABLE playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL
        REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    share_token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_playlists_user ON playlists(telegram_user_id, id);

CREATE TABLE playlist_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL
        REFERENCES playlists(id) ON DELETE CASCADE,
    track_id TEXT NOT NULL,
    title TEXT,
    artist TEXT,
    duration INTEGER,
    thumbnail_url TEXT,
    position INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(playlist_id, track_id)
);

CREATE TABLE search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL
        REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_search_history_user ON search_history(telegram_user_id, id);

CREATE TABLE play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL
        REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    track_id TEXT NOT NULL,
    title TEXT,
    artist TEXT,
    thumbnail_url TEXT,
    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_play_history_user ON play_history(telegram_user_id, id);

-- Legacy/unused shared-cache tables. The running app uses app/cache.py.
CREATE TABLE stream_cache (
    video_id TEXT PRIMARY KEY,
    stream_url TEXT NOT NULL,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE track_resolution (
    track_key TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Notes:

- `stream_cache` and `track_resolution` are created for a possible persistent
  cache, but no runtime path uses them. Helper functions exist in
  `app/database.py`; the resolver and API use the in-memory cache only.
- `share_token` is generated with `secrets.token_urlsafe(16)` and is
  `NOT NULL UNIQUE`; shared links stop working when the playlist or user data is
  deleted.
- `playlist_tracks.position` defines order; adding a track appends
  `MAX(position) + 1`.
- `delete_user_data()` removes favorites, playlists with their tracks, and both
  histories in one transaction. It does not touch the shared cache tables,
  which contain no personal data.

---

## 7. Caching Strategy (`app/cache.py`)

The working cache is an in-memory TTL dict. No Redis.

- Max 5000 entries with lazy eviction; expired entries are removed on access.
- Keys: `"<video_id>"` for Opus, `"<video_id>:aac"` for AAC,
  `"<key>:headers"` for CDN headers, and `"<key>:unavailable"` for the negative
  cache.
- **TTL is derived from the CDN URL's `expire` parameter** (minus a 30 min
  safety margin, capped at 6 h); **2 h is the fallback** when `expire` is
  unavailable. URLs with less than 60 s left are not cached.
- Dead videos and unavailable formats are negative-cached for 1 h.
- Per-key resolver locks prevent duplicate extractions for the same key.
- One process only (single uvicorn worker), so the cache, resolver state and
  rate limiter are consistent.

---

## 8. Hosting & Operations

**Platform: Railway**, built from the `Dockerfile` (`railway.json`).

| What | How |
|---|---|
| Backend + frontend | One Railway service; FastAPI serves `frontend/` with `StaticFiles` |
| SQLite | Railway volume mounted at `/data`, `DATABASE_PATH=/data/lammot.db` |
| Secrets | Railway Variables (`BOT_TOKEN`), never in git |
| Bot webhook | Set automatically on startup when `PUBLIC_URL` is set |
| Health | `GET /healthz` (liveness) and `GET /health` (resolver state) |
| Process user | Non-root (uid 10001); entrypoint gives it the volume |

### Resolver operations

- **Canary** — `CANARY_VIDEO_IDS` (comma-separated, up to 5) is probed in the
  background every `CANARY_INTERVAL_SECONDS` (default 900, minimum 60).
  `CANARY_START_DELAY_SECONDS` defaults to 15.
- **Cookie replacement** — the owner (`OWNER_TG_ID`) sends a Netscape
  `cookies.txt` document with `/cookies` (or `/cookies reserve`). The server
  validates the file (≤100 KB, YouTube cookies present), tests it against a
  canary track, then atomically replaces
  `YTDLP_COOKIES_FILE` or `YTDLP_RESERVE_COOKIES_FILE`.
- **Alerts** — `OWNER_TG_ID` also receives Telegram alerts on resolver state
  transitions: bot check, recovery, degradation, or cookie-account switch.
- **yt-dlp tuning** — `YTDLP_CACHE_DIR` enables a persistent player/n-sig cache
  (use `/data/cache` on Railway); `YTDLP_PLAYER_CLIENTS` overrides the first
  player-client chain; `BGUTIL_BASE_URL` enables a bgutil PO-token provider
  when the plugin is installed.
- **Volume move** — see [docs/eu-migration.md](docs/eu-migration.md) for the
  WAL-safe backup/restore runbook.

### Single-process assumptions

The in-memory cache, rate limiter, resolver semaphores, per-key locks,
prefetch queue, and resolver state are process-local. Running multiple
replicas would split this state, multiply yt-dlp load, and create SQLite write
contention. Keep one replica.

Costs: Railway bills usage (CPU, RAM, volume, egress). Audio relay traffic is
egress. Check current prices on railway.com before relying on the free tier.

---

## 9. Telegram Bot Integration

- Bot username: whatever you register in BotFather (read via `getMe` on startup)
- `/start` → bot replies with the **"Open Lammot 🎵"** web_app button
- Chat menu button "Open" is set via `setChatMenuButton` on startup
- Identity: `telegram_user_id` from verified initData
- `/cookies` and `/cookies reserve` are owner-only commands handled through
  the same webhook (see section 8)

### Playlist sharing

`/api/me` returns `share_base`, built from the bot username and optional
`MINI_APP_SHORT_NAME`:

- without a short name: `https://t.me/<bot>?startapp=`
- with `MINI_APP_SHORT_NAME`: `https://t.me/<bot>/<short_name>?startapp=`

The frontend appends `playlist_<share_token>` and uses
`openTelegramLink` to open Telegram's share sheet. The Mini App reads
`initDataUnsafe.start_param`, validates the token, and opens the playlist
read-only via `/api/playlists/shared/{share_token}`. Fallback:
`/start playlist_<token>` makes the bot reply with a button that opens it.

---

## 10. Current Status

Sprints 1–8 are complete. Post-release hardening includes:

- **#18–#19** — background search prefetch and the resolver gate work that
  lets prefetch survive pre-roll gates and drop stale searches.
- **#20–#21** — iOS playback: metadata duration is authoritative, doubled
  media duration is ignored, and a forced track end is used.
- **#22** — the multi-agent workflow and TDD rules in `AGENTS.md`.

CI (`.github/workflows/tests.yml`) runs only
`python -m pytest -m "not integration" -v`. `tests/test_audio_format.py` runs
the Node test for `frontend/audio-format.js` through pytest.
`tests/js/player-time.test.js` is documented in `AGENTS.md` but is **not**
executed by the current workflow.

Live status, known bugs, limitations, and recent decisions are maintained in
[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md).

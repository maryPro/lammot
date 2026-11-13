# LammotBot — Architecture

**Telegram Mini App Music Player**
Version: 1.1 | Stack: Python + FastAPI + SQLite + HTML/CSS/JS | Deploy: Docker on Railway

---

## 1. System Overview

```
User (Telegram) → Mini App (HTML/JS) → FastAPI backend ─┬→ ytmusicapi (search, radio)
                        ▲                               ├→ yt-dlp + Deno (resolve stream URL)
                        │  audio bytes (Range)          └→ SQLite (favorites, history, playlists)
                        └──────── /audio relay ◄──────── googlevideo CDN

Telegram Bot API ──webhook──► /telegram/webhook  (/start → "Open Lammot" button)
```

The server **never stores audio files**. It resolves a CDN URL with yt-dlp and
relays the bytes to the browser, which plays them with the HTML5 `<audio>` tag.

**Why a relay and not the CDN URL directly:** a googlevideo URL only works from
the IP address that resolved it. In production that is the server, not the
user's phone, so the browser would get 403. The relay costs egress traffic
(~5 MB per song) but is the only reliable option.

---

## 2. Project Structure

```
lammot/
├── app/
│   ├── main.py          # FastAPI app: API routes, audio relay, webhook, security headers
│   ├── auth.py          # Telegram initData verification, signed audio links
│   ├── bot.py           # Bot API: setWebhook, menu button, /start reply
│   ├── search.py        # Sprint 1: ytmusicapi search
│   ├── resolver.py      # Sprint 2: yt-dlp stream URL resolver (opus / aac)
│   ├── cache.py         # In-memory TTL cache for stream URLs
│   ├── radio.py         # Sprint 3: radio queue algorithm
│   └── database.py      # Sprint 5: SQLite async operations
├── frontend/
│   ├── index.html       # Sprint 6: app shell
│   ├── style.css        # Sprint 6–7: tokens, themes, accent colors, wallpaper
│   └── app.js           # Sprint 6–7: all UI logic, player, queue
├── tests/               # pytest: api, auth, bot, database, resolver, radio, search
├── Dockerfile           # Sprint 8: Python 3.12 + Deno, non-root user
├── docker-entrypoint.sh # Hands the volume to the app user, starts uvicorn
├── railway.json         # Sprint 8: Dockerfile build, /healthz healthcheck
├── requirements.txt     # runtime
├── requirements-dev.txt # runtime + pytest
├── .env.example         # all settings, documented (real .env is never committed)
├── ARCHITECTURE.md
└── UI_SPEC.md
```

---

## 3. Stream Resolver (app/resolver.py)

Converts a YouTube `video_id` into a playable audio stream.

1. **Search** — `ytmusicapi` with `filter="songs"` (official tracks only)
2. **Stream extraction** — `yt-dlp`, info only (`skip_download=True`)
   - `prefer="opus"`: itag 251 (WebM/Opus ~160 kbps), fallback itag 140
   - `prefer="aac"`: itag 140 (M4A/AAC ~128 kbps), fallback itag 251
   - The client asks for `aac` when the webview cannot play WebM/Opus (older
     iOS), and retries as `aac` if Opus playback fails.
3. **Relay** — `/audio/{video_id}` streams the CDN response to the browser,
   forwarding `Range` so seeking works. On CDN 403/410 the URL is re-resolved once.

yt-dlp needs a JavaScript runtime for YouTube since 2025.11: the Docker image
ships Deno, and `yt-dlp[default]` installs the `yt-dlp-ejs` scripts.

### Caching
| What | Where | TTL |
|---|---|---|
| `video_id(+format) → CDN URL` and its HTTP headers | In-memory dict (`app/cache.py`) | 2 hours |
| `track_key → video_id` | SQLite `track_resolution` (table ready, not used yet) | Forever |

---

## 4. Infinite Radio Algorithm (app/radio.py)

Source: `ytmusicapi.get_watch_playlist(videoId=...)`

### Queue post-processing:
- **Deduplication**: skips the seed, the user's last 50 played tracks (from
  `play_history`) and the ids already in the client's queue (`exclude`)
- **Diversity filter**: max 1 track per artist per 4-track block
- **Lazy resolving**: the next song's link is requested 15 seconds before the
  current one ends (`timeupdate` in app.js)

### Playback modes:
| Source | Behavior |
|---|---|
| Search result | Radio mode: queue refills with similar songs |
| Favorites | Next track in the list only. Stops after the last one. |
| Custom / shared playlist | Next track in the list only. Stops after the last one. |

---

## 5. API Endpoints (app/main.py)

The user id is **never** in the URL: it always comes from verified initData.
Responses use `{"ok": true, "data": ...}`. Tracks always have one shape:
`video_id, title, artist, duration_seconds, thumbnail_url`.

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/me` | ✓ | Current user + base URL for share links |
| `DELETE` | `/api/me?confirm=true` | ✓ | Delete all of the user's data |
| `GET` | `/api/search?q=...&limit=10` | ✓ | Search songs (rate limit 60/min) |
| `GET` | `/api/stream/{video_id}?format=opus\|aac` | ✓ | Resolve and return a signed `/audio/...` link (60/min) |
| `GET` | `/audio/{video_id}?t=...` | signed link | Audio relay with Range support |
| `GET` | `/api/radio/{video_id}?exclude=...&limit=5` | ✓ | Next radio tracks |
| `GET` | `/api/favorites` | ✓ | User's favorites |
| `POST` | `/api/favorites` | ✓ | Add track `{video_id, title, ...}` |
| `DELETE` | `/api/favorites/{video_id}` | ✓ | Remove from favorites |
| `GET` | `/api/history` | ✓ | Last 10 distinct searches + last 20 plays |
| `POST` | `/api/history` | ✓ | `{kind: "search", query}` or `{kind: "play", track}` |
| `DELETE` | `/api/history?type=search\|play\|all` | ✓ | Clear history |
| `GET` | `/api/playlists` | ✓ | User's playlists with track counts |
| `POST` | `/api/playlists` | ✓ | Create `{name}` (max 100 per user) |
| `GET` | `/api/playlists/{playlist_id}` | ✓ | Playlist + tracks (404 if not yours) |
| `DELETE` | `/api/playlists/{playlist_id}` | ✓ | Delete playlist |
| `POST` | `/api/playlists/{playlist_id}/tracks` | ✓ | Add track |
| `DELETE` | `/api/playlists/{playlist_id}/tracks/{video_id}` | ✓ | Remove track |
| `GET` | `/api/playlists/shared/{share_token}` | public | Read-only shared playlist (no owner data) |
| `POST` | `/telegram/webhook` | secret header | Bot updates from Telegram |
| `GET` | `/healthz` | public | Healthcheck for Railway |

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

## 6. Database Schema (app/database.py)

**SQLite** via `aiosqlite` (async). File path from `DATABASE_PATH` (on Railway: `/data/lammot.db` on a volume).

Retention: the newest 50 searches and 200 plays are kept per user; older rows are deleted on insert. Users can clear history or delete all their data from Settings.

```sql
-- Users (auto-created on first open)
CREATE TABLE users (
    telegram_user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Favorites (default playlist)
CREATE TABLE favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER,
    track_id TEXT,         -- YouTube video_id
    title TEXT,
    artist TEXT,
    duration INTEGER,      -- seconds
    thumbnail_url TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(telegram_user_id, track_id)
);

-- Custom playlists
CREATE TABLE playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER,
    name TEXT,
    share_token TEXT UNIQUE,   -- for sharing via link
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE playlist_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER REFERENCES playlists(id) ON DELETE CASCADE,
    track_id TEXT,
    title TEXT,
    artist TEXT,
    duration INTEGER,
    thumbnail_url TEXT,
    position INTEGER,          -- order in playlist
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Search history
CREATE TABLE search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER,
    query TEXT,
    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Play history
CREATE TABLE play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER,
    track_id TEXT,
    title TEXT,
    artist TEXT,
    thumbnail_url TEXT,
    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Stream URL cache (backup if in-memory cache is lost)
CREATE TABLE stream_cache (
    video_id TEXT PRIMARY KEY,
    stream_url TEXT,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Track ID resolution cache (persistent)
CREATE TABLE track_resolution (
    track_key TEXT PRIMARY KEY,  -- normalized "artist - title"
    video_id TEXT,
    resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

---

## 7. Caching Strategy (app/cache.py)

No Redis. In-memory TTL dict, max 5000 entries, lazy eviction.
**TTL = 2 hours:** YouTube CDN URLs expire in ~6 hours; 2 h leaves margin.
One process only (single uvicorn worker), so the cache and the rate limiter
are consistent.

---

## 8. Hosting & Deployment

**Platform: Railway**, built from the `Dockerfile` (`railway.json`).

| What | How |
|---|---|
| Backend + frontend | One Railway service; FastAPI serves `frontend/` with `StaticFiles` |
| SQLite | Railway volume mounted at `/data`, `DATABASE_PATH=/data/lammot.db` |
| Secrets | Railway Variables (`BOT_TOKEN`), never in git |
| Bot webhook | Set automatically on startup when `PUBLIC_URL` is set |
| Health | `GET /healthz` |
| Process user | Non-root (uid 10001); entrypoint gives it the volume |

Costs: Railway bills usage (CPU, RAM, volume, egress). Audio relay traffic is
egress. Check current prices on railway.com before relying on the free tier.

---

## 9. Telegram Bot Integration

- Bot username: whatever you register in BotFather (read via `getMe` on startup)
- `/start` → bot replies with the **"Open Lammot 🎵"** web_app button
- Chat menu button "Open" is set via `setChatMenuButton` on startup
- Identity: `telegram_user_id` from verified initData

### Playlist sharing
Link: `https://t.me/<bot>?startapp=playlist_<share_token>` (Main Mini App,
enable it in BotFather). With `MINI_APP_SHORT_NAME` set:
`https://t.me/<bot>/<short_name>?startapp=playlist_<share_token>`.
The Mini App reads `start_param` and opens the playlist read-only.
Fallback: `/start playlist_<token>` makes the bot reply with a button that opens it.

---

## 10. Sprint Plan

| Sprint | Module | Status |
|---|---|---|
| 1 | `app/search.py` | ✅ |
| 2 | `app/resolver.py`, `app/cache.py` | ✅ |
| 3 | `app/radio.py` | ✅ |
| 4 | `app/main.py`, `app/auth.py` | ✅ |
| 5 | `app/database.py` (+ API wiring, data deletion) | ✅ |
| 6 | `frontend/` core: search, cards, player, queue | ✅ |
| 7 | `frontend/` themes, 15 accents, wallpaper, settings, playlists | ✅ |
| 8 | Dockerfile, railway.json, `app/bot.py`, deploy | ✅ code ready, deploy by hand |

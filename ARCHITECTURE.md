# LammotBot — Architecture

**Telegram Mini App Music Player**
Version: 1.0 | Stack: Python + FastAPI + SQLite + HTML/CSS/JS

---

## 1. System Overview

```
User (Telegram) → Mini App (HTML/JS) → FastAPI Backend → yt-dlp → YouTube CDN
                                      ↕
                                   SQLite DB
                                (favorites, history,
                                 playlists, search cache)
```

The server **never stores audio files**. It only resolves a direct CDN stream URL from YouTube and returns it to the client. The browser plays audio natively via the `<audio>` HTML5 tag.

---

## 2. Project Structure

```
lammot/
├── app/
│   ├── main.py          # FastAPI app, all API routes
│   ├── search.py        # Sprint 1: ytmusicapi search
│   ├── resolver.py      # Sprint 2: yt-dlp stream URL resolver
│   ├── radio.py         # Sprint 3: infinite radio queue algorithm
│   ├── database.py      # Sprint 5: SQLite async operations
│   └── cache.py         # In-memory TTL cache for stream URLs
├── frontend/
│   ├── index.html       # Sprint 6: main app shell
│   ├── style.css        # Sprint 7: themes, accent colors, wallpapers
│   └── app.js           # Sprint 6+7: all UI logic
├── tests/
│   ├── test_search.py
│   ├── test_resolver.py
│   ├── test_radio.py
│   └── test_api.py
├── ARCHITECTURE.md
├── UI_SPEC.md
├── requirements.txt
├── .gitignore
└── .env                 # BOT_TOKEN, SECRET (never committed)
```

---

## 3. Stream Resolver (app/resolver.py)

Converts a song title + artist into a playable HTTPS audio stream URL.

### Steps:
1. **Query normalization** — strip `[Explicit]`, `(Remastered 2021)`, brackets
2. **YouTube Music search** — `ytmusicapi` with `filter="songs"` (official tracks only, no covers/remixes)
3. **Candidate scoring**:
   - Duration delta ≤ ±3 seconds (filters out video clips with long intros)
   - Priority: "Artist - Topic" channels (official label distributors)
4. **Stream extraction** — `yt-dlp` with `skip_download=True`
   - Preferred format: `itag 251` (WebM/Opus ~160 kbps)
   - Fallback format: `itag 140` (M4A/AAC ~128 kbps)
5. **Return** — direct `googlevideo.com` CDN URL to client

### Caching:
| What | Where | TTL |
|---|---|---|
| `track_title → youtube_video_id` | SQLite (persistent) | Forever |
| `youtube_video_id → CDN stream URL` | In-memory dict | 2 hours |

---

## 4. Infinite Radio Algorithm (app/radio.py)

Source: `ytmusicapi.get_watch_playlist(videoId=...)`

### Queue post-processing:
- **Deduplication**: exclude tracks from user's last 50 played history
- **Diversity filter**: max 1 track per artist per 4-track block
- **Lazy resolving**: stream URL for the next track is resolved 15 seconds before current track ends (via `timeupdate` JS event)

### Playback modes:
| Source | Behavior |
|---|---|
| Search result | After track ends → Radio mode (auto-queue similar tracks) |
| Favorites playlist | After track ends → Next track in playlist only. No radio. |
| Custom playlist | After track ends → Next track in playlist only. No radio. |

---

## 5. API Endpoints (app/main.py)

Base URL: `https://lammot.up.railway.app`

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/search?q=...&limit=10` | Search songs via ytmusicapi |
| `GET` | `/api/stream/{video_id}` | Get CDN stream URL for a track |
| `GET` | `/api/radio/{video_id}?exclude=...&limit=5` | Get next radio queue tracks (seed and `exclude` ids skipped; `exclude` is temporary until Sprint 5 reads play_history) |
| `GET` | `/api/favorites/{telegram_user_id}` | Get user's favorites |
| `POST` | `/api/favorites/{telegram_user_id}` | Add track to favorites |
| `DELETE` | `/api/favorites/{telegram_user_id}/{track_id}` | Remove from favorites |
| `GET` | `/api/history/{telegram_user_id}` | Get last 10 search queries + last played |
| `POST` | `/api/history/{telegram_user_id}` | Save search query or played track |
| `GET` | `/api/playlists/{telegram_user_id}` | Get all user playlists |
| `POST` | `/api/playlists/{telegram_user_id}` | Create new playlist |
| `GET` | `/api/playlists/{telegram_user_id}/{playlist_id}` | Get playlist tracks |
| `POST` | `/api/playlists/{telegram_user_id}/{playlist_id}/tracks` | Add track to playlist |
| `DELETE` | `/api/playlists/{telegram_user_id}/{playlist_id}/tracks/{track_id}` | Remove from playlist |
| `GET` | `/api/playlists/shared/{share_token}` | Get shared playlist (public, no auth) |

### Authentication:
All `/api/*` endpoints (except `/shared/`) verify Telegram's `initData` hash to confirm the request comes from a real Telegram user. No passwords, no tokens — Telegram handles identity.

- Header format: `Authorization: tma <initData>`
- Hash compared with `hmac.compare_digest` (constant time)
- `auth_date` older than `INIT_DATA_MAX_AGE` (default 24 h) is rejected
- The app refuses to start without `BOT_TOKEN` (see `.env.example`)

---

## 6. Database Schema (app/database.py)

**SQLite** via `aiosqlite` (async).

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

## 7. Caching Strategy (app/cache.py)

No Redis needed. Simple Python in-memory TTL dict.

```python
# Pseudo-code
cache = {}  # { video_id: (stream_url, expires_at) }

def get_stream_url(video_id):
    if video_id in cache and cache[video_id].expires_at > now():
        return cache[video_id].url   # instant, 0ms
    url = resolve_via_ytdlp(video_id)
    cache[video_id] = CacheEntry(url, expires_at=now() + 2h)
    return url
```

**Why TTL = 2 hours:** YouTube CDN URLs expire in ~6 hours. We use 2h to stay safe with margin.

---

## 8. Hosting & Deployment

**Platform: Railway** (free tier, $5/month credit)

| What | Service |
|---|---|
| FastAPI backend | Railway Web Service |
| SQLite database | Persisted volume on Railway |
| Frontend static files | Served by FastAPI (`StaticFiles`) |
| Telegram bot webhook | Auto-configured on deploy |

### Why Railway:
- Free $5/month credit — covers a small app completely
- No sleep on inactivity (unlike Render free tier)
- One-click GitHub deploy
- Persistent filesystem for SQLite

---

## 9. Telegram Bot Integration

- Bot name: **LammotBot**
- Bot username: **@LammotBot**
- Mini App URL set via BotFather: `https://lammot.up.railway.app`
- Entry: user sends `/start` → bot replies with button **"Open Lammot 🎵"**
- Identity: `telegram_user_id` from Telegram `initData` (verified server-side)

### Playlist sharing:
Deep link format: `t.me/LammotBot/app?startapp=playlist_{share_token}`
When opened → Mini App reads `startapp` param → loads shared playlist in read-only view.

---

## 10. Sprint Plan

| Sprint | Module | Description |
|---|---|---|
| 1 | `app/search.py` | ytmusicapi song search, multi-language, scoring |
| 2 | `app/resolver.py` | yt-dlp stream URL extraction + Range header test |
| 3 | `app/radio.py` | Radio queue algorithm, dedup, diversity filter |
| 4 | `app/main.py` | FastAPI all endpoints + initData auth |
| 5 | `app/database.py` | SQLite: users, favorites, playlists, history, cache |
| 6 | `frontend/` | index.html, search, cards, audio playback |
| 7 | `frontend/` | Themes, 15 accent colors, wallpapers, queue UI |
| 8 | Deployment | Railway deploy, BotFather setup, end-to-end test |

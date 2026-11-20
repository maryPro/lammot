# LammotBot — UI Specification

**Telegram Mini App | Language: English**
Version: 1.2

Last verified against: `PR #27` (2026-11-20)
Project status: [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)

---

## 1. Design Tokens

### Colors

#### Light Theme (default)
```css
--bg-primary: #FFFFFF;
--bg-primary-rgb: 255, 255, 255;
--bg-secondary: #F5F5F5;
--bg-card: #FFFFFF;
--text-primary: #111111;
--text-secondary: #666666;
--border: #E0E0E0;
--shadow: rgba(0, 0, 0, 0.08);
--danger: #D92D20;
```

#### Dark Theme
```css
--bg-primary: #121212;
--bg-primary-rgb: 18, 18, 18;
--bg-secondary: #1E1E1E;
--bg-card: #242424;
--text-primary: #FFFFFF;
--text-secondary: #AAAAAA;
--border: #333333;
--shadow: rgba(0, 0, 0, 0.4);
--danger: #FF6B5E;
```

#### Accent Colors (15 options, user-selectable)
```css
/* Default */
--accent: #BC13FE;  /* 1. Cyberpunk Purple ★ default */
--on-accent: #FFFFFF;
--accent-soft: rgba(188, 19, 254, 0.12);
--accent-glow: rgba(188, 19, 254, 0.45);

/* Full palette */
/* 1  */ #BC13FE  /* Cyberpunk Purple */
/* 2  */ #8B5CF6  /* Deep Violet */
/* 3  */ #FF2D78  /* Neon Pink */
/* 4  */ #00D4FF  /* Electric Blue */
/* 5  */ #00FF41  /* Matrix Green */
/* 6  */ #FF6B00  /* Cyber Orange */
/* 7  */ #FF073A  /* Neon Red */
/* 8  */ #FFD700  /* Gold */
/* 9  */ #00CED1  /* Mint */
/* 10 */ #E040FB  /* Lavender */
/* 11 */ #FF7F50  /* Copper */
/* 12 */ #50C878  /* Emerald */
/* 13 */ #6610F2  /* Indigo */
/* 14 */ #006994  /* Ocean */
/* 15 */ #C0C0C0  /* Silver */
```

The accent is user-selectable in Settings. When it changes, `app.js` sets
`--accent`, `--accent-soft`, and `--accent-glow`. `--on-accent` is chosen from
the accent's relative luminance: dark text on light accents such as Gold,
Silver, and Matrix Green, white text on dark accents.

### Typography
```css
--font: -apple-system, 'SF Pro Display', 'Roboto', sans-serif;
--font-size-title: 16px;
--font-size-body: 14px;
--font-size-caption: 12px;
--font-weight-bold: 600;
--font-weight-normal: 400;
```

### Spacing, Radius, Layout
```css
--radius-card: 12px;
--radius-pill: 999px;
--spacing-xs: 4px;
--spacing-sm: 8px;
--spacing-md: 16px;
--spacing-lg: 24px;

--tabbar-h: 58px;
--mini-h: 64px;
--safe-bottom: max(env(safe-area-inset-bottom, 0px),
                   var(--tg-safe-area-inset-bottom, 0px));
--wallpaper-overlay: 0.88;  /* 0.80 in dark theme */
```

---

## 2. Screen Layouts

### 2.1 Main Screen and Search

```
┌─────────────────────────┐
│  🔍 Search music…       │  ← 44px round search pill
│─────────────────────────│
│  Recent searches        │
│   🕘 Zemfira            │  ← up to 10 unique queries
│   🕘 Imagine Dragons    │
│                         │
│  [mini-player]          │  ← once a track is loaded
│─────────────────────────│
│  ⚙️         ⭐       👤 │  ← Settings | Favorites | Profile
└─────────────────────────┘
```

- The search field is a 44 px pill.
- Search runs only on explicit Enter / the mobile keyboard Search action.
  Typing never requests or displays results.
- Editing the query hides any previous results and returns to Recent searches.
- At least 2 characters are required to submit.
- Up to 20 results are requested (`limit=20`); the API cap is 25.
- Recent searches show up to 10 unique queries, newest first.
- With no recent searches, the home screen shows an empty-state message
  inviting the user to search.
- The mini-player appears once a track is loaded and remains visible while
  paused or loading, not only while audio is actively playing.
- The clear button appears only when the field is not empty.

### 2.2 Search Results and Track Card

```
┌─────────────────────────┐
│  🔍 Zemfira          ×  │
│─────────────────────────│
│ [thumb] Title       [+] │  ← favorite button
│         Artist      ⋯   │  ← more actions
│         3:45            │
│─────────────────────────│
│ [thumb] Title       [✓] │  ← already in Favorites
│         Artist      ⋯   │
└─────────────────────────┘
```

Track card:

- Thumbnail: 48×48 px, rounded 8 px; a music note placeholder if the image fails.
- Title: 14 px bold, one line, truncated.
- Artist: 12 px secondary color, one line, truncated.
- Duration: 12 px secondary color, tabular numerals, right-aligned when known.
- The row itself starts playback.
- Search and shared-playlist rows show the favorite button: `+` to add, `✓`
  (checked, greyed) when already saved.
- The `⋯` more-actions button opens a sheet:
  - **Play next** — only when a track is already loaded.
  - **Add to playlist** — opens the playlist picker, including “New playlist…”.
  - **Add to Favorites** or **Remove from Favorites** — reflects current state.
  - **Remove from this playlist** — only in the playlist screen, marked danger.
- The currently playing row gets an accent ring around its thumbnail.

### 2.3 Favorites Screen

```
┌─────────────────────────┐
│ ←  My Favorites         │
│  🔍 Search in Favorites │
│─────────────────────────│
│ [thumb] Title       ⋯   │
│         Artist          │
│         3:45            │
│─────────────────────────│
│  No favorites yet       │  ← empty state
└─────────────────────────┘
```

- Tracks are ordered **newest first**.
- “Search in Favorites” filters by title and artist.
- Rows have no favorite button (the track is already saved), but keep the `⋯`
  menu.
- Playback uses list mode: next track in the filtered list, no radio, and it
  stops after the last track.
- Empty state: “No favorites yet — Tap + next to a song in search to save it
  here.”
- A filter with no matches shows “No songs match your search.”

### 2.4 Playlist Screen

```
┌─────────────────────────┐
│ ←  Playlist Name   ⋯    │
│    12 songs             │
│─────────────────────────│
│  [▶ Play]   [Share]     │
│─────────────────────────│
│ [thumb] Title       ⋯   │
│         Artist          │
└─────────────────────────┘
```

- Header: back button, playlist name, track count (`1 song` / `N songs`).
- **Play** button starts list mode from the first track; disabled when empty.
- **Share** button opens Telegram's share sheet with the server-provided link.
- `⋯` menu: **Share** and **Delete playlist** (danger).
- Empty state: “This playlist is empty. Find a song in search, tap ⋯ and choose
  ‘Add to playlist’.”
- Track `⋯` menu adds **Remove from this playlist**.
- Deleting asks for confirmation and warns that the share link will stop
  working.

### 2.5 Shared Playlist Screen (read-only)

```
┌─────────────────────────┐
│ ←  Playlist Name        │
│    Shared playlist      │
│─────────────────────────│
│  [▶ Play]               │
│─────────────────────────│
│ [thumb] Title       [+] │
│         Artist      ⋯   │
└─────────────────────────┘
```

- Opened from `start_param=playlist_<token>` or the `playlist` query parameter.
- The header shows the playlist name with the subtitle “Shared playlist”;
  before loading, the title is “Shared playlist”.
- Only **Play** is available. There is no share, delete, or playlist menu.
- Track rows keep the favorite button and `⋯` menu; there is no “Remove from
  this playlist” action.
- Empty state: “This playlist is empty.”

### 2.6 Mini-Player

Appears above the bottom bar once a track is loaded. Height: `--mini-h` (64 px).

```
┌─────────────────────────┐
│ [thumb] Title  ⏮  ⏯  ⏭ │
│         Artist          │
│ ━━━━━━━━━━━━━━━━━━━━    │  ← 2px progress bar, accent color
└─────────────────────────┘
```

- Tap the info area → opens the Full-Screen Player.
- Controls: previous, play/pause, next.
- Play/pause icon reflects the player state; the play button is dimmed while
  loading.
- Thumbnail: 40×40 px, rounded 6 px.

### 2.7 Full-Screen Player

```
┌─────────────────────────┐
│            ⌄            │  ← chevron close, or swipe down
│  ┌─────────────────┐    │
│  │    Artwork      │    │  ← 240×240, rounded 16px
│  └─────────────────┘    │
│  Title                  │
│  Artist                 │
│  ━━━━━━●────────────    │  ← draggable range input
│  1:23              3:45 │
│      ⏮    ⏯    ⏭       │
│─────────────────────────│
│  Up next   Radio: …     │  ← source label
│  1. Next Track       ×  │
│  2. Track After      ×  │
│  3. …                ×  │
└─────────────────────────┘
```

- Close via swipe down or the chevron button; there is no cross-style close control.
- Artwork is 240×240 px, rounded 16 px, with an accent glow.
- Timeline is a draggable `<input type="range">` with elapsed/total labels;
  dragging updates the label immediately and seeks on release.
- Queue shows 5 tracks. Items are numbered and clickable to jump to that track;
  a remove control removes an item from the queue.
- Source label shows `Radio: similar songs`, `From Favorites`, or
  `From <playlist>`.
- States:
  - **Loading** — the play button is dimmed while the stream resolves.
  - **Paused** — the play icon is shown, artwork scales down slightly.
  - **Error** — a toast explains the failure; failed tracks skip automatically.
  - **Finding similar songs…** — shown in the queue area while radio refills.
  - **Nothing queued.** — shown when there is no upcoming track.

### 2.8 Profile Screen

```
┌─────────────────────────┐
│ ←  Profile              │
│  (A) First Name         │  ← avatar = first letter
│      @username          │  ← when available
│─────────────────────────│
│  My playlists           │
│  [+ New playlist]       │
│─────────────────────────│
│  🎵 Playlist Name   >   │
│     12 songs            │
│  No playlists yet…      │  ← empty state
└─────────────────────────┘
```

- The avatar shows the first letter of the display name (uppercase); it is
  decorative (`aria-hidden`).
- `@username` is shown only when Telegram provides it.
- “New playlist” opens a text sheet; Enter submits.
- Playlist rows show name and track count and open the Playlist screen.
- Empty state: “No playlists yet. Create one to group songs you like.”

### 2.9 Settings Screen

```
┌─────────────────────────┐
│ ←  Settings             │
│─────────────────────────│
│  Theme                  │
│  [ Light ][ Dark ]      │  ← segmented control
│─────────────────────────│
│  Accent color           │
│  ● ● ● ● ●              │  ← 15 swatches, 5 per row
│  ● ● ● ● ●              │
│  ● ● ● ● ●              │
│─────────────────────────│
│  Wallpaper              │
│  [Upload from gallery]  │
│  [Remove wallpaper]     │  ← only when set
│─────────────────────────│
│  History                │
│  [Clear search history] │
│  [Clear play history]   │
│─────────────────────────│
│  Your data              │
│  [Delete all my data]   │  ← danger
└─────────────────────────┘
```

- Theme is a segmented Light/Dark control, not a toggle.
- Accent picker: 15 filled circles, 32×32 px, 5 per row. The selected swatch
  has a white ring plus an outer line so it shows on light backgrounds.
  Selecting one recolors the app immediately and triggers light haptics.
- Wallpaper: upload from gallery and remove. The note states the picture stays
  on this device only and is not uploaded.
- History: clear search history and clear play history, each with a
  confirmation.
- **Your data → Delete all my data**: confirmation is mandatory. It removes
  favorites, playlists, and history; shared playlist links stop working; the
  action cannot be undone. On success the local lists are cleared and a toast
  confirms deletion.

---

## 3. Interaction Patterns

### Search flow

1. Tap the search field → keyboard opens; recent searches are already visible.
2. Type freely → no request, no results, no spinner. Editing hides any previous
   results and returns to Recent searches.
3. Press Enter (or the mobile keyboard Search action) with at least 2
   characters: the query is recorded, a spinner appears, results are requested,
   and the server starts prefetch for that final query only.
4. Results appear only after the response arrives.
5. Tapping a search result records the search if needed, blurs the field, and
   starts radio mode.
6. Tapping a recent search submits it immediately, through the same path as
   Enter.
7. Clearing the field aborts an in-flight request and returns to idle.

Enter with fewer than 2 characters is a no-op and keeps focus in the field. A
duplicate Enter for the same query that is already loading or displayed is also
a no-op (pressing Try again after an error retries).

### Adding to Favorites

- Tap `+` on a search or shared-playlist row.
- The UI updates optimistically with light haptic feedback (`impactOccurred('light')`).
- On API failure the change is rolled back and a toast shows the error.
- Removing a favorite from the `⋯` menu uses the same optimistic pattern.

### Confirmations and feedback

- Destructive actions use `Telegram.WebApp.showConfirm` when available, with
  `window.confirm` as a fallback.
- Success and error feedback use toasts (`role="status"`, `aria-live="polite"`).
- Bot-check errors show a user-friendly “Music service is temporarily
  unavailable” message.
- Failed tracks are skipped automatically. After 3 consecutive failures,
  playback stops and a toast explains why.

### Playlist sharing

1. Open Profile → tap a playlist, or use the Play button's screen.
2. Tap **Share** (or `⋯` → Share).
3. The link comes from the server-provided `share_base` in `/api/me`, not a
   hardcoded bot URL. The frontend appends `playlist_<share_token>`.
4. Telegram's share sheet opens via `openTelegramLink`; if unavailable, the
   link is copied to the clipboard.
5. The recipient opens the Mini App and sees the read-only Shared Playlist
   screen.

---

## 4. Telegram-Specific APIs

```javascript
Telegram.WebApp.ready();
Telegram.WebApp.expand();
Telegram.WebApp.disableVerticalSwipes();

Telegram.WebApp.BackButton.show();
Telegram.WebApp.BackButton.hide();
Telegram.WebApp.BackButton.onClick(handleBack);

Telegram.WebApp.HapticFeedback.impactOccurred('light');
Telegram.WebApp.HapticFeedback.notificationOccurred('success');

Telegram.WebApp.showConfirm(message, callback);
Telegram.WebApp.openTelegramLink(url);

Telegram.WebApp.setHeaderColor(color);
Telegram.WebApp.setBackgroundColor(color);
Telegram.WebApp.setBottomBarColor(color);

const user = Telegram.WebApp.initDataUnsafe.user;
// { id, first_name, username, language_code }

const startParam = Telegram.WebApp.initDataUnsafe.start_param;
// starts with "playlist_" for shared playlists

const initData = Telegram.WebApp.initData;  // sent as "Authorization: tma ..."
```

---

## 5. Wallpaper Behavior

- User uploads an image from the gallery (`<input type="file" accept="image/*">`).
- The image is compressed to a maximum long side of **1280 px**.
- JPEG quality starts at 0.82 and is reduced in 0.12 steps (down to 0.4)
  until the data URL fits the storage limit.
- The maximum stored data URL is about **1.5 million characters**.
- The data URL is stored in `localStorage` under the key `lammot:wallpaper`;
  it is never uploaded to the server.
- It is applied as a fixed background layer. A semi-transparent overlay uses
  `rgba(var(--bg-primary-rgb), var(--wallpaper-overlay))`: **0.88 in light
  theme**, **0.80 in dark theme**.
- The `has-wallpaper` body class controls display of the wallpaper and the
  slightly opaque sticky headers.

---

## 6. Responsive Behavior

- Designed for **mobile only** (Telegram Mini App).
- Min width: 320 px | Max width: 430 px (centered on tablet).
- Safe area insets respected via `--safe-bottom`.
- Bottom bar always above the iOS home indicator.
- The search bar and view headers are sticky; the full-screen player scrolls
  independently with `overscroll-behavior: contain`.

---

## 7. MediaSession API (Background Playback)

```javascript
navigator.mediaSession.metadata = new MediaMetadata({
  title: track.title,
  artist: track.artist,
  artwork: [{ src: track.thumbnail_url, sizes: '544x544', type: 'image/jpeg' }]
});

navigator.mediaSession.setActionHandler('nexttrack', () => next());
navigator.mediaSession.setActionHandler('previoustrack', () => prev());
navigator.mediaSession.setActionHandler('play', () => audio.play());
navigator.mediaSession.setActionHandler('pause', () => audio.pause());
navigator.mediaSession.setActionHandler('seekto', (details) => {
  if (details && Number.isFinite(details.seekTime)) {
    audio.currentTime = details.seekTime;
  }
});

navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
```

Artwork uses `544x544` and `seekto` is handled. Position synchronization via
`navigator.mediaSession.setPositionState` is **not currently implemented**.

---

## 8. Accessibility and States

- Icon-only buttons carry `aria-label`; track rows and the more-actions button
  include the track title in their labels.
- Theme and accent controls use `role="radio"` with `aria-checked`.
- The active bottom tab uses `aria-current="page"`.
- Keyboard focus uses `:focus-visible` with a 2 px accent outline.
- The full-screen player is a `role="dialog"` with `aria-modal="true"`.
- The sheet and full-screen player return focus/navigation through the Telegram
  Back button; Escape triggers the same back behavior.
- States covered by the UI: loading (spinner or dimmed controls), empty,
  error (with retry where applicable), offline (“No connection…”), and
  bot-check (“Music service is temporarily unavailable”).
- Outside Telegram, the home screen shows an “Open Lammot in Telegram” message
  and disables the search field.

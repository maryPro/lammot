# LammotBot — UI Specification

**Telegram Mini App | Language: English**
Version: 1.0

---

## 1. Design Tokens

### Colors

#### Light Theme (default)
```css
--bg-primary: #FFFFFF;
--bg-secondary: #F5F5F5;
--bg-card: #FFFFFF;
--text-primary: #111111;
--text-secondary: #666666;
--border: #E0E0E0;
--shadow: rgba(0,0,0,0.08);
```

#### Dark Theme
```css
--bg-primary: #121212;
--bg-secondary: #1E1E1E;
--bg-card: #242424;
--text-primary: #FFFFFF;
--text-secondary: #AAAAAA;
--border: #333333;
--shadow: rgba(0,0,0,0.4);
```

#### Accent Colors (15 options, user-selectable)
```css
/* Default */
--accent: #BC13FE;  /* 1. Cyberpunk Purple ★ default */

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

### Typography
```css
--font: -apple-system, 'SF Pro Display', 'Roboto', sans-serif;
--font-size-title: 16px;
--font-size-body: 14px;
--font-size-caption: 12px;
--font-weight-bold: 600;
--font-weight-normal: 400;
```

### Spacing & Radius
```css
--radius-card: 12px;
--radius-pill: 999px;
--spacing-xs: 4px;
--spacing-sm: 8px;
--spacing-md: 16px;
--spacing-lg: 24px;
```

---

## 2. Screen Layouts

### 2.1 Main Screen

```
┌─────────────────────────┐
│  🔍 Search music...     │  ← search input, full width, rounded
│  ───────────────────    │  ← divider line
│  Recent Searches        │  ← section label (always visible)
│   • Zemfira             │  ← last 10 queries, tappable
│   • Imagine Dragons     │
│   • Nirvana             │
│   • ...                 │
│                         │
│                         │
│  [mini-player]          │  ← appears here ONLY when music is playing
│─────────────────────────│
│  ⚙️         ⭐       👤 │  ← bottom bar: Settings | Favorites | Profile
└─────────────────────────┘
```

**Bottom bar icons:**
- `⚙️` Settings — left
- `⭐` Favorites — center (primary action)
- `👤` Profile — right

### 2.2 Search Results (while typing)

```
┌─────────────────────────┐
│  🔍 Zemfira          ✕  │  ← clear button appears
│─────────────────────────│
│ [thumbnail] Title    [+]│  ← [+] add to Favorites
│             Artist      │
│             3:45        │
│─────────────────────────│
│ [thumbnail] Title    [✓]│  ← [✓] already in Favorites, no [+]
│             Artist      │
│─────────────────────────│
│ [thumbnail] Title    [+]│
│ ...                     │
│─────────────────────────│
│ 🎵 Now Playing     ⏯ ⏭ │  ← mini-player (if active)
│─────────────────────────│
│  ⚙️         ⭐       👤 │
└─────────────────────────┘
```

**Track card:**
- Thumbnail: 48×48px, rounded 8px
- Title: 14px bold, truncated at 1 line
- Artist: 12px secondary color
- Duration: 12px secondary color, right-aligned
- `[+]` button: accent color, 32×32px circle
- `[✓]` button: greyed out, same size, no action

### 2.3 Favorites Screen (tap ⭐)

```
┌─────────────────────────┐
│ ←  My Favorites         │  ← back arrow + title
│  🔍 Search in playlist  │  ← search within favorites
│─────────────────────────│
│ [thumbnail] Title       │  ← no [+] button (already in favorites)
│             Artist      │
│             3:45        │
│─────────────────────────│
│ [thumbnail] Title       │
│ ...                     │
│─────────────────────────│
│ 🎵 Now Playing     ⏯ ⏭ │
│─────────────────────────│
│  ⚙️         ⭐       👤 │
└─────────────────────────┘
```

**Playback behavior:** Tracks play in saved order. No radio. When last track ends → stops.

### 2.4 Mini-Player

Appears above the bottom bar when a track is playing. Height: 64px.

```
┌─────────────────────────┐
│ [thumb] Title  ⏮  ⏯  ⏭ │
│         Artist          │
│ ━━━━━━━━━━━━━━━━━━━━    │  ← thin progress bar (2px), accent color
└─────────────────────────┘
```

- Tap anywhere on mini-player (except buttons) → opens Full-Screen Player
- Thumbnail: 40×40px, rounded 6px

### 2.5 Full-Screen Player (tap mini-player)

```
┌─────────────────────────┐
│  ↓  (swipe down or ✕)  │  ← close button top right
│                         │
│  ┌─────────────────┐    │
│  │                 │    │
│  │    Artwork      │    │  ← album cover, square, 240×240px, rounded 16px
│  │                 │    │
│  └─────────────────┘    │
│                         │
│  Title                  │  ← 18px bold
│  Artist                 │  ← 14px secondary
│                         │
│  ━━━━━━●────────────    │  ← progress bar / timeline, draggable
│  1:23              3:45 │  ← elapsed / total
│                         │
│      ⏮    ⏯    ⏭       │  ← playback controls
│                         │
│─────────────────────────│
│  Up Next                │  ← queue section
│  1. Next Track       ✕  │
│  2. Track After      ✕  │
│  3. ...              ✕  │
│  4. ...              ✕  │
│  5. ...              ✕  │
└─────────────────────────┘
```

**Queue:**
- Shows next 5 tracks
- `[✕]` removes track from queue
- In Playlist mode: shows next tracks from playlist
- In Radio mode: shows AI-suggested tracks

### 2.6 Profile Screen (tap 👤)

```
┌─────────────────────────┐
│ ←  Profile              │
│  👤 First Name          │  ← Telegram display name
│─────────────────────────│
│  My Playlists           │
│  [+ New Playlist    ]   │  ← create button
│─────────────────────────│
│  🎵 Playlist Name  [→]  │
│     12 tracks           │
│─────────────────────────│
│  🎵 Another Playlist[→] │
│     5 tracks            │
└─────────────────────────┘
```

### 2.7 Settings Screen (tap ⚙️)

```
┌─────────────────────────┐
│ ←  Settings             │
│─────────────────────────│
│  Theme                  │
│  ○ Light    ● Dark      │  ← toggle
│─────────────────────────│
│  Accent Color           │
│  ● ○ ○ ○ ○             │  ← row of 15 color circles
│  ○ ○ ○ ○ ○             │
│  ○ ○ ○ ○ ○             │
│─────────────────────────│
│  Wallpaper              │
│  [Upload from gallery]  │  ← button
│  [Remove wallpaper   ]  │  ← shown only if wallpaper is set
│─────────────────────────│
│  History                │
│  [Clear search history] │
│  [Clear play history  ] │
└─────────────────────────┘
```

**Accent color picker:**
- 15 filled circles, 32×32px each, 3 rows of 5
- Selected circle has white ring border around it
- Tapping any circle → entire app recolors instantly (CSS variable swap)

---

## 3. Interaction Patterns

### Search flow
1. Tap search field → keyboard opens + "Recent Searches" is already visible
2. Start typing → results appear in real-time (debounce 300ms)
3. Tap a result → track starts playing immediately
4. `[+]` button → track added to Favorites, button changes to `[✓]`

### Adding to Favorites
- From search results: tap `[+]` on track card
- Haptic feedback on add: `Telegram.WebApp.HapticFeedback.impactOccurred('light')`

### Playlist sharing
1. Open Profile → tap playlist → three-dot menu `...`
2. Tap "Share" → copy link `t.me/LammotBot/app?startapp=playlist_{token}`
3. Recipient opens link → Telegram opens Mini App → loads shared playlist in read-only view

---

## 4. Telegram-Specific APIs

```javascript
// Prevent accidental close on swipe down
Telegram.WebApp.disableVerticalSwipes();

// Expand to full height
Telegram.WebApp.expand();

// Haptic feedback when adding to favorites
Telegram.WebApp.HapticFeedback.impactOccurred('light');

// Read Telegram user identity
const user = Telegram.WebApp.initDataUnsafe.user;
// { id, first_name, username, language_code }

// Theme sync (optional)
const colorScheme = Telegram.WebApp.colorScheme; // 'light' | 'dark'
```

---

## 5. Wallpaper Behavior

- User uploads image from gallery (via `<input type="file" accept="image/*">`)
- Image stored as base64 in `localStorage` (no server upload needed)
- Applied as `background-image` on the app shell
- Overlay: semi-transparent layer (`rgba(var(--bg-primary-rgb), 0.85)`) so text remains readable
- In Light theme: overlay opacity 0.88
- In Dark theme: overlay opacity 0.80

---

## 6. Responsive Behavior

- Designed for **mobile only** (Telegram Mini App)
- Min width: 320px | Max width: 430px (centered on tablet)
- Safe area insets respected: `padding-bottom: env(safe-area-inset-bottom)`
- Bottom bar always above iOS home indicator

---

## 7. MediaSession API (Background Playback)

```javascript
navigator.mediaSession.metadata = new MediaMetadata({
  title: track.title,
  artist: track.artist,
  artwork: [{ src: track.thumbnail, sizes: '96x96', type: 'image/jpeg' }]
});

navigator.mediaSession.setActionHandler('nexttrack', () => playNext());
navigator.mediaSession.setActionHandler('previoustrack', () => playPrev());
navigator.mediaSession.setActionHandler('play', () => audio.play());
navigator.mediaSession.setActionHandler('pause', () => audio.pause());
```

Lock screen and notification controls work automatically.

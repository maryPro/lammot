/*
 * LammotBot — Telegram Mini App music player (frontend)
 *
 * Plain JavaScript, no build step. Served by FastAPI from /frontend.
 * Every API call sends Telegram initData ("Authorization: tma ...");
 * the server verifies it and takes the user id from it.
 *
 * Security note: all text coming from the API (titles, names) is inserted
 * with textContent, never innerHTML.
 */
'use strict';

(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  const doc = document;
  const root = doc.documentElement;
  const $ = (id) => doc.getElementById(id);

  // ------------------------------------------------------------ constants
  const ACCENTS = [
    ['Cyberpunk Purple', '#BC13FE'], ['Deep Violet', '#8B5CF6'], ['Neon Pink', '#FF2D78'],
    ['Electric Blue', '#00D4FF'], ['Matrix Green', '#00FF41'], ['Cyber Orange', '#FF6B00'],
    ['Neon Red', '#FF073A'], ['Gold', '#FFD700'], ['Mint', '#00CED1'],
    ['Lavender', '#E040FB'], ['Copper', '#FF7F50'], ['Emerald', '#50C878'],
    ['Indigo', '#6610F2'], ['Ocean', '#006994'], ['Silver', '#C0C0C0'],
  ];
  const DEFAULT_ACCENT = '#BC13FE';
  const SEARCH_MIN_CHARS = 2;
  const QUEUE_VISIBLE = 5;
  const PREFETCH_SECONDS = 15;     // resolve the next song this long before the end
  const RADIO_BATCH = 5;
  const MAX_FAILED_IN_ROW = 3;
  const LINK_LIFETIME_MS = 5 * 60 * 60 * 1000;  // server signs audio links for 6 h
  const WALLPAPER_MAX_SIDE = 1280;
  const WALLPAPER_MAX_CHARS = 1500000;           // ~1.1 MB of base64 in localStorage

  // ------------------------------------------------------------ icons (static SVG)
  const ICONS = {
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    more: '<circle cx="5" cy="12" r="1.6" class="solid"/><circle cx="12" cy="12" r="1.6" class="solid"/><circle cx="19" cy="12" r="1.6" class="solid"/>',
    back: '<path d="M15 5l-7 7 7 7"/>',
    chevron: '<path d="M9 5l7 7-7 7"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4.5l3 1.5"/>',
    music: '<path d="M9 18V5.5l11-2V16"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
    star: '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
    listAdd: '<path d="M4 6h12M4 12h12M4 18h7M18 14v7M14.5 17.5h7"/>',
    playNext: '<path d="M4 6h10M4 12h10M4 18h6"/><path d="M15 15.5v6l5-3z" class="solid"/>',
    share: '<path d="M12 4v11M7.5 8.5L12 4l4.5 4.5"/><path d="M5 13v5a2 2 0 002 2h10a2 2 0 002-2v-5"/>',
    trash: '<path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13"/>',
    play: '<path d="M8 5.5v13l11-6.5z" class="solid"/>',
    scissors: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><path d="M8 8l11 11M8 16L19 5"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="M20.5 16l-5-5-8.5 8.5"/>',
  };

  function icon(name) {
    const svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', 'icon');
    svg.innerHTML = ICONS[name];  // constant markup from ICONS only
    return svg;
  }

  // ------------------------------------------------------------ DOM helper
  function h(tag, props, ...children) {
    const el = doc.createElement(tag);
    if (props) {
      for (const [key, value] of Object.entries(props)) {
        if (value == null || value === false) continue;
        if (key === 'class') el.className = value;
        else if (key === 'text') el.textContent = value;
        else if (key.startsWith('on') && typeof value === 'function') {
          el.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (value === true) el.setAttribute(key, '');
        else el.setAttribute(key, String(value));
      }
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      el.append(child instanceof Node ? child : doc.createTextNode(String(child)));
    }
    return el;
  }

  function safeImageUrl(url) {
    return typeof url === 'string' && url.startsWith('https://') ? url : '';
  }

  function thumb(url, size) {
    const wrap = h('span', { class: size === 40 ? 'thumb-wrap thumb-40' : 'thumb-wrap' });
    const src = safeImageUrl(url);
    if (src) {
      const img = h('img', { class: 'thumb', src, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' });
      img.addEventListener('error', () => img.replaceWith(icon('music')), { once: true });
      wrap.append(img);
    } else {
      wrap.append(icon('music'));
    }
    return wrap;
  }

  function fmtTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
    const s = Math.floor(seconds);
    const hours = Math.floor(s / 3600);
    const mins = Math.floor((s % 3600) / 60);
    const secs = String(s % 60).padStart(2, '0');
    return hours ? `${hours}:${String(mins).padStart(2, '0')}:${secs}` : `${mins}:${secs}`;
  }

  let toastTimer = 0;
  function toast(message) {
    const el = $('toast');
    el.textContent = message;
    el.classList.add('is-visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('is-visible'), 2800);
  }

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === 'success') tg.HapticFeedback.notificationOccurred('success');
      else tg.HapticFeedback.impactOccurred('light');
    } catch (_) { /* older clients */ }
  }

  function confirmAction(message) {
    return new Promise((resolve) => {
      try {
        if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast('6.2')) {
          tg.showConfirm(message, (ok) => resolve(Boolean(ok)));
          return;
        }
      } catch (_) { /* fall back below */ }
      resolve(window.confirm(message));
    });
  }

  // ------------------------------------------------------------ local settings
  const store = {
    get(key, fallback) {
      try {
        const v = localStorage.getItem('lammot:' + key);
        return v === null ? fallback : v;
      } catch (_) { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem('lammot:' + key, value); return true; } catch (_) { return false; }
    },
    remove(key) {
      try { localStorage.removeItem('lammot:' + key); } catch (_) { /* ignore */ }
    },
  };

  function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function applyTheme(theme) {
    const t = theme === 'dark' ? 'dark' : 'light';
    root.dataset.theme = t;
    const bg = t === 'dark' ? '#121212' : '#FFFFFF';
    try { if (tg && tg.setHeaderColor) tg.setHeaderColor(bg); } catch (_) { /* old client */ }
    try { if (tg && tg.setBackgroundColor) tg.setBackgroundColor(bg); } catch (_) { /* old client */ }
    try { if (tg && tg.setBottomBarColor) tg.setBottomBarColor(bg); } catch (_) { /* old client */ }
  }

  function applyAccent(hex) {
    const valid = ACCENTS.some(([, value]) => value === hex) ? hex : DEFAULT_ACCENT;
    const [r, g, b] = hexToRgb(valid);
    // Relative luminance → dark text on light accents (Gold, Silver, Matrix Green…)
    const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
    root.style.setProperty('--accent', valid);
    root.style.setProperty('--on-accent', lum > 0.6 ? '#111111' : '#FFFFFF');
    root.style.setProperty('--accent-soft', `rgba(${r}, ${g}, ${b}, 0.14)`);
    root.style.setProperty('--accent-glow', `rgba(${r}, ${g}, ${b}, 0.5)`);
    return valid;
  }

  function applyWallpaper(dataUrl) {
    const el = $('wallpaper');
    if (dataUrl && dataUrl.startsWith('data:image/')) {
      el.style.backgroundImage = `url("${dataUrl}")`;
      doc.body.classList.add('has-wallpaper');
    } else {
      el.style.backgroundImage = '';
      doc.body.classList.remove('has-wallpaper');
    }
  }

  async function compressImage(file) {
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((resolve, reject) => {
        const i = new Image();
        i.onload = () => resolve(i);
        i.onerror = () => reject(new Error('decode'));
        i.src = url;
      });
      const scale = Math.min(1, WALLPAPER_MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
      const canvas = doc.createElement('canvas');
      canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
      canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
      let quality = 0.82;
      let out = canvas.toDataURL('image/jpeg', quality);
      while (out.length > WALLPAPER_MAX_CHARS && quality > 0.4) {
        quality -= 0.12;
        out = canvas.toDataURL('image/jpeg', quality);
      }
      return out;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  // ------------------------------------------------------------ API client
  let initData = '';

  class ApiError extends Error {
    constructor(status, message, code) {
      super(message);
      this.status = status;
      this.code = code;
    }
  }

  function errorMessage(status, detail, code) {
    if (code === 'bot_check') return 'Music service is temporarily unavailable. Please try again later.';
    if (status === 401) return 'Your session has expired. Close Lammot and open it again from the bot.';
    if (status === 429) return 'Too many requests. Wait a minute and try again.';
    if (status === 404 && detail) return detail;
    if (status >= 500) return 'The music service is not responding. Try again in a moment.';
    return detail || 'Something went wrong. Try again.';
  }

  async function api(path, { method = 'GET', body, signal, auth = true } = {}) {
    const headers = {};
    if (auth) headers.Authorization = 'tma ' + initData;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    let res;
    try {
      res = await fetch(path, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal,
      });
    } catch (err) {
      if (err && err.name === 'AbortError') throw err;
      throw new ApiError(0, 'No connection. Check your internet and try again.');
    }
    if (res.status === 204) return null;
    let payload = null;
    try { payload = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      const detail = payload && typeof payload.detail === 'string' ? payload.detail : '';
      const code = payload && typeof payload.code === 'string' ? payload.code : '';
      throw new ApiError(res.status, errorMessage(res.status, detail, code), code);
    }
    return payload ? payload.data : null;
  }

  function trackBody(t) {
    return {
      video_id: t.video_id,
      title: (t.title || '').slice(0, 300),
      artist: (t.artist || '').slice(0, 200),
      duration_seconds: Math.max(0, Math.min(86400, Math.round(t.duration_seconds || 0))),
      thumbnail_url: safeImageUrl(t.thumbnail_url),
    };
  }

  // ------------------------------------------------------------ app state
  const TrimRange = window.LammotTrim;
  if (!TrimRange) throw new Error('trim-range.js failed to load');

  const state = {
    user: null,
    shareBase: null,
    favorites: new Map(),        // video_id → track
    trims: new Map(),            // video_id → { start_seconds, end_seconds }
    trimPreview: null,           // active trim sheet preview session
    playlists: null,             // null = not loaded yet
    recent: [],
    search: { q: '', status: 'idle', results: [], error: '' },
    searchType: window.LammotSearchTabs
      ? window.LammotSearchTabs.normalizeTab('songs')
      : 'songs',
    viewStack: [],               // [{ name, params }]
  };

  const isFavorite = (videoId) => state.favorites.has(videoId);

  // ------------------------------------------------------------ favorites
  async function addFavorite(track, button) {
    if (isFavorite(track.video_id)) return;
    state.favorites.set(track.video_id, track);
    haptic('light');
    refreshFavoriteButtons(track.video_id);
    try {
      await api('/api/favorites', { method: 'POST', body: trackBody(track) });
    } catch (err) {
      state.favorites.delete(track.video_id);
      refreshFavoriteButtons(track.video_id);
      toast(err.message);
    }
    if (button) button.blur();
  }

  async function removeFavorite(track) {
    if (!isFavorite(track.video_id)) return;
    const saved = state.favorites.get(track.video_id);
    state.favorites.delete(track.video_id);
    refreshFavoriteButtons(track.video_id);
    rerenderViewIf('favorites');
    try {
      await api(`/api/favorites/${encodeURIComponent(track.video_id)}`, { method: 'DELETE' });
      toast('Removed from Favorites');
    } catch (err) {
      state.favorites.set(track.video_id, saved);
      refreshFavoriteButtons(track.video_id);
      rerenderViewIf('favorites');
      toast(err.message);
    }
  }

  function favButton(track) {
    const btn = h('button', { class: 'fav-btn', type: 'button', 'data-fav': track.video_id });
    setFavButton(btn, track);
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!isFavorite(track.video_id)) addFavorite(track, btn);
    });
    return btn;
  }

  function setFavButton(btn, track) {
    const fav = isFavorite(track.video_id);
    btn.classList.toggle('is-fav', fav);
    btn.replaceChildren(icon(fav ? 'check' : 'plus'));
    btn.setAttribute('aria-label', fav ? 'In Favorites' : `Add ${track.title} to Favorites`);
    btn.setAttribute('aria-disabled', fav ? 'true' : 'false');
  }

  function refreshFavoriteButtons(videoId) {
    doc.querySelectorAll('[data-fav]').forEach((btn) => {
      if (btn.dataset.fav !== videoId) return;
      const track = state.favorites.get(videoId) || { video_id: videoId, title: '' };
      setFavButton(btn, track);
    });
  }

  // ------------------------------------------------------------ track rows
  /**
   * opts.variant: 'search' | 'favorites' | 'playlist' | 'shared'
   * opts.onPlay(): starts playback
   * opts.playlistId: for "Remove from this playlist"
   */
  function trackRow(track, opts) {
    const main = h('button', {
      class: 'track-main',
      type: 'button',
      'aria-label': `Play ${track.title} by ${track.artist}`,
      onClick: opts.onPlay,
    },
    thumb(track.thumbnail_url),
    h('span', { class: 'track-text' },
      h('span', { class: 'track-title', text: track.title || 'Untitled' }),
      h('span', { class: 'track-artist', text: track.artist || 'Unknown artist' })),
    track.kind === 'video' ? h('span', { class: 'track-badge', text: 'Video' }) : null,
    track.duration_seconds ? h('span', { class: 'track-dur', text: fmtTime(track.duration_seconds) }) : null);

    const row = h('li', { class: 'track', 'data-track': track.video_id }, main);
    if (player.current && player.current.video_id === track.video_id) row.classList.add('is-current');
    if (opts.variant === 'search' || opts.variant === 'shared') row.append(favButton(track));

    const more = h('button', { class: 'icon-btn more-btn', type: 'button', 'aria-label': `More actions for ${track.title}` }, icon('more'));
    more.addEventListener('click', (e) => { e.stopPropagation(); openTrackMenu(track, opts); });
    row.append(more);
    return row;
  }

  function markCurrentRows() {
    const id = player.current && player.current.video_id;
    doc.querySelectorAll('.track[data-track]').forEach((row) => {
      row.classList.toggle('is-current', row.dataset.track === id);
    });
  }

  function openTrackMenu(track, opts) {
    const items = [];
    if (player.current) {
      items.push({ label: 'Play next', icon: 'playNext', onSelect: () => playNext(track) });
    }
    items.push({ label: 'Trim playback', icon: 'scissors', onSelect: () => openTrimSheet(track) });
    items.push({ label: 'Add to playlist', icon: 'listAdd', onSelect: () => chooseTrackPlaylist(track) });
    if (isFavorite(track.video_id)) {
      items.push({ label: 'Remove from Favorites', icon: 'star', danger: opts.variant === 'favorites', onSelect: () => removeFavorite(track) });
    } else {
      items.push({ label: 'Add to Favorites', icon: 'star', onSelect: () => addFavorite(track) });
    }
    if (opts.variant === 'playlist' && opts.playlistId) {
      items.push({
        label: 'Remove from this playlist',
        icon: 'trash',
        danger: true,
        onSelect: () => removeFromPlaylist(opts.playlistId, track),
      });
    }
    openSheet({ title: track.title, subtitle: track.artist, items });
  }

  // ------------------------------------------------------------ sheets
  function closeSheet() {
    const session = state.trimPreview;
    state.trimPreview = null;
    const backdrop = $('sheet');
    backdrop.hidden = true;
    backdrop.replaceChildren();
    updateBackButton();
    if (session) {
      stopTrimPreview(session);
      if (session.wasPlaying) audio.play().catch(() => {});
    }
  }

  function showSheet(panel) {
    const backdrop = $('sheet');
    backdrop.replaceChildren(panel);
    backdrop.hidden = false;
    backdrop.onclick = (e) => { if (e.target === backdrop) closeSheet(); };
    updateBackButton();
  }

  function openSheet({ title, subtitle, items }) {
    const panel = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('div', { class: 'sheet-title', text: title }),
      subtitle ? h('div', { class: 'sheet-sub', text: subtitle }) : null,
      items.map((item) => h('button', {
        class: 'sheet-item' + (item.danger ? ' danger' : ''),
        type: 'button',
        onClick: () => { closeSheet(); item.onSelect(); },
      }, icon(item.icon), h('span', { text: item.label }))));
    showSheet(panel);
  }

  function askText({ title, placeholder, action }) {
    return new Promise((resolve) => {
      const input = h('input', { class: 'sheet-input', type: 'text', maxlength: '100', placeholder, 'aria-label': title });
      const submit = () => {
        const value = input.value.trim();
        if (!value) { input.focus(); return; }
        closeSheet();
        resolve(value);
      };
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
      const panel = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
        h('div', { class: 'sheet-title', text: title }),
        input,
        h('button', { class: 'btn btn-accent btn-block', type: 'button', onClick: submit, text: action }));
      const backdrop = $('sheet');
      showSheet(panel);
      backdrop.onclick = (e) => { if (e.target === backdrop) { closeSheet(); resolve(null); } };
      setTimeout(() => input.focus(), 50);
    });
  }

  // ------------------------------------------------------------ playback trims
  function trackDuration(track) {
    if (player.current && player.current.video_id === track.video_id && audio.src && !isSilent()) {
      return playbackDuration();
    }
    const value = Number(track.duration_seconds);
    return Number.isFinite(value) && value > 0 ? value : 0;
  }

  function trimmedTrackDuration(track) {
    const full = Number(track.duration_seconds);
    if (!(full > 0)) return 0;
    const trim = TrimRange.normalizeTrim(state.trims.get(track.video_id), full);
    return trim.valid ? trim.end - trim.start : full;
  }

  function stopTrimPreview(session) {
    const preview = (session && session.previewAudio) || $('trim-preview');
    try { preview.pause(); } catch (_) { /* ignore */ }
    preview.removeAttribute('src');
    try { preview.load(); } catch (_) { /* ignore */ }
  }

  function applyTrimChange(track) {
    if (!player.current || player.current.video_id !== track.video_id) return;
    const trim = currentTrim();
    player.startAt = trim.valid ? trim.start : 0;
    if (trim.valid && (audio.currentTime < trim.start || audio.currentTime >= trim.end)) {
      try { audio.currentTime = trim.start; } catch (_) { /* wait for metadata */ }
    }
    renderProgress(segmentPosition(), segmentDuration());
    syncMediaSessionPosition(true);
  }

  function openTrimSheet(track) {
    const full = trackDuration(track);
    const saved = state.trims.get(track.video_id) || null;
    let start = saved ? Number(saved.start_seconds) : 0;
    let end = saved && saved.end_seconds != null ? Number(saved.end_seconds) : full;
    if (!Number.isFinite(start)) start = 0;
    if (!Number.isFinite(end)) end = full;
    start = Math.min(Math.max(start, 0), full);
    end = Math.min(Math.max(end, 0), full);

    const rangeLabel = h('div', { class: 'trim-range-label' });
    const hint = h('p', { class: 'trim-hint' });
    const startInput = h('input', {
      type: 'range', class: 'trim-range', min: '0', max: String(full), step: '0.5',
      value: String(start), 'aria-label': 'Trim start',
    });
    const endInput = h('input', {
      type: 'range', class: 'trim-range', min: '0', max: String(full), step: '0.5',
      value: String(end), 'aria-label': 'Trim end',
    });
    const previewLabel = h('span', { text: 'Preview' });
    const previewBtn = h('button', { class: 'btn', type: 'button' }, icon('play'), previewLabel);
    const saveBtn = h('button', { class: 'btn btn-accent btn-block', type: 'button', text: 'Save' });
    const resetBtn = saved
      ? h('button', { class: 'btn btn-block', type: 'button', text: 'Reset trim' })
      : null;
    const cancelBtn = h('button', { class: 'btn btn-block', type: 'button', text: 'Cancel' });

    const session = {
      track,
      wasPlaying: !audio.paused && !isSilent(),
      previewAudio: $('trim-preview'),
      previewStart: start,
      previewEnd: end,
    };
    state.trimPreview = session;
    if (session.wasPlaying) {
      try { audio.pause(); } catch (_) { /* ignore */ }
    }

    let previewLoading = false;

    const readInputs = () => {
      start = Number(startInput.value);
      end = Number(endInput.value);
    };
    const sync = () => {
      readInputs();
      rangeLabel.textContent = `${fmtTime(start)} – ${fmtTime(end)}`;
      const error = TrimRange.validateTrim(start, end, full);
      saveBtn.disabled = Boolean(error);
      previewBtn.disabled = Boolean(error) || previewLoading;
      hint.textContent = error || '';
    };

    startInput.addEventListener('input', sync);
    endInput.addEventListener('input', sync);
    cancelBtn.addEventListener('click', () => closeSheet());

    previewBtn.addEventListener('click', async () => {
      readInputs();
      const error = TrimRange.validateTrim(start, end, full);
      if (error) { hint.textContent = error; return; }
      stopTrimPreview(session);
      previewLoading = true;
      previewBtn.disabled = true;
      previewLabel.textContent = 'Loading preview…';
      try {
        const url = await streamLink(track, preferredFormat);
        if (state.trimPreview !== session) return;
        session.previewStart = start;
        session.previewEnd = end;
        const preview = session.previewAudio;
        preview.src = url;
        const play = () => {
          if (state.trimPreview !== session) return;
          try { preview.currentTime = start; } catch (_) { /* ignore */ }
          preview.play().catch(() => toast('Preview could not start'));
        };
        if (preview.readyState >= 1) play();
        else preview.addEventListener('loadedmetadata', play, { once: true });
      } catch (err) {
        if (state.trimPreview === session) toast(err.message);
      } finally {
        previewLoading = false;
        previewLabel.textContent = 'Preview';
        if (state.trimPreview === session) sync();
      }
    });

    saveBtn.addEventListener('click', async () => {
      readInputs();
      const error = TrimRange.validateTrim(start, end, full);
      if (error) { hint.textContent = error; return; }
      stopTrimPreview(session);
      saveBtn.disabled = true;
      try {
        const row = await api(`/api/trims/${encodeURIComponent(track.video_id)}`, {
          method: 'PUT',
          body: {
            start_seconds: Math.round(start * 10) / 10,
            end_seconds: Math.round(end * 10) / 10,
          },
        });
        state.trims.set(track.video_id, row);
        applyTrimChange(track);
        haptic('success');
        toast('Trim saved');
        closeSheet();
      } catch (err) {
        saveBtn.disabled = false;
        toast(err.message);
      }
    });

    if (resetBtn) {
      resetBtn.addEventListener('click', async () => {
        stopTrimPreview(session);
        resetBtn.disabled = true;
        try {
          await api(`/api/trims/${encodeURIComponent(track.video_id)}`, { method: 'DELETE' });
          state.trims.delete(track.video_id);
          applyTrimChange(track);
          toast('Trim removed');
          closeSheet();
        } catch (err) {
          resetBtn.disabled = false;
          toast(err.message);
        }
      });
    }

    if (!(full > 0)) {
      rangeLabel.textContent = '0:00 – 0:00';
      startInput.disabled = true;
      endInput.disabled = true;
      previewBtn.disabled = true;
      saveBtn.disabled = true;
      hint.textContent = 'Track duration unavailable';
    } else {
      sync();
    }

    const panel = h('div', { class: 'sheet trim-sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Trim playback' },
      h('div', { class: 'sheet-title', text: 'Trim playback' }),
      h('div', { class: 'sheet-sub', text: `${track.title || 'Untitled'}${track.artist ? ' · ' + track.artist : ''}` }),
      rangeLabel,
      h('label', { class: 'trim-range-row' },
        h('span', { class: 'trim-range-name', text: 'Start' }), startInput),
      h('label', { class: 'trim-range-row' },
        h('span', { class: 'trim-range-name', text: 'End' }), endInput),
      hint,
      h('div', { class: 'trim-actions' }, previewBtn),
      saveBtn,
      resetBtn,
      cancelBtn);
    showSheet(panel);
  }

  // ------------------------------------------------------------ playlists
  async function loadPlaylists() {
    state.playlists = await api('/api/playlists');
    return state.playlists;
  }

  async function createPlaylist() {
    const name = await askText({ title: 'New playlist', placeholder: 'Playlist name', action: 'Create playlist' });
    if (!name) return null;
    try {
      const pl = await api('/api/playlists', { method: 'POST', body: { name } });
      state.playlists = [pl, ...(state.playlists || [])];
      toast(`Created “${pl.name}”`);
      rerenderViewIf('profile');
      return pl;
    } catch (err) {
      toast(err.message);
      return null;
    }
  }

  async function chooseTrackPlaylist(track) {
    try {
      if (!state.playlists) await loadPlaylists();
    } catch (err) {
      toast(err.message);
      return;
    }
    const items = state.playlists.map((pl) => ({
      label: pl.name,
      icon: 'music',
      onSelect: () => addToPlaylist(pl, track),
    }));
    items.push({
      label: 'New playlist…',
      icon: 'plus',
      onSelect: async () => {
        const pl = await createPlaylist();
        if (pl) addToPlaylist(pl, track);
      },
    });
    openSheet({ title: 'Add to playlist', subtitle: track.title, items });
  }

  async function addToPlaylist(pl, track) {
    try {
      const res = await api(`/api/playlists/${pl.id}/tracks`, { method: 'POST', body: trackBody(track) });
      if (res && res.added) {
        pl.track_count = (pl.track_count || 0) + 1;
        haptic('light');
        toast(`Added to “${pl.name}”`);
      } else {
        toast(`Already in “${pl.name}”`);
      }
    } catch (err) {
      toast(err.message);
    }
  }

  async function removeFromPlaylist(playlistId, track) {
    try {
      await api(`/api/playlists/${playlistId}/tracks/${encodeURIComponent(track.video_id)}`, { method: 'DELETE' });
      toast('Removed from playlist');
      const pl = (state.playlists || []).find((p) => p.id === playlistId);
      if (pl && pl.track_count > 0) pl.track_count -= 1;
      const v = currentView();
      if (v && v.name === 'playlist' && v.params.id === playlistId) {
        delete v.params.pl;
        renderView(v);
      }
    } catch (err) {
      toast(err.message);
    }
  }

  function shareLink(pl) {
    return state.shareBase ? `${state.shareBase}playlist_${pl.share_token}` : null;
  }

  async function sharePlaylist(pl) {
    const link = shareLink(pl);
    if (!link) {
      toast('Sharing is not set up on the server yet (bot username unknown).');
      return;
    }
    const shareUrl = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(`Listen to “${pl.name}” on Lammot`)}`;
    try {
      if (tg && tg.openTelegramLink) { tg.openTelegramLink(shareUrl); return; }
    } catch (_) { /* fall back to clipboard */ }
    try {
      await navigator.clipboard.writeText(link);
      toast('Link copied');
    } catch (_) {
      toast(link);
    }
  }

  async function deletePlaylist(pl) {
    const ok = await confirmAction(`Delete “${pl.name}”? Its share link will stop working.`);
    if (!ok) return;
    try {
      await api(`/api/playlists/${pl.id}`, { method: 'DELETE' });
      state.playlists = (state.playlists || []).filter((p) => p.id !== pl.id);
      toast('Playlist deleted');
      goBack();
    } catch (err) {
      toast(err.message);
    }
  }

  // ------------------------------------------------------------ search (home)
  const searchInput = $('search-input');
  const SearchQuery = window.LammotSearchQuery;
  if (!SearchQuery) throw new Error('search-query.js failed to load');
  const SearchTabs = window.LammotSearchTabs;
  if (!SearchTabs) throw new Error('search-tabs.js failed to load');
  let searchController = null;
  let searchSeq = 0;

  function rememberSearch(query) {
    const q = query.trim();
    if (!q) return;
    state.recent = [q, ...state.recent.filter((r) => r.toLowerCase() !== q.toLowerCase())].slice(0, 10);
    api('/api/history', { method: 'POST', body: { kind: 'search', query: q } }).catch(() => {});
  }

  function onSearchInput() {
    $('search-clear').hidden = searchInput.value.length === 0;
    if (searchController) searchController.abort();
    searchSeq += 1;
    state.search = SearchQuery.idleStateForQuery(searchInput.value);
    renderHome();
  }

  async function submitSearch(raw, { remember = true, force = false } = {}) {
    const q = force
      ? SearchQuery.normalizeQuery(raw)
      : SearchQuery.shouldSubmitQuery(state.search, raw, SEARCH_MIN_CHARS);
    if (q === null) return;
    if (force && !SearchQuery.canSubmitQuery(q, SEARCH_MIN_CHARS)) return;
    if (searchController) searchController.abort();
    const seq = ++searchSeq;
    prefetchQueue.length = 0;  // drop warm-ups for the previous query
    searchController = new AbortController();
    state.search = { q, status: 'loading', results: [], error: '' };
    if (remember) rememberSearch(q);
    renderHome();
    try {
      const type = SearchTabs.apiType(state.searchType);
      const results = await api(`/api/search?q=${encodeURIComponent(q)}&limit=20&format=${preferredFormat}&type=${type}`, { signal: searchController.signal });
      if (seq !== searchSeq) return;
      state.search = { q, status: 'done', results: results || [], error: '' };
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      if (seq !== searchSeq) return;
      state.search = { q, status: 'error', results: [], error: err.message };
    }
    renderHome();
  }

  function updateSearchTabs() {
    for (const [id, type] of [['search-tab-songs', 'songs'], ['search-tab-videos', 'videos']]) {
      const tab = $(id);
      if (tab) tab.setAttribute('aria-selected', String(state.searchType === type));
    }
  }

  function setSearchType(type) {
    const next = SearchTabs.normalizeTab(type);
    if (next === state.searchType) return;
    state.searchType = next;
    updateSearchTabs();
    if (searchInput.disabled) return;  // outside Telegram: keep the notice
    const s = state.search;
    if (s.status === 'done' || s.status === 'loading' || s.status === 'error') {
      submitSearch(s.q, { remember: false, force: true });
    } else {
      renderHome();
    }
  }

  function clearSearch() {
    searchInput.value = '';
    onSearchInput();
    searchInput.focus();
  }

  function renderHome() {
    const body = $('home-body');
    const s = state.search;
    updateSearchTabs();

    if (s.status === 'idle') {
      const section = h('div', { class: 'section' }, h('h2', { class: 'section-label', text: 'Recent searches' }));
      if (state.recent.length) {
        section.append(h('ul', { class: 'recent-list' }, state.recent.map((q) => h('li', null,
          h('button', {
            class: 'recent-item',
            type: 'button',
            onClick: () => {
              searchInput.value = q;
              $('search-clear').hidden = false;
              submitSearch(q);
            },
          }, icon('clock'), h('span', { text: q }))))));
      } else {
        section.append(h('p', { class: 'state-msg', text: 'Search for a song or an artist. Your recent searches will show up here.' }));
      }
      body.replaceChildren(section);
      return;
    }

    if (s.status === 'loading' && !s.results.length) {
      body.replaceChildren(h('div', { class: 'spinner', role: 'progressbar', 'aria-label': 'Searching' }));
      return;
    }

    if (s.status === 'error') {
      body.replaceChildren(h('div', { class: 'state-msg' },
        h('strong', { text: 'Search failed' }),
        s.error,
        h('br'),
        h('button', { class: 'retry-btn', type: 'button', text: 'Try again', onClick: () => submitSearch(s.q, { remember: false }) })));
      return;
    }

    if (!s.results.length) {
      if (state.searchType === 'videos') {
        body.replaceChildren(h('div', { class: 'state-msg' },
          h('strong', { text: `Nothing found for “${s.q}”` }),
          'No videos under 15:00 found. Try a different query or switch back to Songs.'));
      } else {
        body.replaceChildren(h('div', { class: 'state-msg' },
          h('strong', { text: `Nothing found for “${s.q}”` }),
          'Check the spelling or try the artist name.',
          h('br'),
          h('button', {
            class: 'retry-btn',
            type: 'button',
            text: 'Search Extended',
            onClick: () => setSearchType('videos'),
          })));
      }
      return;
    }

    const list = h('ul', {
      class: 'track-list',
      'aria-label': state.searchType === 'videos' ? 'Extended video results' : 'Song results',
    },
      s.results.map((track) => trackRow(track, {
        variant: 'search',
        onPlay: () => {
          searchInput.blur();
          playRadio(track);
        },
      })));
    body.replaceChildren(list);
  }

  // ------------------------------------------------------------ views (screens)
  const VIEW_RENDERERS = {};

  function currentView() {
    return state.viewStack[state.viewStack.length - 1] || null;
  }

  function openView(name, params = {}, { asRoot = false } = {}) {
    if (asRoot) state.viewStack = [];
    state.viewStack.push({ name, params });
    showCurrentView();
  }

  function goBack() {
    state.viewStack.pop();
    showCurrentView();
  }

  function rerenderViewIf(name) {
    const v = currentView();
    if (v && v.name === name) renderView(v);
  }

  function showCurrentView() {
    const v = currentView();
    $('screen-home').hidden = Boolean(v);
    $('screen-view').hidden = !v;
    doc.querySelectorAll('.tab').forEach((tab) => {
      if (v && v.name === tab.dataset.tab) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    });
    if (v) renderView(v);
    window.scrollTo(0, 0);
    updateBackButton();
  }

  function renderView(v) {
    const container = $('screen-view');
    const renderer = VIEW_RENDERERS[v.name];
    container.replaceChildren(...renderer(v.params));
  }

  function viewHeader(title, subtitle, extra) {
    return h('header', { class: 'view-header' },
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Back', onClick: goBack }, icon('back')),
      h('h1', { class: 'view-title' }, title, subtitle ? h('span', { class: 'view-subtitle', text: subtitle }) : null),
      extra || null);
  }

  // Loads data once per render; shows a spinner meanwhile, then re-renders.
  function withData(key, loader, renderBody) {
    const v = currentView();
    if (v.params[key] === undefined) {
      v.params[key] = null;
      loader().then((data) => {
        v.params[key] = { ok: true, data };
      }).catch((err) => {
        v.params[key] = { ok: false, error: err.message };
      }).finally(() => {
        if (currentView() === v) renderView(v);
      });
    }
    const loaded = v.params[key];
    if (!loaded) return h('div', { class: 'spinner', role: 'progressbar', 'aria-label': 'Loading' });
    if (!loaded.ok) {
      return h('div', { class: 'state-msg' },
        h('strong', { text: 'Could not load this page' }),
        loaded.error,
        h('br'),
        h('button', {
          class: 'retry-btn',
          type: 'button',
          text: 'Try again',
          onClick: () => { delete v.params[key]; renderView(v); },
        }));
    }
    return renderBody(loaded.data);
  }

  // --- Favorites
  VIEW_RENDERERS.favorites = (params) => {
    const header = viewHeader('My Favorites');
    const body = h('div', { class: 'screen-body' });

    const content = withData('favs', async () => {
      const favs = await api('/api/favorites');
      state.favorites = new Map(favs.map((t) => [t.video_id, t]));
      return true;
    }, () => {
      const all = Array.from(state.favorites.values());
      if (!all.length) {
        return h('div', { class: 'state-msg' },
          h('strong', { text: 'No favorites yet' }),
          'Tap + next to a song in search to save it here.');
      }
      const field = h('div', { class: 'search-field filter-field' }, icon('search'));
      const input = h('input', {
        type: 'text',
        placeholder: 'Search in Favorites',
        'aria-label': 'Search in Favorites',
        value: params.filter || '',
        autocomplete: 'off',
      });
      field.append(input);
      const list = h('ul', { class: 'track-list' });
      const fill = () => {
        const f = (params.filter || '').toLowerCase();
        const items = f ? all.filter((t) => `${t.title} ${t.artist}`.toLowerCase().includes(f)) : all;
        list.replaceChildren(...items.map((track, i) => trackRow(track, {
          variant: 'favorites',
          onPlay: () => playList(items, i, 'Favorites'),
        })));
        if (!items.length) list.append(h('li', { class: 'state-msg', text: 'No songs match your search.' }));
      };
      input.addEventListener('input', () => { params.filter = input.value; fill(); });
      fill();
      return h('div', null, field, list);
    });

    body.append(content);
    return [header, body];
  };

  // --- Profile
  VIEW_RENDERERS.profile = () => {
    const header = viewHeader('Profile');
    const body = h('div', { class: 'screen-body' });

    const u = state.user || {};
    const name = u.first_name || 'Telegram user';
    body.append(h('div', { class: 'profile-card' },
      h('div', { class: 'avatar', 'aria-hidden': 'true', text: name.trim().charAt(0).toUpperCase() || '?' }),
      h('div', null,
        h('div', { class: 'profile-name', text: name }),
        u.username ? h('div', { class: 'profile-handle', text: '@' + u.username }) : null)));

    const section = h('div', { class: 'section' },
      h('h2', { class: 'section-label', text: 'My playlists' }),
      h('button', { class: 'btn btn-block', type: 'button', onClick: createPlaylist }, icon('plus'), 'New playlist'));

    section.append(withData('pls', loadPlaylists, () => {
      const pls = state.playlists || [];
      if (!pls.length) {
        return h('p', { class: 'state-msg', text: 'No playlists yet. Create one to group songs you like.' });
      }
      return h('div', null, pls.map((pl) => h('button', {
        class: 'playlist-row',
        type: 'button',
        onClick: () => openView('playlist', { id: pl.id }),
      },
      h('span', { class: 'playlist-icon' }, icon('music')),
      h('span', { class: 'playlist-text' },
        h('span', { class: 'playlist-name', text: pl.name }),
        h('span', { class: 'playlist-count', text: pl.track_count === 1 ? '1 song' : `${pl.track_count} songs` })),
      icon('chevron'))));
    }));

    body.append(section);
    return [header, body];
  };

  // --- One of my playlists
  VIEW_RENDERERS.playlist = (params) => {
    const body = h('div', { class: 'screen-body' });
    let pl = null;
    let count = 0;

    body.append(withData('pl', () => api(`/api/playlists/${params.id}`), (data) => {
      pl = data.playlist;
      const tracks = data.tracks;
      count = tracks.length;

      const actions = h('div', { class: 'btn-row' },
        h('button', {
          class: 'btn btn-accent',
          type: 'button',
          disabled: tracks.length === 0,
          onClick: () => playList(tracks, 0, pl.name),
        }, icon('play'), 'Play'),
        h('button', { class: 'btn', type: 'button', onClick: () => sharePlaylist(pl) }, icon('share'), 'Share'));

      if (!tracks.length) {
        return h('div', null, actions, h('p', {
          class: 'state-msg',
          text: 'This playlist is empty. Find a song in search, tap ⋯ and choose “Add to playlist”.',
        }));
      }
      return h('div', null, actions, h('ul', { class: 'track-list' }, tracks.map((track, i) => trackRow(track, {
        variant: 'playlist',
        playlistId: pl.id,
        onPlay: () => playList(tracks, i, pl.name),
      }))));
    }));

    if (!pl) return [viewHeader('Playlist'), body];
    const current = pl;
    const menu = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Playlist actions' }, icon('more'));
    menu.addEventListener('click', () => openSheet({
      title: current.name,
      items: [
        { label: 'Share', icon: 'share', onSelect: () => sharePlaylist(current) },
        { label: 'Delete playlist', icon: 'trash', danger: true, onSelect: () => deletePlaylist(current) },
      ],
    }));
    return [viewHeader(current.name, count === 1 ? '1 song' : `${count} songs`, menu), body];
  };

  // --- Someone else's playlist, opened from a share link (read-only)
  VIEW_RENDERERS.shared = (params) => {
    const body = h('div', { class: 'screen-body' });
    let name = '';

    body.append(withData('pl', () => api(`/api/playlists/shared/${encodeURIComponent(params.token)}`, { auth: false }), (data) => {
      name = data.name;
      const tracks = data.tracks;
      if (!tracks.length) return h('p', { class: 'state-msg', text: 'This playlist is empty.' });
      return h('div', null,
        h('div', { class: 'btn-row' }, h('button', {
          class: 'btn btn-accent',
          type: 'button',
          onClick: () => playList(tracks, 0, data.name),
        }, icon('play'), 'Play')),
        h('ul', { class: 'track-list' }, tracks.map((track, i) => trackRow(track, {
          variant: 'shared',
          onPlay: () => playList(tracks, i, data.name),
        }))));
    }));

    return [name ? viewHeader(name, 'Shared playlist') : viewHeader('Shared playlist'), body];
  };

  // --- Settings
  VIEW_RENDERERS.settings = () => {
    const header = viewHeader('Settings');
    const body = h('div', { class: 'screen-body' });

    // Theme
    const theme = root.dataset.theme;
    const themeBtn = (value, label) => h('button', {
      type: 'button',
      role: 'radio',
      'aria-checked': String(theme === value),
      onClick: () => {
        store.set('theme', value);
        applyTheme(value);
        rerenderViewIf('settings');
      },
      text: label,
    });
    body.append(h('div', { class: 'section' },
      h('h2', { class: 'section-label', id: 'theme-label', text: 'Theme' }),
      h('div', { class: 'segmented', role: 'radiogroup', 'aria-labelledby': 'theme-label' },
        themeBtn('light', 'Light'), themeBtn('dark', 'Dark'))));

    // Accent color
    const accent = store.get('accent', DEFAULT_ACCENT);
    const swatches = h('div', { class: 'swatches', role: 'radiogroup', 'aria-labelledby': 'accent-label' },
      ACCENTS.map(([name, hex]) => {
        const sw = h('button', {
          class: 'swatch',
          type: 'button',
          role: 'radio',
          'aria-checked': String(hex === accent),
          'aria-label': name,
          title: name,
          onClick: () => {
            store.set('accent', hex);
            applyAccent(hex);
            haptic('light');
            swatches.querySelectorAll('.swatch').forEach((el) => el.setAttribute('aria-checked', String(el === sw)));
          },
        });
        sw.style.setProperty('--swatch', hex);
        return sw;
      }));
    body.append(h('div', { class: 'section' },
      h('h2', { class: 'section-label', id: 'accent-label', text: 'Accent color' }), swatches));

    // Wallpaper
    const hasWallpaper = Boolean(store.get('wallpaper', ''));
    const fileInput = h('input', { type: 'file', accept: 'image/*', hidden: true });
    fileInput.addEventListener('change', async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      try {
        const dataUrl = await compressImage(file);
        if (!store.set('wallpaper', dataUrl)) {
          toast('This image is too large to save. Try a smaller one.');
          return;
        }
        applyWallpaper(dataUrl);
        toast('Wallpaper set');
        rerenderViewIf('settings');
      } catch (_) {
        toast('Could not read this image. Try a JPEG or PNG.');
      }
    });
    body.append(h('div', { class: 'section' },
      h('h2', { class: 'section-label', text: 'Wallpaper' }),
      h('div', { class: 'settings-actions' },
        h('button', { class: 'btn btn-block', type: 'button', onClick: () => fileInput.click() }, icon('image'), 'Upload from gallery'),
        hasWallpaper ? h('button', {
          class: 'btn btn-block',
          type: 'button',
          onClick: () => {
            store.remove('wallpaper');
            applyWallpaper('');
            rerenderViewIf('settings');
          },
          text: 'Remove wallpaper',
        }) : null,
        fileInput),
      h('p', { class: 'settings-note', text: 'The picture stays on this device only. It is not uploaded.' })));

    // History
    const clearHistory = async (type, label) => {
      const ok = await confirmAction(`Clear your ${label}?`);
      if (!ok) return;
      try {
        await api(`/api/history?type=${type}`, { method: 'DELETE' });
        if (type === 'search') { state.recent = []; renderHome(); }
        toast(`${label.charAt(0).toUpperCase()}${label.slice(1)} cleared`);
      } catch (err) {
        toast(err.message);
      }
    };
    body.append(h('div', { class: 'section' },
      h('h2', { class: 'section-label', text: 'History' }),
      h('div', { class: 'settings-actions' },
        h('button', { class: 'btn btn-block', type: 'button', text: 'Clear search history', onClick: () => clearHistory('search', 'search history') }),
        h('button', { class: 'btn btn-block', type: 'button', text: 'Clear play history', onClick: () => clearHistory('play', 'play history') }))));

    // Your data
    body.append(h('div', { class: 'section' },
      h('h2', { class: 'section-label', text: 'Your data' }),
      h('button', {
        class: 'btn btn-block btn-danger',
        type: 'button',
        text: 'Delete all my data',
        onClick: deleteMyData,
      }),
      h('p', {
        class: 'settings-note',
        text: 'Removes your favorites, playlists and history from Lammot. Shared playlist links stop working.',
      })));

    return [header, body];
  };

  async function deleteMyData() {
    const ok = await confirmAction('Delete all your favorites, playlists and history? This cannot be undone.');
    if (!ok) return;
    try {
      await api('/api/me?confirm=true', { method: 'DELETE' });
      state.favorites.clear();
      state.trims.clear();
      state.playlists = [];
      state.recent = [];
      renderHome();
      haptic('success');
      toast('Your data has been deleted');
    } catch (err) {
      toast(err.message);
    }
  }

  // ------------------------------------------------------------ player
  const audio = $('audio');
  const AUDIO_FORMAT = (() => {
    try {
      if (window.LammotAudioFormat) {
        return window.LammotAudioFormat.chooseFormat({
          canPlayType: (type) => audio.canPlayType(type),
          userAgent: navigator.userAgent,
          platform: navigator.platform,
          maxTouchPoints: navigator.maxTouchPoints,
        });
      }
    } catch (_) { /* the safest format wins */ }
    return 'aac';
  })();
  // Survives per-track resets: once a format is known to work here, every
  // later track and prefetch asks for it instead of paying a second resolve.
  let preferredFormat = AUDIO_FORMAT;
  const { pickDuration, positionState, progressRatio, shouldForceTrackEnd } = window.LammotTime;
  const PlaybackView = window.LammotPlaybackView;

  // A play() call made directly inside a tap "unlocks" the <audio> element;
  // after that, play() also works after awaiting the network (next songs,
  // auto-advance). The first tap plays 0.1 s of silence for that.
  const SILENT_WAV = 'data:audio/wav;base64,UklGRkQDAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YSADAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgA==';
  let audioUnlocked = false;
  const isSilent = () => audio.src.startsWith('data:');

  function unlockAudio() {
    if (audioUnlocked) return;
    audioUnlocked = true;
    try {
      audio.src = SILENT_WAV;
      const p = audio.play();
      if (p && p.then) p.then(() => { if (isSilent()) audio.pause(); }).catch(() => {});
    } catch (_) { /* ignore */ }
  }

  const player = {
    current: null,
    queue: [],          // upcoming tracks
    history: [],        // previously played (for ⏮)
    mode: 'radio',      // 'radio' (search) | 'list' (favorites / playlist)
    source: '',
    radioKind: 'song',  // 'song' | 'video' — kind of the current radio seed
    loadSeq: 0,
    links: new Map(),   // `${video_id}:${format}` → { url, expires }
    inflight: new Map(), // `${video_id}:${format}` → { promise, prefetch }
    refilling: false,
    recorded: false,
    format: preferredFormat,
    triedFallback: false,
    failedInRow: 0,
    prefetchedFor: null,
    botCheck: false,
    loading: false,
    playing: false,
    seeking: false,
    forcedEnd: false,
    startAt: 0,
  };

  // Track metadata is the source of truth: some iOS webviews report a
  // doubled media duration. Fall back to the media element only when the
  // track has no duration or playback runs past it.
  function playbackDuration() {
    return pickDuration(audio.duration, player.current && player.current.duration_seconds, audio.currentTime);
  }

  // The trim sheet and progress UI use the selected segment as the visible
  // range: 0:00 is the trim start and the total is the trimmed length.
  function currentTrim() {
    const raw = player.current ? state.trims.get(player.current.video_id) : null;
    return TrimRange.normalizeTrim(raw, playbackDuration());
  }

  function segmentDuration() {
    const full = playbackDuration();
    const trim = currentTrim();
    return trim.valid ? trim.end - trim.start : full;
  }

  function segmentPosition() {
    const trim = currentTrim();
    return trim.valid ? Math.max(0, audio.currentTime - trim.start) : audio.currentTime;
  }

  function applyStartSeek() {
    const start = player.startAt || 0;
    const seq = player.loadSeq;
    if (!start || !player.current) return;
    const seek = () => {
      if (seq !== player.loadSeq) return;
      try {
        if (player.current && audio.currentTime < start) audio.currentTime = start;
      } catch (_) { /* iOS rejects seeks before metadata */ }
    };
    if (audio.readyState >= 1) seek();
    else audio.addEventListener('loadedmetadata', seek, { once: true });
  }

  async function streamLink(track, format, { prefetch = false } = {}) {
    const key = `${track.video_id}:${format}`;
    const cached = player.links.get(key);
    if (cached && cached.expires > Date.now()) return cached.url;
    const pending = player.inflight.get(key);
    // Share a request of the same or higher priority.  A tap must not wait
    // behind a background warm-up, so it starts its own interactive request.
    if (pending && (prefetch || !pending.prefetch)) return pending.promise;

    const warm = prefetch ? '&prefetch=1' : '';
    const request = api(`/api/stream/${encodeURIComponent(track.video_id)}?format=${format}${warm}`)
      .then((data) => {
        player.links.set(key, { url: data.stream_url, expires: Date.now() + LINK_LIFETIME_MS });
        return data.stream_url;
      })
      .finally(() => {
        const current = player.inflight.get(key);
        if (current && current.promise === request) player.inflight.delete(key);
      });
    player.inflight.set(key, { promise: request, prefetch });
    return request;
  }

  // Prefetch is bounded: a tap must never queue behind background work, and
  // extra resolves raise the risk of a YouTube bot check.
  const PREFETCH_LIMIT = 1;
  const prefetchQueue = [];
  let prefetchActive = 0;

  function runPrefetchQueue() {
    if (player.botCheck) { prefetchQueue.length = 0; return; }
    while (prefetchActive < PREFETCH_LIMIT && prefetchQueue.length) {
      const track = prefetchQueue.shift();
      prefetchActive += 1;
      streamLink(track, preferredFormat, { prefetch: true })
        .catch((err) => {
          if (err instanceof ApiError && err.code === 'bot_check') {
            player.botCheck = true;
            prefetchQueue.length = 0;
          }
        })
        .finally(() => {
          prefetchActive -= 1;
          runPrefetchQueue();
        });
    }
  }

  function prefetchTrack(track, format = preferredFormat) {
    if (!track || !track.video_id || player.botCheck) return;
    if (format !== preferredFormat) return streamLink(track, format, { prefetch: true }).catch(() => {});
    const key = `${track.video_id}:${format}`;
    const cached = player.links.get(key);
    if (cached && cached.expires > Date.now()) return;
    if (player.inflight.has(key)) return;
    if (prefetchQueue.some((t) => t.video_id === track.video_id)) return;
    prefetchQueue.push(track);
    runPrefetchQueue();
  }

  function setLoading(on) {
    player.loading = on;
    $('mini-player').classList.toggle('is-loading', on);
    $('full-player').classList.toggle('is-loading', on);
    renderPlayState();
  }

  async function startTrack(track, { remember = true } = {}) {
    unlockAudio();  // must run before the first await, while still inside the tap
    prefetchQueue.length = 0;  // a tap must not compete with queued warm-ups
    const seq = ++player.loadSeq;
    if (remember && player.current) {
      player.history.push(player.current);
      if (player.history.length > 50) player.history.shift();
    }
    player.current = track;
    player.recorded = false;
    player.forcedEnd = false;
    player.playing = false;
    player.format = preferredFormat;
    player.triedFallback = false;
    player.prefetchedFor = null;
    const startTrim = TrimRange.normalizeTrim(
      state.trims.get(track.video_id), Number(track.duration_seconds) || 0
    );
    player.startAt = startTrim.valid ? startTrim.start : 0;
    renderPlayer();
    markCurrentRows();
    updateMediaSession();
    setLoading(true);

    try {
      const url = await streamLink(track, player.format);
      if (seq !== player.loadSeq) return;
      audio.src = url;
      applyStartSeek();
      await audio.play();
      if (seq === player.loadSeq) player.playing = true;
      player.failedInRow = 0;
      player.botCheck = false;
    } catch (err) {
      if (seq !== player.loadSeq) return;
      if (err && err.name === 'NotAllowedError') {
        // Autoplay blocked: wait for the user to press play
        renderPlayState();
      } else if (err && err.name === 'AbortError') {
        // A newer track replaced this one
      } else if (err && err.name === 'NotSupportedError') {
        if (fallbackInFlight(seq) || player.format === 'opus') {
          if (!(await fallbackToAac(seq, 0, true)) && seq === player.loadSeq) handleTrackFailure(seq);
        } else {
          handleTrackFailure(seq);
        }
      } else if (err instanceof ApiError) {
        handleTrackFailure(seq, err.message, err.code);
      }
      // Media decode/network errors arrive via the 'error' event
    } finally {
      if (seq === player.loadSeq) setLoading(false);
    }
    refillRadio(false);
  }

  function handleTrackFailure(seq, message, code) {
    if (code === 'bot_check') {
      player.botCheck = true;
      player.failedInRow = 0;
      try { audio.pause(); } catch (_) { /* ignore */ }
      toast('Music service is temporarily unavailable. Please try again later.');
      return;
    }
    player.botCheck = false;
    player.failedInRow += 1;
    if (player.failedInRow >= MAX_FAILED_IN_ROW) {
      toast('Playback stopped: several songs in a row could not be loaded.');
      player.failedInRow = 0;
      return;
    }
    toast(message || 'This song cannot be played. Skipping to the next one.');
    setTimeout(() => {
      if (seq === player.loadSeq) next({ auto: true });
    }, 1200);
  }

  let fallbackAttempt = null;   // { seq, promise } — one AAC retry per load
  const MEDIA_ERR_SRC_NOT_SUPPORTED = 4;
  const fallbackInFlight = (seq) => Boolean(fallbackAttempt && fallbackAttempt.seq === seq);

  async function fallbackToAac(seq, at, persistFormat) {
    if (fallbackInFlight(seq)) return fallbackAttempt.promise;
    const promise = (async () => {
      player.triedFallback = true;
      player.format = 'aac';
      player.prefetchedFor = null;
      try {
        const url = await streamLink(player.current, 'aac');
        if (seq !== player.loadSeq) return true;
        audio.src = url;
        const trim = currentTrim();
        let resumeAt = Math.max(at || 0, player.startAt || 0);
        if (trim.valid) resumeAt = Math.min(Math.max(resumeAt, trim.start), trim.end);
        if (resumeAt) {
          try { audio.currentTime = resumeAt; } catch (_) { /* wait for metadata */ }
        }
        await audio.play();
        if (seq === player.loadSeq) player.playing = true;
        // Only a stream that actually plays may change the session format
        if (persistFormat) preferredFormat = 'aac';
        return true;
      } catch (err) {
        if (seq !== player.loadSeq) return true;
        if (err instanceof ApiError && err.code === 'bot_check') {
          handleTrackFailure(seq, err.message, err.code);
          return true;
        }
        return false;
      }
    })();
    const clear = () => {
      if (fallbackAttempt && fallbackAttempt.promise === promise) fallbackAttempt = null;
    };
    fallbackAttempt = { seq, promise };
    promise.then(clear, clear);
    return promise;
  }

  async function onMediaError() {
    if (!player.current || !audio.src || isSilent()) return;
    player.playing = false;
    renderPlayState();
    const seq = player.loadSeq;
    // Some webviews claim WebM/Opus support but fail: retry once as AAC.
    // Network/decode failures retry too, but must not pin the session format.
    const codecError = !audio.error || audio.error.code === MEDIA_ERR_SRC_NOT_SUPPORTED;
    if (fallbackInFlight(seq) || (!player.triedFallback && player.format === 'opus')) {
      const at = audio.currentTime || 0;
      if (await fallbackToAac(seq, at, codecError)) return;
    }
    if (seq === player.loadSeq) handleTrackFailure(seq);
  }

  function playRadio(track) {
    player.mode = 'radio';
    player.radioKind = SearchTabs.isVideoTrack(track) ? 'video' : 'song';
    player.source = player.radioKind === 'video' ? 'Radio: similar videos' : 'Radio: similar songs';
    player.queue = [];
    // startTrack() refills the queue after playback starts, so the radio
    // resolve never competes with the track the user just tapped.
    startTrack(track);
  }

  function playList(tracks, index, sourceName) {
    player.mode = 'list';
    player.source = `From ${sourceName}`;
    player.queue = tracks.slice(index + 1);
    startTrack(tracks[index]);
  }

  function playNext(track) {
    player.queue = player.queue.filter((t) => t.video_id !== track.video_id);
    player.queue.unshift(track);
    renderQueue();
    toast('Will play next');
  }

  async function refillRadio(force) {
    if (player.mode !== 'radio' || player.refilling || !player.current) return;
    if (!force && player.queue.length >= 2) return;
    player.refilling = true;
    const seed = player.queue.length ? player.queue[player.queue.length - 1] : player.current;
    const exclude = [...new Set([
      player.current.video_id,
      ...player.queue.map((t) => t.video_id),
      ...player.history.slice(-20).map((t) => t.video_id),
    ])].filter((id) => id !== seed.video_id).slice(0, 50);
    const qs = new URLSearchParams({
      limit: String(RADIO_BATCH),
      kind: player.radioKind === 'video' ? 'video' : 'song',
    });
    exclude.forEach((id) => qs.append('exclude', id));
    try {
      const more = await api(`/api/radio/${encodeURIComponent(seed.video_id)}?${qs}`);
      if (player.mode !== 'radio') return;
      const have = new Set([player.current && player.current.video_id, ...player.queue.map((t) => t.video_id)]);
      for (const t of more || []) {
        if (!have.has(t.video_id)) { player.queue.push(t); have.add(t.video_id); }
      }
      renderQueue();
    } catch (err) {
      if (force) toast(err.message);
    } finally {
      player.refilling = false;
    }
  }

  async function next({ auto = false } = {}) {
    if (!player.current) return;
    if (!player.queue.length && player.mode === 'radio') await refillRadio(true);
    const upcoming = player.queue.shift();
    if (!upcoming) {
      if (auto) {
        audio.pause();
        renderPlayState();
      } else {
        toast(player.mode === 'list' ? 'This was the last song in the list.' : 'No more songs in the queue.');
      }
      renderQueue();
      return;
    }
    startTrack(upcoming);
  }

  function prev() {
    if (!player.current) return;
    if (audio.currentTime > 3 || !player.history.length) {
      const trim = currentTrim();
      audio.currentTime = trim.valid ? trim.start : 0;
      return;
    }
    const previous = player.history.pop();
    player.queue.unshift(player.current);
    startTrack(previous, { remember: false });
  }

  function togglePlay() {
    if (!player.current || player.loading) return;
    if (audio.paused) {
      if (!audio.src || isSilent()) { startTrack(player.current, { remember: false }); return; }
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  }

  function jumpTo(index) {
    const skipped = player.queue.splice(0, index + 1);
    const target = skipped.pop();
    startTrack(target);
  }

  function removeFromQueue(index) {
    player.queue.splice(index, 1);
    renderQueue();
    refillRadio(false);
  }

  // --- player rendering
  function setImage(img, url) {
    const src = safeImageUrl(url);
    if (src) img.setAttribute('src', src);
    else img.removeAttribute('src');
  }

  function renderPlayer() {
    const t = player.current;
    const mini = $('mini-player');
    mini.hidden = !t;
    doc.body.classList.toggle('has-mini', Boolean(t));
    if (!t) return;
    $('mini-title').textContent = t.title || 'Untitled';
    $('mini-artist').textContent = t.artist || '';
    $('mini-thumb').setAttribute('referrerpolicy', 'no-referrer');
    setImage($('mini-thumb'), t.thumbnail_url);
    $('fp-title').textContent = t.title || 'Untitled';
    $('fp-artist').textContent = t.artist || '';
    $('fp-art').setAttribute('referrerpolicy', 'no-referrer');
    setImage($('fp-art'), t.thumbnail_url);
    $('fp-total').textContent = fmtTime(trimmedTrackDuration(t));
    renderProgress(0, trimmedTrackDuration(t));
    renderPlayState();
    renderQueue();
  }

  function renderPlayState() {
    const state = PlaybackView.playbackUiState({
      loading: player.loading,
      playing: player.playing,
    });
    const playing = state === 'playing';
    const loading = state === 'loading';
    const busy = PlaybackView.isPlaybackBusy(state);
    const label = PlaybackView.playbackAriaLabel(state);
    $('mini-player').classList.toggle('is-playing', playing);
    $('full-player').classList.toggle('is-playing', playing);
    $('mini-player').classList.toggle('is-loading', loading);
    $('full-player').classList.toggle('is-loading', loading);
    for (const id of ['mini-play', 'fp-play']) {
      const button = $(id);
      button.setAttribute('aria-label', label);
      button.setAttribute('aria-busy', busy ? 'true' : 'false');
      button.setAttribute('aria-disabled', busy ? 'true' : 'false');
    }
    try {
      if ('mediaSession' in navigator) navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
    } catch (_) { /* ignore */ }
  }

  function renderProgress(current, duration) {
    const ratio = progressRatio(current, duration);
    $('mini-progress').style.transform = `scaleX(${ratio})`;
    if (!player.seeking) {
      const seek = $('fp-seek');
      seek.value = String(Math.round(ratio * 1000));
      seek.style.setProperty('--fill', `${ratio * 100}%`);
      $('fp-elapsed').textContent = fmtTime(current);
    }
    if (duration > 0) $('fp-total').textContent = fmtTime(duration);
  }

  function renderQueue() {
    const list = $('fp-queue');
    $('fp-source').textContent = player.current ? player.source : '';
    const upcoming = player.queue.slice(0, QUEUE_VISIBLE);
    if (!upcoming.length) {
      list.replaceChildren(h('li', {
        class: 'state-msg',
        text: player.mode === 'radio' && player.refilling ? 'Finding similar songs…' : 'Nothing queued.',
      }));
      return;
    }
    list.replaceChildren(...upcoming.map((t, i) => h('li', { class: 'queue-item' },
      h('button', {
        class: 'track-main',
        type: 'button',
        'aria-label': `Play ${t.title}`,
        onClick: () => jumpTo(i),
      },
      thumb(t.thumbnail_url, 40),
      h('span', { class: 'track-text' },
        h('span', { class: 'track-title', text: t.title || 'Untitled' }),
        h('span', { class: 'track-artist', text: t.artist || '' }))),
      h('button', {
        class: 'icon-btn',
        type: 'button',
        'aria-label': `Remove ${t.title} from the queue`,
        onClick: () => removeFromQueue(i),
      }, icon('close')))));
  }

  // --- media session (lock screen controls)
  const MEDIA_POSITION_SYNC_MS = 900;
  let lastPositionSync = 0;

  // The lock screen timeline is fed by setPositionState.  Without it iOS
  // falls back to the media element duration, which is doubled on iOS.
  function applyPositionState(duration, position, force) {
    if (!('mediaSession' in navigator)
        || typeof navigator.mediaSession.setPositionState !== 'function') return;
    const state = positionState(duration, position, audio.playbackRate);
    if (!state) return;
    const now = Date.now();
    if (!force && now - lastPositionSync < MEDIA_POSITION_SYNC_MS) return;
    lastPositionSync = now;
    try { navigator.mediaSession.setPositionState(state); } catch (_) { /* unsupported */ }
  }

  function syncMediaSessionPosition(force) {
    if (!player.current || isSilent()) return;
    applyPositionState(segmentDuration(), segmentPosition(), force);
  }

  function updateMediaSession() {
    if (!('mediaSession' in navigator) || !player.current) return;
    const t = player.current;
    try {
      const artwork = safeImageUrl(t.thumbnail_url)
        ? [{ src: t.thumbnail_url, sizes: '544x544', type: 'image/jpeg' }]
        : [];
      navigator.mediaSession.metadata = new MediaMetadata({ title: t.title, artist: t.artist, artwork });
    } catch (_) { /* unsupported */ }
    // A new track starts at zero; reset the system timeline immediately
    // instead of waiting for the first timeupdate.
    applyPositionState(trimmedTrackDuration(t), 0, true);
  }

  function bindMediaSession() {
    if (!('mediaSession' in navigator)) return;
    const handlers = {
      play: () => audio.play().catch(() => {}),
      pause: () => audio.pause(),
      nexttrack: () => next(),
      previoustrack: () => prev(),
      seekto: (d) => {
        if (!d || !Number.isFinite(d.seekTime)) return;
        const full = playbackDuration();
        const raw = player.current ? state.trims.get(player.current.video_id) : null;
        const trim = currentTrim();
        const target = trim.valid ? trim.start + d.seekTime : d.seekTime;
        audio.currentTime = TrimRange.clampToTrim(target, raw, full);
      },
    };
    for (const [action, fn] of Object.entries(handlers)) {
      try { navigator.mediaSession.setActionHandler(action, fn); } catch (_) { /* unsupported action */ }
    }
  }

  // --- full-screen player open/close
  function openFullPlayer() {
    if (!player.current) return;
    const fp = $('full-player');
    fp.hidden = false;
    fp.classList.add('is-entering');
    requestAnimationFrame(() => requestAnimationFrame(() => fp.classList.remove('is-entering')));
    fp.scrollTop = 0;
    renderQueue();
    updateBackButton();
  }

  function closeFullPlayer() {
    const fp = $('full-player');
    if (fp.hidden) return;
    fp.classList.add('is-entering');
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setTimeout(() => {
      fp.hidden = true;
      fp.classList.remove('is-entering');
      $('fp-panel').style.transform = '';
      updateBackButton();
    }, reduce ? 0 : 260);
  }

  function bindSwipeDown() {
    const fp = $('full-player');
    const panel = $('fp-panel');
    let startY = null;
    let dy = 0;
    fp.addEventListener('touchstart', (e) => {
      if (fp.scrollTop > 0 || e.target.closest('#fp-seek, .fp-queue')) { startY = null; return; }
      startY = e.touches[0].clientY;
      dy = 0;
    }, { passive: true });
    fp.addEventListener('touchmove', (e) => {
      if (startY === null) return;
      dy = Math.max(0, e.touches[0].clientY - startY);
      panel.style.transition = 'none';
      panel.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    fp.addEventListener('touchend', () => {
      if (startY === null) return;
      panel.style.transition = '';
      panel.style.transform = '';
      if (dy > 110) closeFullPlayer();
      startY = null;
    });
  }

  // ------------------------------------------------------------ Telegram back button
  function updateBackButton() {
    if (!tg || !tg.BackButton) return;
    const needed = !$('full-player').hidden || !$('sheet').hidden || state.viewStack.length > 0;
    try {
      if (needed) tg.BackButton.show();
      else tg.BackButton.hide();
    } catch (_) { /* old client */ }
  }

  function handleBack() {
    if (!$('sheet').hidden) { closeSheet(); return; }
    if (!$('full-player').hidden) { closeFullPlayer(); return; }
    if (state.viewStack.length) goBack();
  }

  // ------------------------------------------------------------ wiring
  function bindStaticUI() {
    searchInput.addEventListener('input', onSearchInput);
    searchInput.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return;
      e.preventDefault();
      const q = SearchQuery.normalizeQuery(searchInput.value);
      if (!SearchQuery.canSubmitQuery(q, SEARCH_MIN_CHARS)) return;
      submitSearch(q);
      searchInput.blur();
    });
    $('search-clear').addEventListener('click', clearSearch);

    for (const [id, type] of [['search-tab-songs', 'songs'], ['search-tab-videos', 'videos']]) {
      const tab = $(id);
      if (tab) tab.addEventListener('click', () => setSearchType(type));
    }

    doc.querySelectorAll('.tab').forEach((tab) => {
      tab.addEventListener('click', () => {
        const v = currentView();
        if (v && state.viewStack.length === 1 && v.name === tab.dataset.tab) {
          goBack();  // tapping the open tab again returns to search
          return;
        }
        openView(tab.dataset.tab, {}, { asRoot: true });
      });
    });

    $('mini-open').addEventListener('click', openFullPlayer);
    $('mini-play').addEventListener('click', togglePlay);
    $('mini-next').addEventListener('click', () => next());
    $('mini-prev').addEventListener('click', prev);
    $('fp-play').addEventListener('click', togglePlay);
    $('fp-next').addEventListener('click', () => next());
    $('fp-prev').addEventListener('click', prev);
    $('fp-close').addEventListener('click', closeFullPlayer);
    bindSwipeDown();

    const seek = $('fp-seek');
    seek.addEventListener('input', () => {
      player.seeking = true;
      const duration = segmentDuration();
      const ratio = Number(seek.value) / 1000;
      seek.style.setProperty('--fill', `${ratio * 100}%`);
      $('fp-elapsed').textContent = fmtTime(ratio * duration);
    });
    seek.addEventListener('change', () => {
      const ratio = Number(seek.value) / 1000;
      const full = playbackDuration();
      if (full > 0) {
        const raw = player.current ? state.trims.get(player.current.video_id) : null;
        audio.currentTime = TrimRange.seekTime(ratio, raw, full);
      }
      player.seeking = false;
    });

    audio.addEventListener('play', renderPlayState);
    audio.addEventListener('pause', () => {
      player.playing = false;
      renderPlayState();
      syncMediaSessionPosition(true);
    });
    audio.addEventListener('playing', () => {
      if (isSilent()) return;
      player.playing = true;
      setLoading(false);
      renderPlayState();
      // iOS ignores next/previous handlers registered before playback is
      // active and shows its own ±10 s seek buttons instead.  Register them
      // again now that the session is active.
      bindMediaSession();
      syncMediaSessionPosition(true);
      if (player.current && !player.recorded) {
        player.recorded = true;
        api('/api/history', { method: 'POST', body: { kind: 'play', track: trackBody(player.current) } }).catch(() => {});
      }
    });
    audio.addEventListener('timeupdate', () => {
      if (isSilent()) return;
      const full = playbackDuration();
      const rawTrim = player.current ? state.trims.get(player.current.video_id) : null;
      const trim = currentTrim();
      if (!player.loading && rawTrim && trim.valid && audio.currentTime >= trim.end - 0.25) {
        // The selected segment ends here; iOS may still tick through the
        // source tail, so use the same single-shot guard as the doubled-
        // duration path.
        if (!player.forcedEnd) {
          player.forcedEnd = true;
          next({ auto: true });
        }
        return;
      }
      const track = player.current && player.current.duration_seconds;
      if (!player.loading && shouldForceTrackEnd(audio.duration, track, audio.currentTime)) {
        // iOS reports a doubled duration and keeps ticking through silence
        // after the real end, so "ended" may never arrive on time.
        if (!player.forcedEnd) {
          player.forcedEnd = true;
          next({ auto: true });
        }
        return;
      }
      renderProgress(segmentPosition(), segmentDuration());
      syncMediaSessionPosition(false);
      // Lazy resolve: get the next song's link ~15 s before this one ends
      const upcoming = player.queue[0];
      const remaining = trim.valid ? trim.end - audio.currentTime : full - audio.currentTime;
      if (upcoming && !player.botCheck && (trim.valid || full > 0)
          && remaining < PREFETCH_SECONDS
          && player.prefetchedFor !== upcoming.video_id) {
        player.prefetchedFor = upcoming.video_id;
        streamLink(upcoming, preferredFormat, { prefetch: true }).catch(() => {});
      }
    });
    for (const evt of ['loadedmetadata', 'durationchange']) {
      audio.addEventListener(evt, () => {
        if (isSilent()) return;
        renderProgress(segmentPosition(), segmentDuration());
        syncMediaSessionPosition(true);
      });
    }
    audio.addEventListener('loadedmetadata', applyStartSeek);
    audio.addEventListener('seeked', () => syncMediaSessionPosition(true));
    audio.addEventListener('ended', () => {
      if (isSilent()) return;
      player.playing = false;
      next({ auto: true });
    });
    audio.addEventListener('error', onMediaError);

    const preview = $('trim-preview');
    preview.addEventListener('timeupdate', () => {
      const session = state.trimPreview;
      if (!session || session.previewEnd == null) return;
      if (preview.currentTime >= session.previewEnd - 0.2) {
        try { preview.pause(); } catch (_) { /* ignore */ }
      }
    });
    preview.addEventListener('error', () => {
      if (state.trimPreview && preview.getAttribute('src')) {
        toast('Preview is unavailable for this track.');
      }
    });

    doc.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') handleBack();
    });

    bindMediaSession();
  }

  function showOutsideTelegram() {
    $('home-body').replaceChildren(h('div', { class: 'state-msg' },
      h('strong', { text: 'Open Lammot in Telegram' }),
      'This player works inside Telegram. Open the bot and tap “Open Lammot”.'));
    $('search-input').disabled = true;
  }

  function startParamPlaylist() {
    const unsafe = (tg && tg.initDataUnsafe) || {};
    const param = unsafe.start_param || '';
    if (param.startsWith('playlist_')) return param.slice('playlist_'.length);
    const fromUrl = new URLSearchParams(window.location.search).get('playlist');
    return fromUrl || null;
  }

  async function init() {
    applyTheme(store.get('theme', 'light'));
    applyAccent(store.get('accent', DEFAULT_ACCENT));
    applyWallpaper(store.get('wallpaper', ''));

    if (tg) {
      try { tg.ready(); } catch (_) { /* ignore */ }
      try { tg.expand(); } catch (_) { /* ignore */ }
      try { if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); } catch (_) { /* old client */ }
      try { tg.BackButton.onClick(handleBack); } catch (_) { /* old client */ }
    }

    bindStaticUI();
    initData = (tg && tg.initData) || '';
    if (!initData) {
      showOutsideTelegram();
      return;
    }

    renderHome();
    renderPlayer();

    const [me, favs, history, trims] = await Promise.allSettled([
      api('/api/me'),
      api('/api/favorites'),
      api('/api/history'),
      api('/api/trims'),
    ]);
    if (me.status === 'fulfilled' && me.value) {
      state.user = me.value.user;
      state.shareBase = me.value.share_base;
    } else if (me.status === 'rejected') {
      toast(me.reason.message);
    }
    if (favs.status === 'fulfilled' && favs.value) {
      state.favorites = new Map(favs.value.map((t) => [t.video_id, t]));
    }
    if (history.status === 'fulfilled' && history.value) {
      state.recent = history.value.searches || [];
      if (state.search.status === 'idle') renderHome();
    }
    if (trims.status === 'fulfilled' && trims.value) {
      state.trims = new Map(trims.value.map((t) => [t.video_id, t]));
    }

    const token = startParamPlaylist();
    if (token && /^[A-Za-z0-9_-]{16,64}$/.test(token)) {
      openView('shared', { token }, { asRoot: true });
    }
  }

  init();
})();

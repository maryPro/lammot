/*
 * Artist and album view models, kept free of DOM access so they can be tested
 * with `node --test`. app.js loads this file with a plain <script> before
 * app.js and renders the normalized objects.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LammotArtistView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const ARTIST_SHELF_LIMIT = 3;
  const POPULAR_LIMIT = 5;
  const ALBUM_SHELF_LIMIT = 10;
  const SINGLE_SHELF_LIMIT = 10;

  function text(value) {
    return typeof value === 'string' ? value : '';
  }

  function limitOf(value, fallback) {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(0, Math.floor(n));
  }

  function normalizeTrack(raw, fallbackThumb, fallbackArtist, fallbackArtistId) {
    if (!raw || typeof raw !== 'object') return null;
    const videoId = text(raw.video_id);
    if (!videoId) return null;

    const seconds = Number(raw.duration_seconds);
    const artistId = text(raw.artist_id) || text(fallbackArtistId);

    return {
      video_id: videoId,
      title: text(raw.title),
      artist: text(raw.artist) || text(fallbackArtist),
      artist_id: artistId || null,
      duration_seconds: Number.isFinite(seconds) ? Math.max(0, Math.round(seconds)) : 0,
      thumbnail_url: text(raw.thumbnail_url) || text(fallbackThumb),
    };
  }

  function normalizeTracks(items, limit, fallbackThumb, fallbackArtist, fallbackArtistId) {
    const out = [];
    for (const item of Array.isArray(items) ? items : []) {
      if (out.length >= limit) break;
      const track = normalizeTrack(item, fallbackThumb, fallbackArtist, fallbackArtistId);
      if (track) out.push(track);
    }
    return out;
  }

  function normalizeArtistCard(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const browseId = text(raw.browse_id);
    const name = text(raw.name) || text(raw.title);
    if (!browseId || !name) return null;
    return {
      browse_id: browseId,
      name: name,
      thumbnail_url: text(raw.thumbnail_url),
    };
  }

  function normalizeAlbumCard(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const browseId = text(raw.browse_id);
    if (!browseId) return null;
    return {
      browse_id: browseId,
      title: text(raw.title),
      year: text(raw.year),
      type: text(raw.type),
      thumbnail_url: text(raw.thumbnail_url),
    };
  }

  function normalizeCards(items, limit) {
    const out = [];
    for (const item of Array.isArray(items) ? items : []) {
      if (out.length >= limit) break;
      const card = normalizeAlbumCard(item);
      if (card) out.push(card);
    }
    return out;
  }

  function normalizeArtistResults(items, limit) {
    const max = limitOf(limit, ARTIST_SHELF_LIMIT);
    const out = [];
    for (const item of Array.isArray(items) ? items : []) {
      if (out.length >= max) break;
      const card = normalizeArtistCard(item);
      if (card) out.push(card);
    }
    return out;
  }

  function normalizeArtistPage(payload) {
    const data = payload && typeof payload === 'object' ? payload : {};
    const rawArtist = data.artist && typeof data.artist === 'object' ? data.artist : {};
    const artist = {
      browse_id: text(rawArtist.browse_id),
      name: text(rawArtist.name),
      thumbnail_url: text(rawArtist.thumbnail_url),
    };

    return {
      artist: artist,
      popular: normalizeTracks(
        data.popular,
        POPULAR_LIMIT,
        artist.thumbnail_url,
        artist.name,
        artist.browse_id
      ),
      albums: normalizeCards(data.albums, ALBUM_SHELF_LIMIT),
      singles: normalizeCards(data.singles, SINGLE_SHELF_LIMIT),
    };
  }

  function normalizeAlbumPage(payload) {
    const data = payload && typeof payload === 'object' ? payload : {};
    const rawAlbum = data.album && typeof data.album === 'object' ? data.album : {};
    const album = {
      browse_id: text(rawAlbum.browse_id),
      title: text(rawAlbum.title),
      artist: text(rawAlbum.artist),
      artist_id: text(rawAlbum.artist_id) || null,
      year: text(rawAlbum.year),
      type: text(rawAlbum.type),
      thumbnail_url: text(rawAlbum.thumbnail_url),
    };

    return {
      album: album,
      tracks: normalizeTracks(
        data.tracks,
        Number.MAX_SAFE_INTEGER,
        album.thumbnail_url,
        album.artist,
        album.artist_id
      ),
    };
  }

  function artistLinkModel(track) {
    const raw = track && typeof track === 'object' ? track : {};
    const id = text(raw.artist_id);
    return {
      id: id,
      name: text(raw.artist) || 'Unknown artist',
      isLink: Boolean(id),
    };
  }

  function albumMetaLine(album) {
    const raw = album && typeof album === 'object' ? album : {};
    const type = text(raw.type);
    const year = text(raw.year);
    if (!type) return year;
    return year ? `${type} • ${year}` : type;
  }

  return {
    normalizeArtistResults: normalizeArtistResults,
    normalizeArtistPage: normalizeArtistPage,
    normalizeAlbumPage: normalizeAlbumPage,
    artistLinkModel: artistLinkModel,
    albumMetaLine: albumMetaLine,
  };
});

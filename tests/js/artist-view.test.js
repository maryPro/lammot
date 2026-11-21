'use strict';

const test = require('node:test');
const assert = require('node:assert');

const {
  normalizeArtistResults,
  normalizeArtistPage,
  normalizeAlbumPage,
  artistLinkModel,
  albumMetaLine,
} = require('../../frontend/artist-view.js');

const ARTIST_ID = 'UCmMUZbaYdNH0bEd1PAlAqsA';
const ALBUM_ID = 'MPREb_AYetWMZunqA';

function track(overrides) {
  return {
    video_id: 'aaaaaaaaaaa',
    title: 'Wonderwall',
    artist: 'Oasis',
    artist_id: ARTIST_ID,
    duration_seconds: 258,
    thumbnail_url: 'https://i.ytimg.com/track.jpg',
    ...overrides,
  };
}

test('normalizeArtistResults maps and caps at the requested limit', () => {
  const raw = [
    { browse_id: 'UCaaaaaaaa1', name: 'One', thumbnail_url: 'https://a/1.jpg' },
    { browse_id: 'UCaaaaaaaa2', name: 'Two', thumbnail_url: 'https://a/2.jpg' },
    { browse_id: 'UCaaaaaaaa3', name: 'Three', thumbnail_url: 'https://a/3.jpg' },
    { browse_id: 'UCaaaaaaaa4', name: 'Four', thumbnail_url: 'https://a/4.jpg' },
  ];

  assert.deepStrictEqual(normalizeArtistResults(raw, 3).map((a) => a.name), [
    'One',
    'Two',
    'Three',
  ]);
});

test('normalizeArtistResults drops cards without an id or name', () => {
  const raw = [
    { browse_id: '', name: 'No id' },
    { browse_id: 'UCaaaaaaaa1', name: '' },
    { browse_id: 'UCaaaaaaaa2', title: 'Title fallback' },
    null,
  ];

  assert.deepStrictEqual(normalizeArtistResults(raw, 3), [
    {
      browse_id: 'UCaaaaaaaa2',
      name: 'Title fallback',
      thumbnail_url: '',
    },
  ]);
});

test('normalizeArtistResults handles missing input', () => {
  assert.deepStrictEqual(normalizeArtistResults(null, 3), []);
  assert.deepStrictEqual(normalizeArtistResults(undefined, 3), []);
});

test('normalizeArtistPage normalizes sections and hides empty ones', () => {
  const page = normalizeArtistPage({
    artist: { browse_id: ARTIST_ID, name: 'Oasis', thumbnail_url: 'https://a/me.jpg' },
    popular: [track(), { title: 'No id' }],
    albums: [
      { browse_id: ALBUM_ID, title: 'Morning Glory', year: '1995', type: 'Album', thumbnail_url: 'https://a/alb.jpg' },
      { title: 'No id' },
    ],
    singles: [],
  });

  assert.strictEqual(page.artist.name, 'Oasis');
  assert.deepStrictEqual(page.popular.map((t) => t.video_id), ['aaaaaaaaaaa']);
  assert.deepStrictEqual(page.albums.map((a) => a.browse_id), [ALBUM_ID]);
  assert.deepStrictEqual(page.singles, []);
});

test('normalizeArtistPage caps popular, albums and singles', () => {
  const manyTracks = Array.from({ length: 8 }, (_, i) =>
    track({ video_id: `vid${String(i).padStart(8, '0')}` }));
  const manyCards = Array.from({ length: 14 }, (_, i) => ({
    browse_id: `MPREb_${i}`,
    title: `Card ${i}`,
    type: 'Album',
    year: '2000',
  }));

  const page = normalizeArtistPage({
    artist: { browse_id: ARTIST_ID, name: 'Oasis' },
    popular: manyTracks,
    albums: manyCards,
    singles: manyCards,
  });

  assert.strictEqual(page.popular.length, 5);
  assert.strictEqual(page.albums.length, 10);
  assert.strictEqual(page.singles.length, 10);
});

test('normalizeArtistPage fills a missing track thumbnail from the artist', () => {
  const page = normalizeArtistPage({
    artist: { browse_id: ARTIST_ID, name: 'Oasis', thumbnail_url: 'https://a/me.jpg' },
    popular: [track({ thumbnail_url: '' })],
  });

  assert.strictEqual(page.popular[0].thumbnail_url, 'https://a/me.jpg');
});

test('normalizeAlbumPage filters invalid tracks and preserves order', () => {
  const page = normalizeAlbumPage({
    album: {
      browse_id: ALBUM_ID,
      title: 'Morning Glory',
      artist: 'Oasis',
      artist_id: ARTIST_ID,
      year: '1995',
      type: 'Album',
      thumbnail_url: 'https://a/cover.jpg',
    },
    tracks: [
      track({ video_id: 'first111111', thumbnail_url: '' }),
      { video_id: '', title: 'No id' },
      { title: 'Also no id' },
      track({ video_id: 'second11111', title: 'Second' }),
    ],
  });

  assert.deepStrictEqual(page.tracks.map((t) => t.video_id), [
    'first111111',
    'second11111',
  ]);
  assert.strictEqual(page.tracks[0].thumbnail_url, 'https://a/cover.jpg');
});

test('normalizeAlbumPage handles missing input', () => {
  const page = normalizeAlbumPage(null);
  assert.deepStrictEqual(page.tracks, []);
  assert.strictEqual(page.album.title, '');
});

test('artistLinkModel returns a link when artist_id exists', () => {
  assert.deepStrictEqual(artistLinkModel(track()), {
    id: ARTIST_ID,
    name: 'Oasis',
    isLink: true,
  });
});

test('artistLinkModel is static without artist_id', () => {
  assert.deepStrictEqual(artistLinkModel(track({ artist_id: null })), {
    id: '',
    name: 'Oasis',
    isLink: false,
  });
  assert.deepStrictEqual(artistLinkModel(null), {
    id: '',
    name: 'Unknown artist',
    isLink: false,
  });
});

test('albumMetaLine formats Album, Single and EP', () => {
  assert.strictEqual(albumMetaLine({ type: 'Album', year: '1995' }), 'Album • 1995');
  assert.strictEqual(albumMetaLine({ type: 'Single', year: '2024' }), 'Single • 2024');
  assert.strictEqual(albumMetaLine({ type: 'EP', year: '2023' }), 'EP • 2023');
});

test('albumMetaLine omits a missing year', () => {
  assert.strictEqual(albumMetaLine({ type: 'Album' }), 'Album');
  assert.strictEqual(albumMetaLine(null), '');
});

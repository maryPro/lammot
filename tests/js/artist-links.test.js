'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const app = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'style.css'), 'utf8');

function section(startMarker, endMarker) {
  const start = app.indexOf(startMarker);
  assert.notStrictEqual(start, -1, `missing marker: ${startMarker}`);
  const end = app.indexOf(endMarker, start);
  assert.notStrictEqual(end, -1, `missing end marker: ${endMarker}`);
  return app.slice(start, end);
}

test('artist names in track lists are not underlined', () => {
  const link = css.match(/\.track-artist-link \{([^}]*)\}/);
  assert.notStrictEqual(link, null, '.track-artist-link must be styled');
  assert.doesNotMatch(link[1], /text-decoration:\s*underline;/,
    'list rows must keep the artist tappable without an underline');
  assert.match(app, /class: 'track-artist track-artist-link'/,
    'the shared artist button must stay in every track list');
});

test('the full player keeps the artist underline', () => {
  const rule = css.match(/\.fp-artist\.is-link \{([^}]*)\}/);
  assert.notStrictEqual(rule, null, '.fp-artist.is-link must be styled');
  assert.match(rule[1], /text-decoration:\s*underline;/,
    'only the full-screen player marks the artist link with an underline');
});

test('Favorites and playlist rows resolve an artist without a stored artist_id', () => {
  const artist = section('function trackArtistElement(', 'function trackRow(');
  assert.match(artist, /function trackArtistElement\(track, opts\)/,
    'the row renderer must know which list it is drawing');
  assert.match(artist, /variant === 'favorites'/,
    'Favorites rows must allow the artist-name fallback');
  assert.match(artist, /variant === 'playlist'/,
    'playlist rows must allow the artist-name fallback');
  assert.match(artist, /variant === 'shared'/,
    'shared playlist rows must allow the same fallback');
  assert.match(artist, /openArtist\(link\.id, link\.name\)/,
    'the artist button must open the profile');
});

test('a missing artist id is resolved with the artist search endpoint', () => {
  const open = section('function openArtist(', 'function artistAvatar(');
  assert.match(open, /openArtistByName\(name\)/,
    'openArtist must use the name lookup when no id is stored');
  assert.match(open, /\/api\/artists\?q=/,
    'the fallback must reuse the artist search endpoint');
  assert.match(open, /ArtistView\.normalizeArtistResults/,
    'the fallback must normalize the artist card like the search shelf');
  assert.match(open, /openView\('artist'/,
    'a resolved artist must open the artist page');
});

test('the player opens the artist profile for songs without a stored id', () => {
  const render = section('function renderPlayer()', 'function renderPlayState()');
  assert.match(render, /artistLink\.isLink \|\|/,
    'the player artist must be enabled when the name can be looked up');
  assert.match(render, /dataset\.artistName/,
    'the player must remember the artist name for the fallback');
  assert.match(render, /t\.kind !== 'video'/,
    'Extended video rows stay out of the artist-name fallback');

  const click = section(
    "$('fp-artist').addEventListener('click'",
    "$('fp-close').addEventListener('click'"
  );
  assert.match(click, /dataset\.artistId/, 'the player must use a stored id when available');
  assert.match(click, /dataset\.artistName/, 'the player must fall back to the artist name');
  assert.match(click, /openArtist\(artistId, name\)/, 'the player must open the artist page');
});

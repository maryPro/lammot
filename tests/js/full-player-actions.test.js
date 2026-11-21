'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const app = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'style.css'), 'utf8');

function between(startMarker, endMarker) {
  const start = html.indexOf(startMarker);
  assert.notStrictEqual(start, -1, `index.html must contain ${startMarker}`);
  const end = html.indexOf(endMarker, start);
  assert.notStrictEqual(end, -1, `index.html must contain ${endMarker} after ${startMarker}`);
  return html.slice(start, end);
}

test('the player top row keeps only the close action', () => {
  const top = between('<div class="fp-top">', '<div class="fp-art-wrap">');
  assert.notStrictEqual(top.indexOf('id="fp-close"'), -1, 'the top row must keep the close chevron');
  assert.doesNotMatch(top, /id="fp-more"/,
    'the more button must leave the top row for the track meta row');
});

test('the transport row keeps only previous, play, next', () => {
  const controls = between('<div class="fp-controls">', '<section class="fp-queue"');
  const ids = ['fp-prev', 'fp-play', 'fp-next'];
  const positions = ids.map((id) => {
    const pos = controls.indexOf(`id="${id}"`);
    assert.notStrictEqual(pos, -1, `the controls must include #${id}`);
    return pos;
  });
  for (let i = 1; i < positions.length; i += 1) {
    assert.ok(
      positions[i - 1] < positions[i],
      `#${ids[i - 1]} must come before #${ids[i]}`,
    );
  }
  assert.doesNotMatch(controls, /id="fp-more"/,
    'the more button must leave the transport row');
  assert.doesNotMatch(controls, /id="fp-fav"/,
    'the favorite heart must leave the transport row');
});

test('the player heart and more actions sit beside the track title', () => {
  const meta = between('<div class="fp-meta">', '<div class="fp-timeline">');
  const title = meta.indexOf('id="fp-title"');
  const artist = meta.indexOf('id="fp-artist"');
  const fav = meta.indexOf('id="fp-fav"');
  const more = meta.indexOf('id="fp-more"');
  assert.notStrictEqual(title, -1, 'the meta row must keep the title');
  assert.notStrictEqual(artist, -1, 'the meta row must keep the artist');
  assert.notStrictEqual(fav, -1, 'the meta row must hold the favorite heart');
  assert.notStrictEqual(more, -1, 'the meta row must hold the more button');
  assert.ok(title < artist && artist < fav && fav < more,
    'the heart and more actions must follow the title and artist, heart first');
});

test('renderPlayer syncs the player heart and labels the more button', () => {
  const start = app.indexOf('function renderPlayer() {');
  assert.notStrictEqual(start, -1, 'renderPlayer() must exist');
  const end = app.indexOf('function renderPlayState()', start);
  assert.notStrictEqual(end, -1, 'renderPlayState() must follow renderPlayer()');
  const body = app.slice(start, end);
  assert.match(body, /const fav = \$\('fp-fav'\);/, 'renderPlayer must read the player heart');
  assert.match(body, /fav\.dataset\.fav = t\.video_id;/, 'the heart must be tracked by video id');
  assert.match(body, /setFavButton\(fav, t\);/, 'the heart must render the current favorite state');
  assert.match(
    body,
    /\$\('fp-more'\)\.setAttribute\('aria-label', `More actions for \$\{t\.title \|\| 'Untitled'\}`\);/,
    'the more button must name the current track',
  );
});

test('the player heart toggles the current track in Favorites', () => {
  assert.match(
    app,
    /\$\('fp-fav'\)\.addEventListener\('click',[\s\S]{0,220}?const t = player\.current;[\s\S]{0,120}?if \(isFavorite\(t\.video_id\)\) removeFavorite\(t\);[\s\S]{0,80}?else addFavorite\(t, \$\('fp-fav'\)\);/,
    'the player heart must add or remove the current track',
  );
});

test('the player more button opens the current-track menu', () => {
  assert.match(
    app,
    /\$\('fp-more'\)\.addEventListener\('click',[\s\S]{0,120}?if \(player\.current\) \{[\s\S]{0,80}?openTrackMenu\(player\.current, \{ variant: 'player' \}\);/,
    'the more button must open the player menu for the current track',
  );
});

test('the player menu omits Play next and favorite items', () => {
  const start = app.indexOf('function openTrackMenu(');
  assert.notStrictEqual(start, -1, 'openTrackMenu() must exist');
  const end = app.indexOf('// ------------------------------------------------------------ sheets', start);
  assert.notStrictEqual(end, -1, 'the sheet helpers must follow openTrackMenu()');
  const body = app.slice(start, end);
  assert.match(body, /const nowPlaying = opts\.variant === 'player';/,
    'the menu must know when it is opened from the player');
  assert.match(body, /if \(player\.current && !nowPlaying\) \{[\s\S]{0,80}?items\.push\(\{ label: 'Play next'/,
    'Play next must be skipped for the current track');
  assert.match(body, /if \(!nowPlaying\) \{\s*if \(isFavorite\(track\.video_id\)\)/,
    'favorite items must be skipped for the player menu');
});

test('the top row aligns the close to the right', () => {
  assert.match(css, /\.fp-top \{[^}]*display: flex;[^}]*justify-content: flex-end;/,
    'the close chevron must sit in the top-right corner');
  assert.match(css, /\.fp-meta \{[^}]*display: flex;[^}]*align-items: center;/,
    'the meta row must lay the text and actions out side by side');
  assert.match(css, /\.fp-meta-actions \{[^}]*display: flex;[^}]*gap:/,
    'the heart and more buttons must stay grouped to the right of the title');
});

test('the transport and meta buttons keep their sizes', () => {
  assert.match(css, /\.fp-skip \{\s*width: 44px;\s*height: 44px;\s*\}/,
    'skip buttons must be 44px');
  assert.match(css, /\.fp-fav \{\s*width: 44px;\s*height: 44px;/,
    'the meta heart must keep a 44px hit target');
  assert.match(css, /\.fp-fav \.icon \{\s*width: 28px;\s*height: 28px;\s*\}/,
    'the meta heart glyph must stay readable without a background');
});

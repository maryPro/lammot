'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const app = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'style.css'), 'utf8');

function controlsMarkup() {
  const start = html.indexOf('<div class="fp-controls">');
  assert.notStrictEqual(start, -1, 'index.html must contain the full-player controls');
  const end = html.indexOf('</div>', start);
  assert.notStrictEqual(end, -1, 'the full-player controls block must be closed');
  return html.slice(start, end);
}

test('the full-player row orders more, previous, play, next, favorite', () => {
  const block = controlsMarkup();
  const ids = ['fp-more', 'fp-prev', 'fp-play', 'fp-next', 'fp-fav'];
  const positions = ids.map((id) => {
    const pos = block.indexOf(`id="${id}"`);
    assert.notStrictEqual(pos, -1, `the controls must include #${id}`);
    return pos;
  });
  for (let i = 1; i < positions.length; i += 1) {
    assert.ok(
      positions[i - 1] < positions[i],
      `#${ids[i - 1]} must come before #${ids[i]}`,
    );
  }
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

test('the five-button row stays compact', () => {
  assert.match(css, /\.fp-controls \{[^}]*gap: var\(--spacing-sm\);/,
    'the player row must use the small gap');
  assert.match(css, /\.fp-skip \{\s*width: 44px;\s*height: 44px;\s*\}/,
    'skip buttons must be 44px');
  assert.match(css, /\.fp-action,\s*\.fp-fav \{\s*width: 40px;\s*height: 40px;/,
    'secondary player actions must be 40px');
});

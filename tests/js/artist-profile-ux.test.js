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

test('the artist page renders a drag handle and a close chevron', () => {
  const artistView = section('VIEW_RENDERERS.artist =', '// --- Album page');
  assert.match(artistView, /class: 'artist-sheet'/,
    'the artist page must be wrapped in its own sheet container');
  assert.match(artistView, /class: 'artist-top'/,
    'the artist page must have a top row for the drag affordances');
  assert.match(artistView, /class: 'artist-grabber'/,
    'the artist page must show the standard grabber pill');
  assert.match(artistView, /'aria-label': 'Close artist profile'/,
    'the top-right chevron must be labelled for screen readers');
  assert.match(artistView, /icon\('chevronDown'\)/,
    'the artist page must offer the same down-chevron as the player');
});

test('the artist sheet supports swipe down to close', () => {
  const swipe = section(
    'function bindArtistSwipeDown(',
    '// ------------------------------------------------------------ Telegram back button'
  );
  assert.match(swipe, /touchstart/, 'the artist sheet must track the start of a swipe');
  assert.match(swipe, /touchmove/, 'the artist sheet must follow the finger');
  assert.match(swipe, /touchend/, 'the artist sheet must decide on release');
  assert.match(swipe, /v\.name !== 'artist'/,
    'the gesture must ignore every other view');
  assert.match(swipe, /window\.scrollY/,
    'swipe-to-close must only start when the page is already at the top');
  assert.match(swipe, /dy > 90/, 'a short drag must snap back instead of closing');
  assert.match(swipe, /closeArtistView\(\)/, 'a long drag must close the artist page');
  assert.match(app, /bindArtistSwipeDown\(\);/, 'the swipe gesture must be wired at startup');
});

test('closing the artist sheet returns to the previous view', () => {
  const close = section('function closeArtistView(', 'function bindArtistSwipeDown(');
  assert.match(close, /translateY\(100%\)/, 'the sheet must slide away before it closes');
  assert.match(close, /goBack\(\)/, 'closing the artist sheet must pop the view stack');
});

test('the artist drag indicator uses the quiet pill style', () => {
  assert.match(css, /\.artist-top \{[^}]*display:\s*flex;/s,
    'the artist top row must lay out the grabber and close control');
  const grabber = css.match(/\.artist-grabber \{([^}]*)\}/);
  assert.notStrictEqual(grabber, null, '.artist-grabber must be styled');
  assert.match(grabber[1], /width:\s*36px;/);
  assert.match(grabber[1], /height:\s*4px;/);
  assert.match(grabber[1], /background:\s*var\(--border\);/,
    'the artist grabber must match the player grabber');
});

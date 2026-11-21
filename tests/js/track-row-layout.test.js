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

const trackRow = section('function trackRow(', 'function markCurrentRows(');
const artistView = section('VIEW_RENDERERS.artist =', '// --- Album page');

test('track rows keep the title and artist in one centred text block', () => {
  assert.match(trackRow, /class: 'track-text'[\s\S]*trackArtistElement\(track\)/,
    'the artist must render inside the title text block');
  assert.doesNotMatch(trackRow, /main,\s*trackArtistElement\(track\)/,
    'the artist must not be a separate CSS grid row');
});

test('the track row is a single vertically-centred flex row', () => {
  const rule = css.match(/\.track \{([^}]*)\}/);
  assert.notStrictEqual(rule, null, '.track must be styled');
  assert.match(rule[1], /display:\s*flex;/, 'track rows must not use a two-row grid');
  assert.match(rule[1], /align-items:\s*center;/,
    'the cover, text block and actions must share one vertical centre line');
  assert.match(rule[1], /padding:\s*10px 0;/, 'track rows must keep equal top and bottom padding');
});

test('the artist line no longer uses the old second-grid-row offsets', () => {
  assert.doesNotMatch(css, /\.track-artist-static,[\s\S]{0,240}grid-row:\s*2/,
    'the artist line must not be placed on a second grid row');
  assert.doesNotMatch(css, /\.track-artist-static,[\s\S]{0,240}padding:\s*0 0 10px 60px/,
    'the artist line must not be pushed under the cover with hand-tuned padding');
});

test('the title and artist lines are compact', () => {
  const rule = css.match(/\.track-text \{([^}]*)\}/);
  assert.notStrictEqual(rule, null, '.track-text must be styled');
  assert.match(rule[1], /flex-direction:\s*column;/);
  assert.match(rule[1], /gap:\s*[0-4]px;/, 'title and artist must sit close together');
});

test('search rows use the compact pink plus button to add a favourite', () => {
  assert.match(trackRow, /opts\.variant === 'search'[\s\S]{0,160}favButton\(track, 'add'\)/,
    'search rows must offer the add-to-favourites plus button');
  const add = css.match(/\.fav-btn\.fav-add \{([^}]*)\}/);
  assert.notStrictEqual(add, null, 'the add button needs its own style');
  assert.match(add[1], /background:\s*var\(--accent\);/,
    'the add button must be a filled accent circle');
  assert.match(add[1], /border-radius:\s*var\(--radius-pill\);/);
});

test('saved search rows switch the plus to a check', () => {
  assert.match(app, /icon\(fav \? 'check' : 'plus'\)/,
    'the add button must show a check once the track is saved');
});

test('artist page rows keep a favourite heart alongside duration and menu', () => {
  assert.match(trackRow, /opts\.variant === 'artist'/,
    'artist rows must include the favourite button condition');
  assert.match(trackRow, /favButton\(track\)/, 'artist rows must render the heart');
  const duration = trackRow.indexOf("class: 'track-dur'");
  const heart = trackRow.indexOf('favButton(track, \'add\')');
  const toggleHeart = trackRow.indexOf('favButton(track)');
  const more = trackRow.indexOf("class: 'icon-btn more-btn'");
  assert.ok(duration !== -1 && heart !== -1 && toggleHeart !== -1 && more !== -1,
    'duration, favourite control and menu must all be present');
  assert.ok(duration < heart && heart < toggleHeart && toggleHeart < more,
    'the right side must read duration, favourite control, then menu');
});

test('the artist page has no duplicate custom header and no header Play button', () => {
  assert.doesNotMatch(artistView, /viewHeader\(/,
    'the artist page must rely on the Telegram BackButton instead of a second in-app header');
  assert.doesNotMatch(artistView, /artist-play/,
    'the small header Play button must be removed');
  assert.match(artistView, /class: 'artist-hero'[\s\S]*hero-title/,
    'the artist name must stay as the hero title under the avatar');
});

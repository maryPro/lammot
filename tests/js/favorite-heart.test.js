'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const app = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'style.css'), 'utf8');

test('search rows show a heart instead of a plus', () => {
  assert.match(app, /heart:\s*'<path d="M12 20/, 'app.js must define an outlined heart icon');
  assert.match(app, /heartFilled:\s*'<path d="M12 20[^']*class="solid"/,
    'app.js must define a filled heart icon');
  assert.match(app, /icon\(fav \? 'heartFilled' : 'heart'\)/,
    'the favorite button must use heartFilled when saved and heart when not');
});

test('tapping a filled heart removes the track from Favorites', () => {
  assert.match(app, /if \(isFavorite\(track\.video_id\)\) removeFavorite\(track\);\s*else addFavorite\(track, btn\);/,
    'the favorite button must toggle between add and remove');
});

test('the Favorites tab uses a heart', () => {
  assert.match(html, /data-tab="favorites">\s*<svg[\s\S]*?M12 20/,
    'the bottom bar Favorites tab must draw a heart');
});

test('the search track menu no longer offers Add to Favorites', () => {
  assert.match(app, /opts\.variant !== 'search'[\s\S]{0,160}label: 'Add to Favorites'/,
    'Add to Favorites must be skipped for search rows');
});

test('the track menu uses a heart for Remove from Favorites', () => {
  assert.match(app, /label: 'Remove from Favorites', icon: 'heartFilled'/,
    'the remove action must show a heart instead of a star');
});

test('Remove from Favorites is destructive in every track list', () => {
  assert.match(app, /label: 'Remove from Favorites', icon: 'heartFilled', danger: true/,
    'removing a favorite must stay red in search results and Favorites alike');
});

test('the favorite heart fills more of the circle', () => {
  assert.match(css, /\.fav-btn \.icon \{\s*width: 20px;\s*height: 20px;/,
    'the heart inside the favorite button must be larger than the previous 18px');
});

'use strict';

const test = require('node:test');
const assert = require('node:assert');

const {
  normalizeTab,
  apiType,
  tabLabel,
  isVideoTrack,
} = require('../../frontend/search-tabs.js');

test('normalizeTab: videos stays videos', () => {
  assert.strictEqual(normalizeTab('videos'), 'videos');
});

test('normalizeTab: songs stays songs', () => {
  assert.strictEqual(normalizeTab('songs'), 'songs');
});

test('normalizeTab: missing or invalid values default to songs', () => {
  assert.strictEqual(normalizeTab(undefined), 'songs');
  assert.strictEqual(normalizeTab(null), 'songs');
  assert.strictEqual(normalizeTab(''), 'songs');
  assert.strictEqual(normalizeTab('all'), 'songs');
});

test('apiType: maps a tab to the API type', () => {
  assert.strictEqual(apiType('songs'), 'songs');
  assert.strictEqual(apiType('videos'), 'videos');
  assert.strictEqual(apiType('nonsense'), 'songs');
});

test('tabLabel: videos is Extended, songs is Songs', () => {
  assert.strictEqual(tabLabel('videos'), 'Extended');
  assert.strictEqual(tabLabel('songs'), 'Songs');
  assert.strictEqual(tabLabel(undefined), 'Songs');
});

test('isVideoTrack: only kind === video is a video track', () => {
  assert.strictEqual(isVideoTrack({ kind: 'video' }), true);
  assert.strictEqual(isVideoTrack({ kind: 'song' }), false);
  assert.strictEqual(isVideoTrack({}), false);
  assert.strictEqual(isVideoTrack(null), false);
  assert.strictEqual(isVideoTrack(undefined), false);
});

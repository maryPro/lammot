'use strict';

const test = require('node:test');
const assert = require('node:assert');

const {
  playbackUiState,
  playbackAriaLabel,
  isPlaybackBusy,
} = require('../../frontend/playback-view.js');

test('playbackUiState: loading while playing is still loading', () => {
  assert.strictEqual(playbackUiState({ loading: true, playing: false }), 'loading');
});

test('playbackUiState: loading wins over playing', () => {
  assert.strictEqual(playbackUiState({ loading: true, playing: true }), 'loading');
});

test('playbackUiState: playing after loading clears', () => {
  assert.strictEqual(playbackUiState({ loading: false, playing: true }), 'playing');
});

test('playbackUiState: paused when neither loading nor playing', () => {
  assert.strictEqual(playbackUiState({ loading: false, playing: false }), 'paused');
});

test('playbackUiState: missing input falls back to paused', () => {
  assert.strictEqual(playbackUiState(), 'paused');
});

test('playbackUiState: empty input falls back to paused', () => {
  assert.strictEqual(playbackUiState({}), 'paused');
});

test('playbackAriaLabel: loading', () => {
  assert.strictEqual(playbackAriaLabel('loading'), 'Loading');
});

test('playbackAriaLabel: playing', () => {
  assert.strictEqual(playbackAriaLabel('playing'), 'Pause');
});

test('playbackAriaLabel: paused', () => {
  assert.strictEqual(playbackAriaLabel('paused'), 'Play');
});

test('isPlaybackBusy: true only while loading', () => {
  assert.strictEqual(isPlaybackBusy('loading'), true);
  assert.strictEqual(isPlaybackBusy('playing'), false);
  assert.strictEqual(isPlaybackBusy('paused'), false);
});

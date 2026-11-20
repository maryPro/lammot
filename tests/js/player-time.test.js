'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { pickDuration, positionState, progressRatio, shouldForceTrackEnd } = require('../../frontend/player-time.js');

test('pickDuration: iOS reports doubled media duration', () => {
  assert.strictEqual(pickDuration(483, 242, 0), 242);
});

test('pickDuration: playback still inside metadata', () => {
  assert.strictEqual(pickDuration(483, 242, 241), 242);
});

test('pickDuration: doubled media duration stays untrusted past metadata', () => {
  assert.strictEqual(pickDuration(483, 242, 245), 242);
});

test('pickDuration: genuinely longer stream wins past metadata', () => {
  assert.strictEqual(pickDuration(260, 242, 245), 260);
});

test('pickDuration: media duration is NaN', () => {
  assert.strictEqual(pickDuration(NaN, 242, 0), 242);
});

test('pickDuration: media not loaded yet', () => {
  assert.strictEqual(pickDuration(0, 242, 0), 242);
});

test('pickDuration: no track metadata', () => {
  assert.strictEqual(pickDuration(320, 0, 0), 320);
});

test('pickDuration: both sources unknown', () => {
  assert.strictEqual(pickDuration(NaN, 0, 0), 0);
});

test('pickDuration: live stream with Infinity duration', () => {
  assert.strictEqual(pickDuration(Infinity, 242, 0), 242);
});

test('pickDuration: small mismatch prefers metadata', () => {
  assert.strictEqual(pickDuration(243, 242, 0), 242);
});

test('pickDuration: just under the doubling threshold stays adaptive', () => {
  assert.strictEqual(pickDuration(360, 242, 245), 360);
});

test('pickDuration: clearly doubled duration is ignored', () => {
  assert.strictEqual(pickDuration(400, 242, 245), 242);
});

test('shouldForceTrackEnd: doubled media, still before the real end', () => {
  assert.strictEqual(shouldForceTrackEnd(483, 242, 242.9), false);
});

test('shouldForceTrackEnd: doubled media, past the real end', () => {
  assert.strictEqual(shouldForceTrackEnd(483, 242, 243.1), true);
});

test('shouldForceTrackEnd: genuinely longer stream is not cut', () => {
  assert.strictEqual(shouldForceTrackEnd(260, 242, 300), false);
});

test('shouldForceTrackEnd: unknown metadata never forces', () => {
  assert.strictEqual(shouldForceTrackEnd(483, 0, 500), false);
});

test('shouldForceTrackEnd: unknown media never forces', () => {
  assert.strictEqual(shouldForceTrackEnd(NaN, 242, 500), false);
});

test('progressRatio: halfway', () => {
  assert.strictEqual(progressRatio(121, 242), 0.5);
});

test('progressRatio: clamps to 1', () => {
  assert.strictEqual(progressRatio(500, 242), 1);
});

test('progressRatio: clamps to 0', () => {
  assert.strictEqual(progressRatio(-1, 242), 0);
});

test('progressRatio: zero duration', () => {
  assert.strictEqual(progressRatio(10, 0), 0);
});

test('positionState: reports the corrected duration and position', () => {
  assert.deepStrictEqual(positionState(228, 6, 1), { duration: 228, position: 6, playbackRate: 1 });
});

test('positionState: clamps position past the corrected duration', () => {
  assert.deepStrictEqual(positionState(228, 500, 1), { duration: 228, position: 228, playbackRate: 1 });
});

test('positionState: clamps a negative position to zero', () => {
  assert.deepStrictEqual(positionState(228, -4, 1), { duration: 228, position: 0, playbackRate: 1 });
});

test('positionState: NaN position becomes zero', () => {
  assert.deepStrictEqual(positionState(228, NaN, 1), { duration: 228, position: 0, playbackRate: 1 });
});

test('positionState: invalid playback rate falls back to one', () => {
  assert.deepStrictEqual(positionState(228, 6, 0), { duration: 228, position: 6, playbackRate: 1 });
});

test('positionState: unknown duration returns null', () => {
  assert.strictEqual(positionState(0, 6, 1), null);
});

test('positionState: live stream duration returns null', () => {
  assert.strictEqual(positionState(Infinity, 6, 1), null);
});

test('app.js wiring uses the shared duration helper', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'app.js'), 'utf8');
  assert.match(source, /function playbackDuration\(\)/, 'app.js must define playbackDuration()');
  assert.match(source, /pickDuration\(/, 'playbackDuration() must use pickDuration()');
  assert.match(source, /shouldForceTrackEnd\(/, 'timeupdate must force the end of a doubled stream');
  assert.match(source, /player\.forcedEnd/, 'forced end must be guarded once per track');
  assert.doesNotMatch(source, /audio\.duration \|\|/, 'seek/timeupdate must not fall back to raw audio.duration');
  assert.match(source, /positionState\(/, 'media session must use the shared position helper');
  assert.match(source, /setPositionState\(/, 'the system timeline must be told the corrected duration');
  assert.match(source, /addEventListener\('playing',\s*\(\)\s*=>\s*\{[\s\S]*?bindMediaSession\(\)/,
    'iOS only honors prev/next handlers registered once playback is active');
  assert.doesNotMatch(source, /setActionHandler\('seek(?:backward|forward)'/,
    'seek handlers would replace prev/next on the iOS lock screen');
});

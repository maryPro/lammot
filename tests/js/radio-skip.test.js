'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'app.js'), 'utf8');

function section(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  assert.notStrictEqual(start, -1, `missing marker: ${startMarker}`);
  const end = endMarker ? source.indexOf(endMarker, start) : source.length;
  assert.notStrictEqual(end, -1, `missing end marker: ${endMarker}`);
  return source.slice(start, end);
}

test('a track switch pauses the previous audio before any network wait', () => {
  const stop = section('function stopPlaybackNow(', 'function setLoading(');
  assert.match(stop, /audio\.pause\(\)/, 'stopPlaybackNow() must pause the media element');
  assert.match(stop, /isSilent\(\)/, 'the silent unlock clip must not be paused');

  const start = section('async function startTrack(', 'function handleTrackFailure(');
  const stopAt = start.indexOf('stopPlaybackNow()');
  assert.ok(stopAt >= 0, 'startTrack() must stop the old track');
  assert.ok(
    stopAt < start.indexOf('await streamLink('),
    'the old track must be stopped before the new stream resolves',
  );
});

test('next() stops the current track before refill and resolve', () => {
  const next = section('async function next(', 'function prev(');
  const stopAt = next.indexOf('stopPlaybackNow()');
  assert.ok(stopAt >= 0, 'next() must stop the current track');
  assert.ok(stopAt < next.indexOf('startTrack(upcoming)'),
    'the current track must be stopped before the next one starts');
  const refillAt = next.indexOf('await refillRadio(');
  if (refillAt >= 0) {
    assert.ok(stopAt < refillAt,
      'the current track must be stopped before the radio refill request');
  }
});

test('prev() always switches to the previous track and stops it first', () => {
  const prev = section('function prev(', 'function togglePlay(');
  assert.doesNotMatch(prev, /currentTime > 3/,
    'prev must not only rewind the current track');
  assert.match(prev, /player\.history\.length/, 'prev must still handle an empty history');
  const switchAt = prev.indexOf('startTrack(previous');
  assert.ok(switchAt >= 0, 'prev must play the previous track');
  assert.ok(prev.indexOf('stopPlaybackNow()') < switchAt,
    'the current track must be stopped before the previous one starts');
});

test('the next track is warmed from the start of the current one', () => {
  assert.match(source, /function prefetchUpcoming\(/,
    'a shared helper must prefetch the first queued track');

  const playing = section("audio.addEventListener('playing'", "audio.addEventListener('timeupdate'");
  assert.match(playing, /prefetchUpcoming\(\)/,
    'playback start must warm the next track instead of waiting for the last 15 s');

  const refill = section('async function refillRadio(', 'async function next(');
  assert.match(refill, /prefetchUpcoming\(\)/,
    'a freshly filled radio queue must warm its first track');
});

test('a tap reuses an in-flight resolve for the same track', () => {
  const stream = section('async function streamLink(', '// Prefetch is bounded');
  assert.doesNotMatch(stream, /prefetch \|\| !pending\.prefetch/,
    'same-key requests must not start a duplicate extraction');
  assert.match(stream, /if \(pending\) return pending\.promise/,
    'a tap must reuse the warm-up already resolving this exact track');
});

test('next waits for an in-flight radio refill instead of reporting an empty queue', () => {
  assert.match(source, /refillPromise: null/,
    'the player must track the in-flight radio refill');
  const refill = section('async function refillRadio(', 'async function next(');
  assert.match(refill, /if \(player\.refilling\) return player\.refillPromise/,
    'concurrent refills must share one request');
  assert.match(refill, /player\.refillPromise = run/,
    'the running refill promise must be exposed to next()');
});

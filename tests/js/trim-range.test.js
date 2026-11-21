'use strict';

const test = require('node:test');
const assert = require('node:assert');

const {
  MIN_TRIM_SECONDS,
  normalizeTrim,
  effectiveDuration,
  trimProgress,
  seekTime,
  reachedTrimEnd,
  clampToTrim,
  validateTrim,
} = require('../../frontend/trim-range.js');

test('normalizeTrim: no end means the full track', () => {
  assert.deepStrictEqual(normalizeTrim({ start_seconds: 0, end_seconds: null }, 200), {
    start: 0,
    end: 200,
    duration: 200,
    valid: true,
  });
});

test('normalizeTrim: no trim means the full track', () => {
  assert.deepStrictEqual(normalizeTrim(null, 200), {
    start: 0,
    end: 200,
    duration: 200,
    valid: true,
  });
});

test('normalizeTrim: start and end are clamped to the duration', () => {
  const insideEnd = normalizeTrim({ start_seconds: 10, end_seconds: 500 }, 200);
  assert.strictEqual(insideEnd.start, 10);
  assert.strictEqual(insideEnd.end, 200);
  assert.strictEqual(insideEnd.valid, true);

  const negativeStart = normalizeTrim({ start_seconds: -5, end_seconds: 20 }, 200);
  assert.strictEqual(negativeStart.start, 0);
  assert.strictEqual(negativeStart.end, 20);
  assert.strictEqual(negativeStart.valid, true);
});

test('normalizeTrim: invalid ranges are rejected', () => {
  assert.strictEqual(normalizeTrim({ start_seconds: 50, end_seconds: 20 }, 200).valid, false);
  assert.strictEqual(normalizeTrim({ start_seconds: 50, end_seconds: 50 }, 200).valid, false);
  assert.strictEqual(normalizeTrim({ start_seconds: 50, end_seconds: 50.5 }, 200).valid, false);
  assert.strictEqual(normalizeTrim({ start_seconds: 0, end_seconds: null }, 0).valid, false);
});

test('effectiveDuration: trims to the selected range, otherwise the full track', () => {
  assert.strictEqual(effectiveDuration({ start_seconds: 10, end_seconds: 110 }, 200), 100);
  assert.strictEqual(effectiveDuration({ start_seconds: 10, end_seconds: null }, 200), 190);
  assert.strictEqual(effectiveDuration(null, 200), 200);
});

test('trimProgress: relative position inside the selected range', () => {
  const trim = { start_seconds: 10, end_seconds: 110 };
  assert.strictEqual(trimProgress(60, trim, 200), 0.5);
  assert.strictEqual(trimProgress(10, trim, 200), 0);
  assert.strictEqual(trimProgress(110, trim, 200), 1);
  assert.strictEqual(trimProgress(5, trim, 200), 0);
  assert.strictEqual(trimProgress(120, trim, 200), 1);
});

test('seekTime: maps a ratio back into the selected range', () => {
  const trim = { start_seconds: 10, end_seconds: 110 };
  assert.strictEqual(seekTime(0.5, trim, 200), 60);
  assert.strictEqual(seekTime(0, trim, 200), 10);
  assert.strictEqual(seekTime(1, trim, 200), 110);
  assert.strictEqual(seekTime(-1, trim, 200), 10);
  assert.strictEqual(seekTime(2, trim, 200), 110);
  assert.strictEqual(seekTime(0.5, null, 200), 100);
});

test('reachedTrimEnd: true at the boundary, false before it', () => {
  const trim = { start_seconds: 10, end_seconds: 110 };
  assert.strictEqual(reachedTrimEnd(110, trim, 200), true);
  assert.strictEqual(reachedTrimEnd(109.5, trim, 200), false);
  assert.strictEqual(reachedTrimEnd(109.8, trim, 200), true);
  assert.strictEqual(reachedTrimEnd(50, null, 200), false);
});

test('clampToTrim: keeps seeks inside the selected range', () => {
  const trim = { start_seconds: 10, end_seconds: 110 };
  assert.strictEqual(clampToTrim(5, trim, 200), 10);
  assert.strictEqual(clampToTrim(120, trim, 200), 110);
  assert.strictEqual(clampToTrim(60, trim, 200), 60);
  assert.strictEqual(clampToTrim(120, null, 200), 120);
});

test('validateTrim: rejects inverted and sub-second ranges', () => {
  assert.strictEqual(validateTrim(0, 100, 200), null);
  assert.strictEqual(validateTrim(10, 10, 200), 'End must be after Start');
  assert.strictEqual(validateTrim(10, 5, 200), 'End must be after Start');
  assert.strictEqual(validateTrim(10, 10.5, 200), 'Select at least 1 second');
  assert.strictEqual(validateTrim(-1, 10, 200), 'Start must be inside the track');
  assert.strictEqual(validateTrim(0, 201, 200), 'End must be inside the track');
  assert.strictEqual(validateTrim(0, 100, 0), 'Track duration unavailable');
  assert.strictEqual(MIN_TRIM_SECONDS, 1);
});

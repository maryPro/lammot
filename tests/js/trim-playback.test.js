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

test('track rows show the trimmed duration instead of the raw track duration', () => {
  const row = section('function trackRow(', 'function markCurrentRows(');
  assert.match(row, /trimmedTrackDuration\(track\)/,
    'track rows must derive their duration from the saved trim');
  assert.doesNotMatch(row, /fmtTime\(track\.duration_seconds\)/,
    'track rows must not print the pre-trim duration');
});

test('saving and resetting a trim refreshes visible durations', () => {
  assert.match(source, /function refreshTrimDurations\(/,
    'a shared refresh helper is required for every visible track list');
  const save = section("saveBtn.addEventListener('click'", 'if (resetBtn)');
  assert.match(save, /refreshTrimDurations\(\)/,
    'saving a trim must refresh durations in favorites, playlists and search');
  const reset = section("resetBtn.addEventListener('click'", 'if (!(full > 0))');
  assert.match(reset, /refreshTrimDurations\(\)/,
    'resetting a trim must restore the original durations everywhere');
});

test('Reset trim is rendered as a danger action', () => {
  const reset = section('const resetBtn = saved', 'const cancelBtn');
  assert.match(reset, /btn-danger/,
    'Reset trim must use the same red danger style as “Delete all my data”');
  assert.match(reset, /Reset trim/);
});

test('opening the trim sheet does not pause the playing track', () => {
  const open = section('function openTrimSheet(', 'let previewLoading');
  assert.doesNotMatch(open, /audio\.pause\(\)/,
    'only Preview may pause the main player');
});

test('Preview pauses the main player and closing the sheet resumes it', () => {
  const preview = section("previewBtn.addEventListener('click'", "saveBtn.addEventListener('click'");
  assert.match(preview, /audio\.pause\(\)/,
    'Preview starts its own audio and must pause the main player first');
  assert.match(preview, /wasPlaying/,
    'Preview must remember whether the main track was playing');
  assert.match(source, /if \(session\.wasPlaying\) audio\.play\(\)/,
    'closing the sheet must resume a track that Preview paused');
});

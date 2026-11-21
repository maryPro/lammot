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

test('the queue shows five tracks', () => {
  assert.match(source, /const QUEUE_VISIBLE = 5;/);

  const render = section('function renderQueue()', '// --- media session');
  assert.match(render, /player\.queue\.slice\(0, QUEUE_VISIBLE\)/,
    'renderQueue must display the first five upcoming tracks');
});

test('refill keeps the visible queue full after every switch', () => {
  const refill = section('async function refillRadio(', 'async function next(');

  // Switching to #2 of five leaves three tracks.  A two-track threshold
  // silently stops the refill there and "Up next" stays short (radio-002).
  assert.match(refill, /if \(!force && player\.queue\.length >= QUEUE_VISIBLE\) return;/,
    'refill must run whenever fewer than five upcoming tracks remain');
  assert.doesNotMatch(refill, /player\.queue\.length >= 2\)/,
    'the old two-track threshold leaves the visible list short');

  const start = section('async function startTrack(', 'function handleTrackFailure(');
  assert.match(start, /refillRadio\(false\);/,
    'starting any track must top up the radio queue');
});

test('jumping to a queued track preserves the tracks below it', () => {
  const jump = section('function jumpTo(', 'function removeFromQueue(');
  assert.match(jump, /player\.queue\.splice\(0, index \+ 1\)/,
    'the tapped track and the tracks above it leave the queue');
  assert.match(jump, /startTrack\(target\)/, 'the tapped track must start playing');
});

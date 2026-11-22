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

test('the Up next play target fills the row so the remove button sits on the right edge', () => {
  const main = css.match(/\.queue-item \.track-main \{([^}]*)\}/);
  assert.notStrictEqual(main, null, '.queue-item .track-main must be styled');
  assert.match(main[1], /flex:\s*1;/, 'the play target must take the remaining row width');
  assert.match(main[1], /min-width:\s*0;/,
    'a long title must truncate instead of pushing the remove button left');

  const remove = css.match(/\.queue-item \.icon-btn \{([^}]*)\}/);
  assert.notStrictEqual(remove, null, '.queue-item .icon-btn must be styled');
  assert.match(remove[1], /flex:\s*none;/, 'the remove button must keep its fixed width');
});

test('the remove control comes after the full-width play target in every queue row', () => {
  const render = section('function renderQueue()', '// --- media session');
  const main = render.indexOf("class: 'track-main'");
  const remove = render.indexOf('Remove ${t.title} from the queue');
  assert.notStrictEqual(main, -1, 'queue rows must keep the play target');
  assert.notStrictEqual(remove, -1, 'queue rows must keep the remove control');
  assert.ok(main < remove, 'the remove control must be the last item in the row');
});

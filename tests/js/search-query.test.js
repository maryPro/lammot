'use strict';

const test = require('node:test');
const assert = require('node:assert');

const {
  normalizeQuery,
  canSubmitQuery,
  shouldSubmitQuery,
  idleStateForQuery,
} = require('../../frontend/search-query.js');

test('normalizeQuery: trims surrounding whitespace', () => {
  assert.strictEqual(normalizeQuery('  Smells  '), 'Smells');
});

test('normalizeQuery: empty and null inputs become empty strings', () => {
  assert.strictEqual(normalizeQuery(''), '');
  assert.strictEqual(normalizeQuery(null), '');
  assert.strictEqual(normalizeQuery(undefined), '');
});

test('normalizeQuery: keeps the inner query unchanged after trimming', () => {
  assert.strictEqual(normalizeQuery(' Smells like teen spirit '), 'Smells like teen spirit');
});

test('canSubmitQuery: 0-1 characters cannot be submitted', () => {
  assert.strictEqual(canSubmitQuery('', 2), false);
  assert.strictEqual(canSubmitQuery('a', 2), false);
  assert.strictEqual(canSubmitQuery('  a  ', 2), false);
});

test('canSubmitQuery: 2 characters can be submitted', () => {
  assert.strictEqual(canSubmitQuery('ab', 2), true);
});

test('canSubmitQuery: null input cannot be submitted', () => {
  assert.strictEqual(canSubmitQuery(null, 2), false);
});

test('shouldSubmitQuery: returns the normalized query on a fresh submit', () => {
  const current = idleStateForQuery('');
  assert.strictEqual(
    shouldSubmitQuery(current, ' Smells like teen spirit ', 2),
    'Smells like teen spirit',
  );
});

test('shouldSubmitQuery: too-short queries return null', () => {
  const current = idleStateForQuery('');
  assert.strictEqual(shouldSubmitQuery(current, 'a', 2), null);
});

test('shouldSubmitQuery: same query is rejected while loading', () => {
  const current = { q: 'Smells', status: 'loading', results: [], error: '' };
  assert.strictEqual(shouldSubmitQuery(current, 'Smells', 2), null);
});

test('shouldSubmitQuery: same query is rejected while done', () => {
  const current = { q: 'Smells', status: 'done', results: [], error: '' };
  assert.strictEqual(shouldSubmitQuery(current, 'Smells', 2), null);
});

test('shouldSubmitQuery: same query is rejected with surrounding whitespace', () => {
  const current = { q: 'Smells', status: 'done', results: [], error: '' };
  assert.strictEqual(shouldSubmitQuery(current, '  Smells  ', 2), null);
});

test('shouldSubmitQuery: a different query is allowed after a completed search', () => {
  const current = { q: 'Smells', status: 'done', results: [], error: '' };
  assert.strictEqual(shouldSubmitQuery(current, 'Nirvana', 2), 'Nirvana');
});

test('shouldSubmitQuery: the same query is allowed again after an error (retry)', () => {
  const current = { q: 'Smells', status: 'error', results: [], error: 'boom' };
  assert.strictEqual(shouldSubmitQuery(current, 'Smells', 2), 'Smells');
});

test('idleStateForQuery: clears results and error and returns idle', () => {
  const next = idleStateForQuery('  Smells  ');
  assert.deepStrictEqual(next, { q: 'Smells', status: 'idle', results: [], error: '' });
});

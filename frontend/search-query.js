/*
 * Search submit rules, kept free of DOM access so they can be tested with
 * `node --test`. app.js loads this file with a plain <script> before app.js.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LammotSearchQuery = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function normalizeQuery(raw) {
    if (raw === null || raw === undefined) return '';
    return String(raw).trim();
  }

  function canSubmitQuery(raw, minChars) {
    const q = normalizeQuery(raw);
    return q.length >= Number(minChars);
  }

  function shouldSubmitQuery(current, raw, minChars) {
    const q = normalizeQuery(raw);
    if (q.length < Number(minChars)) return null;

    const status = current && current.status;
    const currentQuery = normalizeQuery(current && current.q);
    if ((status === 'loading' || status === 'done') && currentQuery === q) {
      return null;
    }
    return q;
  }

  function idleStateForQuery(raw) {
    return { q: normalizeQuery(raw), status: 'idle', results: [], error: '' };
  }

  return {
    normalizeQuery: normalizeQuery,
    canSubmitQuery: canSubmitQuery,
    shouldSubmitQuery: shouldSubmitQuery,
    idleStateForQuery: idleStateForQuery,
  };
});

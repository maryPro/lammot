/*
 * Search tab rules, kept free of DOM access so they can be tested with
 * `node --test`. app.js loads this file with a plain <script> before app.js.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LammotSearchTabs = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const SONGS = 'songs';
  const VIDEOS = 'videos';

  function normalizeTab(value) {
    return value === VIDEOS ? VIDEOS : SONGS;
  }

  function apiType(value) {
    return normalizeTab(value);
  }

  function tabLabel(value) {
    return normalizeTab(value) === VIDEOS ? 'Extended' : 'Songs';
  }

  function isVideoTrack(track) {
    return Boolean(track && track.kind === 'video');
  }

  return {
    normalizeTab: normalizeTab,
    apiType: apiType,
    tabLabel: tabLabel,
    isVideoTrack: isVideoTrack,
  };
});

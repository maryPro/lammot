/*
 * Playback button state, kept free of DOM access so it can be tested with
 * `node --test`. app.js loads this file with a plain <script>.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LammotPlaybackView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function playbackUiState({ loading, playing } = {}) {
    if (loading) return 'loading';
    if (playing) return 'playing';
    return 'paused';
  }

  function playbackAriaLabel(state) {
    if (state === 'loading') return 'Loading';
    if (state === 'playing') return 'Pause';
    return 'Play';
  }

  function isPlaybackBusy(state) {
    return state === 'loading';
  }

  return { playbackUiState, playbackAriaLabel, isPlaybackBusy };
});

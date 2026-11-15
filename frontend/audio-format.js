/*
 * Codec choice for the <audio> element, kept free of DOM access so it can be
 * tested with `node --test`. app.js loads this file with a plain <script>.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LammotAudioFormat = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function chooseFormat({ canPlayType, userAgent, platform, maxTouchPoints } = {}) {
    const ua = String(userAgent || '');
    const isIOS = /iPad|iPhone|iPod/.test(ua)
      || (platform === 'MacIntel' && Number(maxTouchPoints) > 1);
    if (isIOS) return 'aac';

    let support = '';
    try {
      support = typeof canPlayType === 'function'
        ? canPlayType('audio/webm; codecs="opus"')
        : '';
    } catch (_) { /* an undecidable probe means "no Opus" */ }
    return support === 'probably' ? 'opus' : 'aac';
  }

  return { chooseFormat };
});
/*
 * Codec choice for the <audio> element, kept free of DOM access so it can be
 * tested with `node --test`. app.js loads this file with a plain <script>.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LammotAudioFormat = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  // iPadOS reports a desktop Macintosh user agent; the touch probe tells it
  // apart from a real Mac.  Both use AVFoundation, which estimates a doubled
  // duration for the fragmented AAC stream the app relays.
  function isIOS({ userAgent, platform, maxTouchPoints } = {}) {
    const ua = String(userAgent || '');
    return /iPad|iPhone|iPod/.test(ua)
      || (platform === 'MacIntel' && Number(maxTouchPoints) > 1);
  }

  function chooseFormat({ canPlayType, userAgent, platform, maxTouchPoints } = {}) {
    if (isIOS({ userAgent, platform, maxTouchPoints })) return 'aac';

    let support = '';
    try {
      support = typeof canPlayType === 'function'
        ? canPlayType('audio/webm; codecs="opus"')
        : '';
    } catch (_) { /* an undecidable probe means "no Opus" */ }
    return support === 'probably' ? 'opus' : 'aac';
  }

  return { chooseFormat, isIOS };
});

(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LammotTime = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var PAST_METADATA_GRACE_SECONDS = 2;

  function positive(value) {
    var n = Number(value);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  function pickDuration(mediaDuration, trackDuration, currentTime) {
    var media = positive(mediaDuration);
    var track = positive(trackDuration);

    if (!track) return media;
    if (!media) return track;

    var current = Number(currentTime);
    if (media > track && Number.isFinite(current)
        && current > track + PAST_METADATA_GRACE_SECONDS) {
      return media;
    }

    return track;
  }

  function progressRatio(current, duration) {
    var total = positive(duration);
    if (!total) return 0;
    var value = Number(current);
    if (!Number.isFinite(value)) return 0;
    return Math.min(1, Math.max(0, value / total));
  }

  return { pickDuration: pickDuration, progressRatio: progressRatio };
});

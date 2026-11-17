(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LammotTime = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var PAST_METADATA_GRACE_SECONDS = 2;
  // iOS AVFoundation reports roughly twice the real duration for the AAC
  // stream we relay.  A genuinely longer stream is never that much longer,
  // so anything above this ratio is the bug, not the media.
  var DOUBLED_DURATION_RATIO = 1.5;
  var TRACK_END_GRACE_SECONDS = 1;

  function positive(value) {
    var n = Number(value);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  function looksDoubled(media, track) {
    return track > 0 && media > track * DOUBLED_DURATION_RATIO;
  }

  function pickDuration(mediaDuration, trackDuration, currentTime) {
    var media = positive(mediaDuration);
    var track = positive(trackDuration);

    if (!track) return media;
    if (!media) return track;
    if (looksDoubled(media, track)) return track;

    var current = Number(currentTime);
    if (media > track && Number.isFinite(current)
        && current > track + PAST_METADATA_GRACE_SECONDS) {
      return media;
    }

    return track;
  }

  // The element keeps advancing through a phantom tail when its duration is
  // doubled, so "ended" may arrive minutes late.  Treat the metadata end as
  // the real end once playback has clearly passed it.
  function shouldForceTrackEnd(mediaDuration, trackDuration, currentTime) {
    var media = positive(mediaDuration);
    var track = positive(trackDuration);
    var current = Number(currentTime);

    if (!media || !track || !Number.isFinite(current)) return false;
    if (!looksDoubled(media, track)) return false;
    return current >= track + TRACK_END_GRACE_SECONDS;
  }

  function progressRatio(current, duration) {
    var total = positive(duration);
    if (!total) return 0;
    var value = Number(current);
    if (!Number.isFinite(value)) return 0;
    return Math.min(1, Math.max(0, value / total));
  }

  return {
    pickDuration: pickDuration,
    progressRatio: progressRatio,
    shouldForceTrackEnd: shouldForceTrackEnd,
  };
});

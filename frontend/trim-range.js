(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LammotTrim = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var MIN_TRIM_SECONDS = 1;
  var END_TOLERANCE_SECONDS = 0.25;

  function positive(value) {
    var n = Number(value);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  // Returns the trim clamped to the known track duration.  A null trim (or
  // null end_seconds) means the whole track.  valid=false means the caller
  // must fall back to full-track playback.
  function normalizeTrim(trim, fullDuration) {
    var duration = positive(fullDuration);
    var rawStart = Number(trim && trim.start_seconds);
    var rawEnd = trim && trim.end_seconds != null ? Number(trim.end_seconds) : duration;

    var start = Number.isFinite(rawStart) ? clamp(rawStart, 0, duration) : 0;
    var end = Number.isFinite(rawEnd) ? clamp(rawEnd, 0, duration) : duration;
    var valid = duration > 0 && end > start && end - start >= MIN_TRIM_SECONDS;

    return { start: start, end: end, duration: duration, valid: valid };
  }

  function effectiveDuration(trim, fullDuration) {
    var n = normalizeTrim(trim, fullDuration);
    return n.valid ? n.end - n.start : n.duration;
  }

  function trimProgress(current, trim, fullDuration) {
    var n = normalizeTrim(trim, fullDuration);
    var value = Number(current);
    if (!n.valid || !Number.isFinite(value)) return 0;
    return clamp((value - n.start) / (n.end - n.start), 0, 1);
  }

  function seekTime(ratio, trim, fullDuration) {
    var n = normalizeTrim(trim, fullDuration);
    var value = Number(ratio);
    if (!Number.isFinite(value)) value = 0;
    value = clamp(value, 0, 1);
    if (!n.valid) return value * n.duration;
    return n.start + value * (n.end - n.start);
  }

  function reachedTrimEnd(current, trim, fullDuration, tolerance) {
    var n = normalizeTrim(trim, fullDuration);
    var value = Number(current);
    if (!n.valid || !Number.isFinite(value)) return false;
    var grace = Number(tolerance);
    if (!Number.isFinite(grace) || grace < 0) grace = END_TOLERANCE_SECONDS;
    return value >= n.end - grace;
  }

  function clampToTrim(current, trim, fullDuration) {
    var n = normalizeTrim(trim, fullDuration);
    var value = Number(current);
    if (!Number.isFinite(value)) value = 0;
    if (!n.valid) return clamp(value, 0, n.duration);
    return clamp(value, n.start, n.end);
  }

  function validateTrim(start, end, fullDuration) {
    var duration = positive(fullDuration);
    if (!duration) return 'Track duration unavailable';

    var from = Number(start);
    var to = Number(end);
    if (!Number.isFinite(from) || from < 0 || from >= duration) {
      return 'Start must be inside the track';
    }
    if (!Number.isFinite(to) || to <= from) return 'End must be after Start';
    if (to > duration) return 'End must be inside the track';
    if (to - from < MIN_TRIM_SECONDS) return 'Select at least 1 second';
    return null;
  }

  return {
    MIN_TRIM_SECONDS: MIN_TRIM_SECONDS,
    normalizeTrim: normalizeTrim,
    effectiveDuration: effectiveDuration,
    trimProgress: trimProgress,
    seekTime: seekTime,
    reachedTrimEnd: reachedTrimEnd,
    clampToTrim: clampToTrim,
    validateTrim: validateTrim,
  };
});

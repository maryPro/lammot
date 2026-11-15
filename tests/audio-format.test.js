'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { chooseFormat } = require('../frontend/audio-format.js');

const IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) '
  + 'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148';
const IPAD_DESKTOP_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
  + 'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17_5 Safari/605.1.15';
const CHROME_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
  + 'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36';
const ANDROID_UA = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) '
  + 'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36';

const supportOf = (answer) => () => answer;

test('iPhone gets AAC even when WebKit answers "maybe" for Opus', () => {
  assert.equal(chooseFormat({
    canPlayType: supportOf('maybe'),
    userAgent: IPHONE_UA,
    platform: 'iPhone',
    maxTouchPoints: 5,
  }), 'aac');
});

test('iPhone gets AAC even when WebKit claims "probably"', () => {
  assert.equal(chooseFormat({
    canPlayType: supportOf('probably'),
    userAgent: IPHONE_UA,
    platform: 'iPhone',
    maxTouchPoints: 5,
  }), 'aac');
});

test('iPadOS reporting a desktop Macintosh user agent gets AAC', () => {
  assert.equal(chooseFormat({
    canPlayType: supportOf('probably'),
    userAgent: IPAD_DESKTOP_UA,
    platform: 'MacIntel',
    maxTouchPoints: 5,
  }), 'aac');
});

test('desktop Chrome gets Opus when support is "probably"', () => {
  assert.equal(chooseFormat({
    canPlayType: supportOf('probably'),
    userAgent: CHROME_UA,
    platform: 'MacIntel',
    maxTouchPoints: 0,
  }), 'opus');
});

test('Android Chrome gets Opus when support is "probably"', () => {
  assert.equal(chooseFormat({
    canPlayType: supportOf('probably'),
    userAgent: ANDROID_UA,
    platform: 'Linux armv8l',
    maxTouchPoints: 5,
  }), 'opus');
});

test('desktop "maybe" is not enough for Opus', () => {
  assert.equal(chooseFormat({
    canPlayType: supportOf('maybe'),
    userAgent: CHROME_UA,
    platform: 'MacIntel',
    maxTouchPoints: 0,
  }), 'aac');
});

test('empty canPlayType answer falls back to AAC', () => {
  assert.equal(chooseFormat({
    canPlayType: supportOf(''),
    userAgent: CHROME_UA,
    platform: 'MacIntel',
    maxTouchPoints: 0,
  }), 'aac');
});

test('a throwing canPlayType falls back to AAC', () => {
  assert.equal(chooseFormat({
    canPlayType: () => { throw new Error('boom'); },
    userAgent: CHROME_UA,
    platform: 'MacIntel',
    maxTouchPoints: 0,
  }), 'aac');
});

test('missing arguments fall back to AAC', () => {
  assert.equal(chooseFormat(), 'aac');
  assert.equal(chooseFormat({}), 'aac');
});

test('the Opus probe asks for WebM/Opus and nothing else', () => {
  const probes = [];
  chooseFormat({
    canPlayType: (type) => { probes.push(type); return 'probably'; },
    userAgent: CHROME_UA,
    platform: 'MacIntel',
    maxTouchPoints: 0,
  });
  assert.deepEqual(probes, ['audio/webm; codecs="opus"']);
});
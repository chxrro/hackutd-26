/* Deterministic scheduler/transport tests. No browser, camera, network, or GPU. */
const test = require('node:test');
const assert = require('node:assert/strict');
const {LatestCaptureLoop, frameFreshness} = require('../scripts/webcam_reasoner.js');
const settle = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  let tick, captured = 0, now = 100000;
  const requests = [], releases = [], errors = [], timers = [];
  const loop = new LatestCaptureLoop({
    capture: () => ({image: `frame-${++captured}`, captured_at: now / 1000}),
    send: frame => {requests.push(frame); return new Promise((resolve, reject) => releases.push({resolve, reject}));},
    onError: error => errors.push(error), now: () => now,
    schedule: (callback, interval) => { tick = callback; timers.push(interval); return 7; },
    cancel: timer => assert.equal(timer, 7),
  });
  return {loop, requests, releases, errors, timers, tick: () => tick(), advance: ms => {now += ms;}};
}
test('one-second sampling keeps a single latest pending frame during slow upload', async () => {
  const f = fixture(); f.loop.start();
  assert.deepEqual(f.timers, [1000]);
  f.tick(); f.tick();
  assert.equal(f.requests.length, 1);
  assert.equal(f.loop.pending.frame.image, 'frame-3');
  assert.equal(f.loop.replaced, 1);
  f.releases[0].resolve(); await settle();
  assert.deepEqual(f.requests.map(r => r.image), ['frame-1', 'frame-3']);
  f.loop.stop(); f.releases[1].resolve(); await settle();
});
test('pause discards pending frames and cannot submit after an upload completes', async () => {
  const f = fixture(); f.loop.start(); f.tick(); f.loop.stop();
  f.releases[0].resolve(); await settle();
  assert.equal(f.requests.length, 1);
  assert.equal(f.loop.pending, null);
});
test('stalled upload discards expired pending frame rather than replaying it', async () => {
  const f = fixture(); f.loop.start(); f.tick(); f.advance(4000);
  f.releases[0].resolve(); await settle();
  assert.equal(f.requests.length, 1);
  assert.equal(f.loop.pending, null);
  f.loop.stop();
});
test('capture or transport failure stops the loop and reports unavailable', async () => {
  const f = fixture(); f.loop.start();
  f.releases[0].reject(Error('offline')); await settle();
  assert.equal(f.loop.running, false);
  assert.equal(f.errors[0].message, 'offline');
  const c = fixture(); c.loop.capture = () => {throw Error('camera disconnected');}; c.loop.start();
  assert.equal(c.loop.running, false);
  assert.deepEqual(c.timers, []);
});
test('repeated Start does not create duplicate sampling timers', async () => {
  const f = fixture(); f.loop.start(); f.loop.start();
  assert.equal(f.timers.length, 1);
  assert.equal(f.requests.length, 1);
  f.loop.stop(); f.releases[0].resolve(); await settle();
});
test('a request from an earlier run cannot stop a newly resumed loop', async () => {
  const f = fixture(); f.loop.start(); f.loop.stop(); f.loop.start();
  f.releases[0].reject(Error('old timeout')); await settle();
  assert.equal(f.loop.running, true);
  assert.equal(f.requests.length, 2);
  assert.equal(f.errors.length, 0);
  f.loop.stop(); f.releases[1].resolve(); await settle();
});

test('an observation that expires during transport is rendered stale', () => {
  const value = {freshness: 'recent', max_frame_age_seconds: 3, result: {captured_at: 100}};
  assert.equal(frameFreshness(value, 102900), 'recent');
  assert.equal(frameFreshness(value, 103100), 'stale');
  assert.equal(frameFreshness({...value, freshness: 'paused'}, 100100), 'paused');
  assert.equal(frameFreshness({...value, freshness: 'stopped'}, 100100), 'stopped');
  assert.equal(frameFreshness({result: null}, 100100), 'unavailable');
});


test('an unavailable camera sample clears queued work without stopping Live', async () => {
  const f = fixture(); f.loop.start(); f.tick();
  const capture = f.loop.capture;
  f.loop.capture = () => null;
  f.tick();
  assert.equal(f.loop.running, true);
  assert.equal(f.loop.pending, null);
  f.releases[0].resolve(); await settle();
  assert.equal(f.requests.length, 1);
  f.loop.capture = capture; f.advance(1000); f.tick();
  assert.equal(f.requests.length, 2);
  f.loop.stop(); f.releases[1].resolve(); await settle();
});

test('retryable upload failure preserves the bounded queue and uses the latest sample', async () => {
  const f = fixture(), retries = [];
  f.loop.onRetry = error => retries.push(error);
  f.loop.start(); f.advance(1000); f.tick(); f.advance(1000); f.tick();
  const error = Object.assign(Error('temporary timeout'), {retryable: true});
  f.releases[0].reject(error); await settle();
  assert.equal(f.loop.running, true);
  assert.deepEqual(f.requests.map(r => r.image), ['frame-1', 'frame-3']);
  assert.equal(retries.length, 1);
  assert.equal(f.errors.length, 0);
  f.loop.stop(); f.releases[1].resolve(); await settle();
});

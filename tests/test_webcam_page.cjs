/* Exercise the real page wiring with simulated media/HTTP. No camera or GPU. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const settle = () => new Promise(resolve => setImmediate(resolve));

async function page(hash = '') {
  let clock = 100000, nextTimer = 0, failStatus = false, failUpload = false;
  let accepted = 0, paused = false, stopped = false, uploadStatus = 202;
  let serverState = {}, track, sessionNumber = 1;
  const intervals = new Map(), events = new Map(), trackEvents = new Map(), requests = [];
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', disabled: false, style: {}, value: '',
      readyState: 2, videoWidth: 640, videoHeight: 480,
      play: async () => {}, replaceChildren() {},
    });
    return elements.get(id);
  };
  function mediaStream() {
    track = {
      readyState: 'live', muted: false,
      stop() { this.readyState = 'ended'; },
      getSettings: () => ({deviceId: 'synthetic-camera'}),
      addEventListener: (type, listener) => trackEvents.set(type, listener),
    };
    const activeTrack = track;
    return {getTracks: () => [activeTrack], getVideoTracks: () => [activeTrack]};
  }
  const document = {
    hidden: false, getElementById: element,
    addEventListener: (type, listener) => events.set(type, listener),
    createElement: () => ({getContext: () => ({drawImage() {}}), toDataURL: () => 'synthetic-frame'}),
  };
  const snapshot = () => ({
    session_id: `test-session-${sessionNumber}`, can_start_new_session: stopped,
    stage: stopped ? 'stopped' : accepted ? 'ready' : 'idle', finished: stopped,
    status: stopped ? 'Stopped' : 'Ready', paused,
    remaining_seconds: accepted ? 100 : null, estimated_compute_usd: 0,
    frames: {accepted, completed: accepted}, freshness: 'unavailable',
    ...serverState,
  });
  const context = vm.createContext({
    document, window: {WEBCAM_SESSION_TOKEN: 'test', addEventListener() {}, location: {hash, pathname: '/'}, history: {replaceState() {}}},
    navigator: {mediaDevices: {
      getUserMedia: async () => mediaStream(),
      enumerateDevices: async () => [{kind: 'videoinput', deviceId: 'synthetic-camera'}],
    }},
    Option: function(label, value) { this.label = label; this.value = value; },
    AbortSignal, Date: class extends Date { static now() { return clock; } },
    // Browser timers reject being invoked as methods on LatestCaptureLoop.
    setInterval: function(callback, ms) {
      if (this?.constructor?.name === 'LatestCaptureLoop') throw TypeError('Illegal invocation');
      const id = ++nextTimer; intervals.set(id, {callback, ms}); return id;
    },
    clearInterval: function(id) {
      if (this?.constructor?.name === 'LatestCaptureLoop') throw TypeError('Illegal invocation');
      intervals.delete(id);
    },
    fetch: async (url, options) => {
      requests.push({url, options});
      if (url === '/status' && failStatus) { failStatus = false; throw TypeError('status offline'); }
      if (url === '/analyze') {
        if (failUpload) { failUpload = false; throw TypeError('upload offline'); }
        if (uploadStatus !== 202) return {ok: false, status: uploadStatus, json: async () => ({error: 'Authorization required'})};
        accepted++; paused = false;
      }
      if (url === '/pause') paused = true;
      if (url === '/stop') stopped = true;
      if (url === '/new-session') { stopped = false; accepted = 0; paused = false; serverState = {}; sessionNumber++; }
      return {ok: true, status: 202, json: async () => snapshot()};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../scripts/webcam_reasoner.js'), 'utf8'), context);
  await settle();
  return {
    element, get track() { return track; }, requests,
    serverState: value => { serverState = value; },
    accepted: () => accepted,
    click: async id => { assert.equal(element(id).disabled, false); await element(id).onclick(); await settle(); },
    tick: async () => { clock += 1000; for (const {callback} of [...intervals.values()]) callback(); await settle(); },
    visibility: async hidden => { document.hidden = hidden; events.get('visibilitychange')(); await settle(); },
    disconnect: async () => { track.readyState = 'ended'; trackEvents.get('ended')(); await settle(); },
    failStatus: () => { failStatus = true; }, failUpload: () => { failUpload = true; },
    rejectUploads: code => { uploadStatus = code; },
  };
}
async function start() { const f = await page(); await f.click('camera'); await f.click('live'); return f; }

test('the wired Live button continues submitting one fresh frame per tick', async () => {
  const f = await start();
  for (let i = 0; i < 5; i++) await f.tick();
  assert.equal(f.accepted(), 6);
  assert.equal(f.element('pause').disabled, false);
});

test('hiding the page suspends samples and returning resumes Live automatically', async () => {
  const f = await start();
  await f.visibility(true);
  await f.tick(); await f.tick();
  assert.equal(f.accepted(), 1);
  await f.visibility(false);
  await f.tick(); await f.tick();
  assert.ok(f.accepted() >= 3);
  assert.equal(f.element('pause').disabled, false);
});

test('a single failed status poll does not turn Live off', async () => {
  const f = await start();
  f.failStatus(); await f.tick();
  assert.match(f.element('age').textContent, /UNAVAILABLE/);
  await f.tick(); await f.tick();
  assert.equal(f.accepted(), 4);
  assert.equal(f.element('pause').disabled, false);
});

test('temporary camera warmup skips the missing frame and retries the next tick', async () => {
  const f = await start();
  f.element('video').readyState = 1;
  await f.tick(); assert.equal(f.accepted(), 1);
  f.element('video').readyState = 2;
  await f.tick(); await f.tick();
  assert.equal(f.accepted(), 3);
});

test('a temporary upload failure retries with a new frame on the next tick', async () => {
  const f = await start();
  f.failUpload(); await f.tick();
  assert.equal(f.element('pause').disabled, false);
  await f.tick(); await f.tick();
  assert.equal(f.accepted(), 3);
  assert.equal(f.element('error').textContent, '');
});

test('explicit Pause while hidden is not undone on returning to the page', async () => {
  const f = await start();
  await f.visibility(true); await f.click('pause');
  await f.visibility(false); await f.tick();
  assert.equal(f.accepted(), 1);
  assert.equal(f.element('pause').disabled, true);
});

test('camera disconnect and authorization failure still stop Live', async () => {
  const f = await start(); await f.disconnect(); await f.tick();
  assert.equal(f.accepted(), 1);
  assert.equal(f.element('pause').disabled, true);
  const g = await start(); g.rejectUploads(403); await g.tick(); await g.tick();
  assert.equal(g.accepted(), 1);
  assert.equal(g.element('pause').disabled, true);
});

test('Stop blocks further sampling even after visibility changes', async () => {
  const f = await start(); await f.click('stop');
  await f.visibility(true); await f.visibility(false); await f.tick();
  assert.equal(f.accepted(), 1);
  assert.equal(f.element('live').disabled, true);
});


test('muted video or temporarily missing dimensions wait without disabling Live', async () => {
  const f = await start();
  f.track.muted = true; await f.tick();
  assert.match(f.element('captureStatus').textContent, /waiting/);
  f.track.muted = false; f.element('video').videoWidth = 0; await f.tick();
  assert.equal(f.accepted(), 1);
  f.element('video').videoWidth = 640; await f.tick();
  assert.equal(f.accepted(), 2);
  assert.match(f.element('captureStatus').textContent, /2 captured/);
});

test('a temporary server error recovers, but Pause prevents automatic retry', async () => {
  const f = await start(); f.rejectUploads(503); await f.tick();
  assert.equal(f.element('pause').disabled, false);
  f.rejectUploads(202); await f.tick();
  assert.equal(f.accepted(), 2);
  f.failUpload(); await f.tick(); await f.click('pause');
  await f.tick(); await f.tick();
  assert.equal(f.accepted(), 2);
  assert.equal(f.element('pause').disabled, true);
});


test('GPU failure stops analysis but leaves the existing camera preview on', async () => {
  const f = await start();
  const activeTrack = f.track;
  for (const stage of ['stopping', 'stop-error', 'stopped']) {
    f.serverState({stage, finished: stage === 'stopped', error: 'resource exhausted'});
    await f.tick();
    const accepted = f.accepted();
    await f.tick();
    assert.equal(f.accepted(), accepted);
    assert.equal(activeTrack.readyState, 'live');
    assert.ok(f.element('video').srcObject);
    assert.equal(f.element('live').disabled, true);
    assert.equal(f.element('analyze').disabled, true);
    assert.match(f.element('serverError').textContent, /resource exhausted/);
  }
});

test('camera can be re-enabled and stays on while the inference session is stopped', async () => {
  const f = await start(); await f.click('stop');
  assert.equal(f.track.readyState, 'ended');
  assert.equal(f.element('video').srcObject, null);
  const posts = f.requests.filter(r => ['/pause', '/analyze', '/stop'].includes(r.url)).length;
  await f.click('camera');
  for (let i = 0; i < 3; i++) await f.tick();
  assert.equal(f.track.readyState, 'live');
  assert.ok(f.element('video').srcObject);
  assert.equal(f.element('live').disabled, true);
  assert.match(f.element('captureStatus').textContent, /preview/i);
  assert.equal(f.requests.filter(r => ['/pause', '/analyze', '/stop'].includes(r.url)).length, posts);
});


test('the explicitly requested Live link enables the camera and keeps submitting', async () => {
  const f = await page('#live');
  await settle();
  assert.equal(f.accepted(), 1);
  await f.tick(); await f.tick();
  assert.equal(f.accepted(), 3);
});


test('the preview link enables the camera without submitting a paid frame', async () => {
  const f = await page('#preview');
  await settle();
  assert.equal(f.track.readyState, 'live');
  assert.equal(f.accepted(), 0);
  await f.tick();
  assert.equal(f.accepted(), 0);
});


test('a stopped session can create another session and restart multi-frame Live without releasing preview', async () => {
  const f = await start();
  assert.equal(f.element('newSession').disabled, true);
  await f.click('stop');
  await f.click('camera');
  const preview = f.track;
  assert.equal(f.element('live').disabled, true);
  await f.click('newSession');
  assert.equal(f.track, preview);
  assert.equal(preview.readyState, 'live');
  assert.equal(f.accepted(), 0); // Creating the recording never starts compute.
  assert.equal(f.element('live').disabled, false);
  await f.click('live');
  await f.tick(); await f.tick();
  assert.equal(f.accepted(), 3);
  const last = f.requests.filter(r => r.url === '/analyze').at(-1);
  assert.equal(JSON.parse(last.options.body).session_id, 'test-session-2');
});

test('new session stays disabled until shutdown and the previous worker have finished', async () => {
  const f = await page();
  f.serverState({stage: 'stopping', finished: false, can_start_new_session: false});
  await f.tick();
  assert.equal(f.element('newSession').disabled, true);
  f.serverState({stage: 'stopped', finished: true, can_start_new_session: true});
  await f.tick();
  assert.equal(f.element('newSession').disabled, false);
  await f.click('newSession');
  assert.equal(f.element('live').disabled, true); // Camera is still required.
  await f.click('camera');
  assert.equal(f.element('live').disabled, false);
});


test('collision output stays concise and expires to unknown with old frames', async () => {
  const f = await page();
  f.serverState({freshness: 'recent', max_frame_age_seconds: 3,
    result: {captured_at: 100, observation: 'POSSIBLE COLLISION — person center, approaching.',
      collision: {state: 'possible', objects: [{track_id: 1, label: 'person', relative_motion: 'approaching', independent_motion: 'moving'}]}}});
  await f.tick();
  assert.match(f.element('observation').textContent, /^POSSIBLE COLLISION/);
  assert.match(f.element('motion').textContent, /person: approaching/);
  await f.tick(); await f.tick(); await f.tick();
  assert.match(f.element('observation').textContent, /^UNKNOWN/);
  assert.equal(f.element('motion').textContent, '');
});

test('unavailable inference suppresses a previous no-apparent-collision result', async () => {
  const f = await page();
  f.serverState({freshness: 'unavailable', max_frame_age_seconds: 3,
    result: {captured_at: 100, observation: 'NO APPARENT COLLISION DETECTED.',
      collision: {state: 'not_observed', objects: []}}});
  await f.tick();
  assert.match(f.element('observation').textContent, /^UNKNOWN/);
});

test('a status outage replaces a previous no-apparent-collision result', async () => {
  const f = await page();
  f.serverState({freshness: 'recent', max_frame_age_seconds: 3,
    result: {captured_at: 101, frame_id: 1, observation: 'NO APPARENT COLLISION DETECTED.',
      collision: {state: 'not_observed', objects: []}}});
  await f.tick();
  assert.equal(f.element('observation').textContent, 'NO APPARENT COLLISION DETECTED.');
  f.failStatus(); await f.tick();
  assert.match(f.element('observation').textContent, /^UNKNOWN/);
  assert.equal(f.element('motion').textContent, '');
});

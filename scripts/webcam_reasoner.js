/* One local upload in flight and one replaceable sampled frame. Completed analyses are recorded by the local server. */
'use strict';
class LatestCaptureLoop {
  constructor({capture, send, onError, onRetry = () => {}, onCapture = () => {}, intervalMs = 1000,
               now = Date.now, schedule = (callback, ms) => setInterval(callback, ms),
               cancel = timer => clearInterval(timer)}) {
    Object.assign(this, {capture, send, onError, onRetry, onCapture, intervalMs, now, schedule, cancel});
    this.running = false;
    this.uploading = false;
    this.pending = null;
    this.timer = null;
    this.generation = 0;
    this.replaced = 0;
  }
  start() {
    if (this.running) return;
    this.running = true;
    this.generation++;
    this.tick();
    if (this.running) this.timer = this.schedule(() => this.tick(), this.intervalMs);
  }
  stop() {
    this.running = false;
    this.generation++;
    if (this.timer !== null) this.cancel(this.timer);
    this.timer = null;
    this.pending = null;
  }
  tick() {
    if (!this.running) return;
    try {
      const frame = this.capture();
      // A hidden page or warming video has no new frame. Keep Live armed,
      // discard waiting work, and sample again on the next tick.
      if (!frame) { this.pending = null; return; }
      this.onCapture(frame);
      if (this.pending) this.replaced++;
      this.pending = {frame, generation: this.generation};
      void this.drain();
    } catch (error) {
      this.stop();
      this.onError(error);
    }
  }
  async drain() {
    if (this.uploading || !this.pending || !this.running) return;
    const {frame, generation} = this.pending;
    this.pending = null;
    // Never upload an old frame left behind by a sleeping or stalled browser.
    if (this.now() / 1000 - frame.captured_at > 3) return;
    this.uploading = true;
    try {
      await this.send(frame);
    } catch (error) {
      if (generation === this.generation && this.running) {
        if (error.retryable) {
          this.onRetry(error);
        } else {
          this.stop();
          this.onError(error);
        }
      }
    } finally {
      this.uploading = false;
      if (this.running && this.pending) void this.drain();
    }
  }
}
function frameFreshness(value, nowMs = Date.now()) {
  if (!value.result) return 'unavailable';
  const state = value.freshness || 'unavailable';
  if (state !== 'recent') return state;
  const captured = value.result.captured_at;
  const limit = value.max_frame_age_seconds;
  if (!Number.isFinite(captured) || !Number.isFinite(limit)) return 'unavailable';
  const age = nowMs / 1000 - captured;
  return age >= -0.5 && age <= limit ? 'recent' : 'stale';
}
if (typeof module !== 'undefined') module.exports = {LatestCaptureLoop, frameFreshness};

if (typeof document !== 'undefined') {
  const $ = id => document.getElementById(id);
  let stream = null, state = null, polling = false, postingSingle = false;
  let statusUnavailable = false, uploadUnavailable = false, samples = 0, restarting = false, requestEpoch = 0;
  async function post(path, payload) {
    let response, data;
    try {
      response = await fetch(path, {
        method: 'POST', headers: {'Content-Type': 'application/json',
          'X-Session-Token': window.WEBCAM_SESSION_TOKEN},
        body: JSON.stringify({...payload, session_id: state?.session_id}), signal: AbortSignal.timeout(10000),
      });
      data = await response.json();
    } catch (error) {
      error.retryable = true; // Retry with a fresh sample, never replay this image.
      throw error;
    }
    if (!response.ok) {
      const error = Error(data.error || 'Request failed');
      error.retryable = response.status >= 500 || [408, 429].includes(response.status);
      throw error;
    }
    return data;
  }
  const reportCamera = (event, error) => { void post('/camera-status', {event, error_name: error?.name || ''}).catch(() => {}); };
  const blocked = () => state && (state.finished || ['stopping', 'stopped', 'stop-error'].includes(state.stage));
  function controls() {
    $('live').disabled = !state || !stream || loop.running || blocked() || postingSingle || restarting || statusUnavailable;
    $('newSession').disabled = !state?.can_start_new_session || restarting || postingSingle || loop.uploading || statusUnavailable;
    $('newSession').textContent = restarting ? 'Creating session…' : 'New analysis session';
    $('pause').disabled = !loop.running;
    $('analyze').disabled = !state || !stream || loop.running || blocked() || postingSingle || restarting || statusUnavailable;
    $('analyze').textContent = state?.remaining_seconds == null ? 'Capture & analyze · start GPU' : 'Capture & analyze';
    $('live').textContent = loop.running ? 'Live active · 1 capture/sec' : 'Start live · 1 capture/sec';
    $('camera').textContent = stream ? 'Restart camera' : 'Enable MacBook camera';
    const video = $('video');
    $('captureStatus').textContent = blocked() ?
      (stream ? 'Camera preview only. Analysis session ended; click New analysis session before starting Live again.' :
        'Analysis session ended. Enable the camera for local preview; click New analysis session before starting Live again.') :
      !loop.running ? 'Live is off.' : document.hidden ?
      'Live waiting: page hidden. Sampling resumes automatically when you return; GPU billing continues.' :
      !stream || stream.getVideoTracks()[0]?.muted || video.readyState < 2 || !video.videoWidth || !video.videoHeight ?
      'Live waiting for a camera frame; retrying automatically.' :
      `Live sampling · ${samples} captured this page session.`;
  }
  function capture() {
    const video = $('video');
    if (document.hidden) { controls(); return null; }
    if (!stream || stream.getVideoTracks()[0]?.readyState !== 'live') {
      throw Error('Camera unavailable or disconnected. Enable the camera before resuming.');
    }
    if (stream.getVideoTracks()[0].muted || video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
      controls();
      return null;
    }
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, 640 / video.videoWidth, 480 / video.videoHeight);
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const captured_at = Date.now() / 1000;
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    return {image: canvas.toDataURL('image/jpeg', .8), captured_at};
  }
  function showCapture(frame) {
    samples++;
    controls();
    $('snapshot').src = frame.image;
    $('snapshot').style.display = 'block';
    $('frameLabel').textContent = `Latest sample: ${new Date(frame.captured_at * 1000).toLocaleTimeString()}`;
  }
  async function pause(message = '') {
    loop.stop();
    controls();
    if (message) $('error').textContent = message;
    try { await post('/pause', {}); }
    catch (error) { $('error').textContent = `${error.message}. The GPU timer may still be running; check Brev.`; }
  }
  const loop = new LatestCaptureLoop({
    capture, onCapture: showCapture,
    send: async frame => {
      const value = await post('/analyze', frame);
      if (uploadUnavailable) {
        uploadUnavailable = false;
        if (!statusUnavailable) $('error').textContent = '';
      }
      render(value);
    },
    onRetry: error => {
      uploadUnavailable = true;
      $('error').textContent = `${error.message}. Live is retrying fresh frames; GPU billing may continue.`;
      showUnavailable();
    },
    onError: error => { void pause(error.message); },
  });
  function stopCamera() {
    const old = stream;
    stream = null;
    if (old) old.getTracks().forEach(track => track.stop());
    $('video').srcObject = null;
    controls();
  }
  async function camera(deviceId) {
    reportCamera('requesting');
    if (loop.running || (stream && !blocked())) await pause();
    stopCamera();
    $('cameraError').textContent = 'Waiting for camera permission. Allow the browser/macOS prompt if shown.';
    if (!navigator.mediaDevices?.getUserMedia) throw Error('Use Safari or Chrome on your Mac at the printed 127.0.0.1 address.');
    stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {
      width: {ideal: 640}, height: {ideal: 480}, ...(deviceId ? {deviceId: {exact: deviceId}} : {}),
    }});
    const active = stream;
    active.getVideoTracks()[0].addEventListener('ended', () => {
      if (stream === active) {
        void pause('Camera disconnected. Observations are unavailable; reconnect the camera.');
        stopCamera();
      }
    });
    $('video').srcObject = stream;
    await $('video').play();
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'videoinput');
    const selected = stream.getVideoTracks()[0].getSettings().deviceId;
    $('devices').replaceChildren(...devices.map((device, index) => {
      const option = new Option(device.label || `Camera ${index + 1}`, device.deviceId);
      option.selected = device.deviceId === selected;
      return option;
    }));
    $('devices').disabled = false;
    $('camera').textContent = 'Restart camera';
    $('cameraError').textContent = '';
    reportCamera('ready');
    controls();
  }
  function render(value) {
    state = value;
    // Cloud failure/end blocks uploads, not the independently controlled preview.
    // Only explicit Stop, camera replacement/disconnect, or page exit releases it.
    if (blocked()) loop.stop();
    $('status').textContent = value.status;
    $('serverError').textContent = value.error || '';
    const log = value.analysis_log;
    $('analysisLog').textContent = log?.enabled ?
      `Session recording: ${log.path} · ${log.saved_this_session} saved this session${log.error ? ' · SAVE FAILED' : ''}` :
      'Analysis logging is not enabled in this server. Refresh after updating it.';
    $('cost').textContent = value.remaining_seconds === null ? 'Local analysis timer has not started. An already-running VM remains billable.' :
      `Auto-stop in ${Math.floor(value.remaining_seconds / 60)}m ${value.remaining_seconds % 60}s · estimated compute $${value.estimated_compute_usd.toFixed(3)} (plus storage)`;
    if (value.stage === 'stopping') $('cost').textContent = 'Stop requested; waiting for Brev to confirm STOPPED. Compute shutdown is not yet confirmed.';
    if (value.stage === 'stop-error') $('cost').textContent = 'Shutdown is not confirmed. Retry Stop or check Brev; the GPU may still be billable.';
    if (value.stage === 'stopped') $('cost').textContent = 'This local session ended after a confirmed GPU stop. A VM restarted separately may still be billable.';
    if (value.paused && value.remaining_seconds !== null && !blocked()) $('cost').textContent += ' Capture paused; the GPU remains billable until stopped.';
    const counts = value.frames || {};
    $('rate').textContent = `Target: 1 capture/sec · completed: ${value.processed_fps ?? 'measuring…'} FPS · analyzed: ${counts.completed ?? 0} · skipped waiting frames: ${(counts.replaced || 0) + (counts.expired || 0)}`;
    if (value.result) {
      const result = value.result, usage = result.usage || {};
      const current = frameFreshness(value) === 'recent';
      $('observation').textContent = result.collision && !current ?
        'UNKNOWN — current collision assessment unavailable.' : result.observation;
      $('motion').textContent = result.collision && current ?
        (result.collision.objects.length ? 'Camera-relative motion: ' : '') + result.collision.objects.map(obj =>
          `#${obj.track_id} ${obj.label}: ${obj.relative_motion}${obj.independent_motion === 'unknown' ? '' : ` (object ${obj.independent_motion})`}`).join(' · ') : '';

      $('metrics').textContent = `Frame ${result.frame_id} · captured ${new Date(result.captured_at * 1000).toLocaleTimeString()} · request ${result.request_latency_ms} ms · ${usage.prompt_tokens ?? '?'} input / ${usage.completion_tokens ?? '?'} output tokens`;
      const age = Math.max(0, Date.now() / 1000 - result.captured_at);
      const freshness = frameFreshness(value);
      const label = freshness === 'recent' ? 'RECENT PAST FRAME' : freshness.toUpperCase();
      $('age').textContent = `${label} · frame age ${age.toFixed(1)}s. This observation cannot establish a clear path.`;
    } else {
      $('observation').textContent = 'UNKNOWN — waiting for an analyzed frame.';
      $('motion').textContent = '';
      $('age').textContent = 'UNAVAILABLE · no analyzed frame. Silence does not mean the path is clear.';
    }
    controls();
    if (statusUnavailable || uploadUnavailable) showUnavailable();
  }
  function showUnavailable() {
    // Replace the observation itself: a previous "no apparent collision" must not stay on screen.
    $('observation').textContent = 'UNKNOWN — current assessment unavailable.';
    $('motion').textContent = '';
    $('age').textContent = 'UNAVAILABLE / STALE · do not use the previous observation as a current alert.';
  }
  async function status() {
    if (polling) return;
    polling = true;
    const epoch = requestEpoch;
    try {
      const response = await fetch('/status', {cache: 'no-store', signal: AbortSignal.timeout(4000)});
      if (!response.ok) throw Error('Status request failed');
      const value = await response.json();
      if (epoch !== requestEpoch) return;
      if (statusUnavailable) {
        statusUnavailable = false;
        if (!uploadUnavailable) $('error').textContent = '';
      }
      render(value);
    } catch (error) {
      if (epoch !== requestEpoch) return;
      statusUnavailable = true;
      $('status').textContent = 'Inference status unavailable; retrying automatically.';
      showUnavailable();
      $('error').textContent = 'Local server unavailable. Retrying; GPU billing may continue. Check Brev or run: brev stop hackutd-reasoner';
      controls();
    } finally { polling = false; }
  }
  $('camera').onclick = () => camera().catch(error => {
    reportCamera('error', error);
    $('cameraError').textContent = `Camera access: ${error.message}. Check browser permission and macOS Privacy & Security → Camera.`;
    controls();
  });
  $('devices').onchange = () => camera($('devices').value).catch(error => { $('cameraError').textContent = error.message; });
  $('newSession').onclick = async () => {
    loop.stop(); restarting = true; requestEpoch++; controls();
    $('error').textContent = '';
    try {
      const value = await post('/new-session', {});
      requestEpoch++; // Discard status replies captured during the rollover.
      samples = 0; uploadUnavailable = false;
      $('snapshot').style.display = 'none';
      $('frameLabel').textContent = ''; $('metrics').textContent = '';
      render(value);
    } catch (error) { $('error').textContent = error.message; }
    finally { restarting = false; controls(); }
  };
  $('live').onclick = () => { $('error').textContent = ''; loop.start(); controls(); };
  $('pause').onclick = () => { void pause(); };
  $('analyze').onclick = async () => {
    postingSingle = true; controls(); $('error').textContent = '';
    try {
      const frame = capture();
      if (!frame) throw Error('No current camera frame. Keep this page visible and wait for the preview.');
      showCapture(frame); render(await post('/analyze', frame));
    }
    catch (error) { $('error').textContent = error.message; }
    finally { postingSingle = false; controls(); }
  };
  $('stop').onclick = async () => {
    loop.stop(); stopCamera();
    try { render(await post('/stop', {})); }
    catch (error) { $('error').textContent = `${error.message}. Check Brev or run: brev stop hackutd-reasoner`; }
  };
  document.addEventListener('visibilitychange', () => {
    // Preserve the user's Live choice across tab switches. Hidden ticks clear
    // pending captures; a visible page samples a new frame immediately.
    loop.tick();
    controls();
  });
  window.addEventListener('pagehide', () => { loop.stop(); stopCamera(); });
  setInterval(status, 1000);
  // Explicit one-use link for an authorized camera test in the normal browser.
  // Existing browser/macOS camera permission is still required.
  const requestedMode = window.location?.hash;
  if (requestedMode === '#live' || requestedMode === '#preview') {
    window.history?.replaceState(null, '', window.location.pathname);
    void (async () => {
      await status();
      if (blocked() || statusUnavailable) return;
      await camera();
      if (requestedMode === '#live') { loop.start(); reportCamera('live'); }
      controls();
    })().catch(error => { reportCamera('error', error); $('cameraError').textContent = `Camera access: ${error.message}`; });
  } else {
    void status();
  }
}

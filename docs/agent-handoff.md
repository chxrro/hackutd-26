# NVIDIA vision agent handoff

Recorded November 7, 2026, for the owner-requested GitHub handoff.
Branch: `nathan/nvidia-collision-handoff`. Read `AGENTS.md` and
[project context](project-context.md) before making changes. This branch includes
teammate changes through `3e8d956` on `origin/main`; their hub, firmware, phone app,
and Camera Bridge were preserved. Merge through a pull request; do not self-merge.

## Owner requirements and current implementation

- Keep NVIDIA vision and report possible collision candidates concisely instead
  of describing scenes. Distinguish camera-relative approach, recession, crossing,
  and stillness using recent frames. No training was performed.
- `scripts/webcam_reasoner.py` serves the local MacBook camera experiment.
  `collision.py` requests at most four grounded object detections from NVIDIA
  Cosmos3-Edge on Brev; `visual_motion.py` uses local OpenCV optical flow and
  background motion checks. Only the latest image is sent to NVIDIA. Up to four
  local frames over five seconds provide motion history.
- Outputs are `POSSIBLE COLLISION`, `NO APPARENT COLLISION DETECTED`, or `UNKNOWN`.
  Stale frames, missing tracks, weak motion evidence, unavailable inference, and
  malformed detections remain explicit. Independent object motion is unknown
  unless background measurements support a stationary camera.
- General metric distance and physical time-to-collision are unknown (`null`).
  A model-only video-motion experiment misclassified synthetic recession/stillness
  as approach and is not used. This is experimental image-space assessment,
  not validated navigation, collision prediction, or a clear-path guarantee.
- Capture targets one frame/sec; analysis speed may vary. Do not stop the script
  because FPS is above or below one. There is one inference request plus the
  latest waiting frame. The normal 20-minute spending guards remain.
- Each server run or explicit new session creates numbered private sessions:
  `data/sessions/session_000001/`, etc. Completed analyses have downscaled JPEGs
  (max 320×240, reduced after inference), metadata, `frames.jsonl`, and an
  observations-only text file. No per-image archives are required. Earlier
  `data/analysis/` reports are preserved. See [recording details](session-recording.md).
- No live connection to belt hardware or Ray-Ban capture has been validated in
  this NVIDIA experiment. The separate teammate `hub/` uses a different perception
  baseline; its implementation does not establish NVIDIA-to-belt integration.

## Run and resume

From the repository root, using a separate environment from `hub/`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/webcam_reasoner.py
```

Open `http://127.0.0.1:8765` in a Mac browser with camera permission. Refresh after
code updates, enable the camera, then select **Start live**. Preview is local;
submitting the first frame starts the configured Brev VM and inference session.
If a prior session finished, choose **New analysis session**, then enable the
camera again. Restarting the VM does not reset a finished local app session.

At this handoff, read-only `/status` verification found the existing local server
idle in `session_000003`, with zero saved analyses and no error. It was left
running. This describes this Mac at verification, not a service another checkout
inherits. Check the port before starting a second server. Use `/status` without
copying private image payloads or page authorization tokens into Git.

Brev CLI authentication, SSH access, and model credentials/cache are local or
remote account state, not repository contents. See [Brev deployment](brev-reasoner.md)
for Compose setup and the private tunnel. The application targets the existing
`hackutd-reasoner` instance. Do not modify the teammate GPU instance.

## Costs and operational preferences

The owner permits reasoner restarts for testing, wants the script kept running,
and wants notice of actions expected to cost more than $3. The recorded compute
rate is $1.4544/hour (about $0.49 per 20-minute window), plus storage; these are
estimates, not current pricing or invoice totals. Pause does not stop billing.
Use **Stop GPU & camera** or `brev stop hackutd-reasoner` when stopping compute is
intended, and verify completion. Keep the local and remote spending guards.

The last inference milestone recorded a warm VM with a remote shutdown timer.
This handoff did not start inference or refresh that timer; verify live power,
health, and billing rather than assuming historical state. Do not add billing
credentials or recharge settings. A fresh machine needs its own authorized account
access. Camera recordings, calibration captures, weights, `.env`, keys and tokens
are deliberately absent from GitHub and remain private.

## Validation evidence and commands

After integrating teammate changes, the GitHub handoff reran and passed 68
Python tests, 27 JavaScript tests, Python compilation, JavaScript syntax,
dependency consistency, and the final branch whitespace check.
Actual NVIDIA calls on rendered targets classified approach and moderate crossing
as possible candidates, recession/stillness as no apparent collision, and fast
untrackable crossing as unknown. A production-worker smoke saved two paired
synthetic JPEG/metadata records. These are synthetic GPU/optical-flow checks,
not physical-camera collision accuracy. See [collision details](collision-check.md)
and [synthetic evidence](validation/collision-synthetic.json). An earlier smoke
assertion failed; its cause was not established, and a fresh guarded retry passed.

Local checks to reproduce without starting GPU compute:

```bash
.venv/bin/python -m unittest discover -s tests -q
node --test tests/test_webcam_loop.cjs tests/test_webcam_page.cjs
node --check scripts/webcam_reasoner.js
.venv/bin/python -m py_compile scripts/*.py
.venv/bin/python -m pip check
git diff --check origin/main...HEAD
```

Teammate hub checks are separate (`cd hub && uv run pytest`). Its environment and
OpenCV variant differ; do not combine them with the root NVIDIA environment.
The GitHub handoff did not rerun hub, firmware, iOS, physical camera, Ray-Ban,
network-to-belt, walking, metric-distance, or real collision-validation tests.
No new GPU calls are required just to reproduce the local test suites.

## Useful next work

1. Run controlled, consented physical-camera sequences with measured object and
   camera movement. Measure missed candidates, false alerts, track association,
   latency, low light, camera shake, and stale/disconnected behavior.
2. Investigate calibrated depth/range without fabricating metric values. The
   optional offline replay/calibration tools are separate from NVIDIA Live.
3. Coordinate a NVIDIA perception adapter with the hub owner before changing any
   shared protocol. Current `docs/protocol.md`, ports and belt commands were not
   changed in this work. Preserve obstacle direction as distinct from travel advice.
4. Validate bounded haptics and timeouts on a bench before a supervised demo.
   Never rely on this prototype for unsupervised navigation.

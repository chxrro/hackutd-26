# Session recordings and first CPU perception experiment

Implemented November 7, 2026. This remains an experimental aid; the new recording
and synthetic replay checks do not validate navigation or real-world range.

## Recording

Follow the README virtual-environment setup, then run:

```bash
.venv/bin/python scripts/webcam_reasoner.py
```

Each local server run allocates the next unused numbered folder (atomically,
including concurrent starts); preview-only runs can leave an empty session.
Pause/resume uses the same folder. After a confirmed stop and completion of the
old worker, **New analysis session** creates the next folder, resets the local
timer/counters, and enables Live when the camera is ready. It preserves preview
and makes no GPU requests; click Live/Analyze to reuse/start the VM. Restarting
the VM externally does not reset the local session. Old pages cannot submit to or
stop a replacement session: requests are checked against the active session ID.
Numbering does not reuse or overwrite an existing folder.

```text
data/sessions/session_000001/
  session.json
  frames.jsonl
  observations.txt
  images/
    frame_000001.jpg
    frame_000003.jpg
```

Only frames with completed model responses are saved. Gaps in frame numbers are
expected: the latest-frame queue replaces/skips frames before inference.
NVIDIA receives the current captured JPEG for object vision. Local optical flow
uses recent frames to assess motion; see [the collision check](collision-check.md).
After inference, Pillow makes an archival JPEG
at most 320×240, preserving aspect ratio, quality 75, with no EXIF. Input is not
reduced for archival convenience. These are normal image files; no ZIP is needed.
Actual bytes depend on scene content. No automatic retention/deletion is applied.

Each schema-version-2 JSONL record contains the observation, model, allowlisted
token counts, frame/session IDs, capture/completion times, latency and staleness,
plus `image.path`, dimensions, original dimensions, bytes and SHA-256. Live
collision records also include a schema-checked/allowlisted `collision` block with
candidate IDs, motion evidence, temporal references and explicit uncertainty;
`observations.txt` contains the short assessment, not a scene description. Paths are
relative to the session folder. `observations.txt` is maintained automatically
with observations separated by blank lines. `session.json` describes the format;
the authoritative record/count is the JSONL index, not a cached manifest total.

A reduced image is written and synced before its JSONL entry. All acknowledged
records have image+analysis; save errors are visible and request GPU shutdown.
A crash/disk error can leave an unindexed JPEG or extra observation, so use JSONL
to find complete pairs. Post-pause/stop completions remain recorded with their
inactive flag; that does not make them actionable. Rejected/failed frames are
not given fabricated analyses. Existing text-only data in `data/analysis/` stays
untouched; past images cannot be recovered.

Default data paths are Git-ignored. Session folders use 0700, image/metadata files
0600. Only the requested observation/metrics are serialized; auth headers and
arbitrary model metadata are excluded. Scenes/observations can be private. Use
consented scenes and delete the session folder when no longer needed. Keep custom
`--sessions-dir` or `--analysis-log` locations out of Git yourself.

## CPU replay: detection and object tracking

```bash
.venv/bin/python -m pip install -r requirements-perception.txt
.venv/bin/python scripts/fetch_perception_model.py
.venv/bin/python scripts/replay_perception.py data/sessions/session_000001 --annotate
```

No Brev call or GPU rental occurs in these commands. The downloader fetches a
3.8 MB ONNX detector pinned to OpenCV Zoo revision
`48b10fa505aeb51fbd127fc60a5c660040e823b7`, verifies its upstream LFS SHA-256, and
keeps the weights in ignored `models/`. The unmodified Apache-2.0 NanoDet helper,
license and attribution are in `scripts/vendor/nanodet/`. Primary source:
<https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/object_detection_nanodet>.

Replay verifies image checksums, letterboxes to the detector's 416×416 input,
and maps boxes back to saved-image coordinates. A simple class-aware greedy IoU
tracker assigns IDs, uses capture time to expire tracks after a three-second gap,
and reports missing tracks. It requires increasing timestamps. There is no
re-identification or camera-motion compensation: occlusion, fast movement,
low FPS and crossings can switch IDs. Empty detections do not mean clear space.
The upstream detector uses its default confidence threshold and NMS; small,
transparent, reflective, uncommon objects and drop-offs may be missed. Reduced
JPEGs can lose small hazards, so replay does not establish full-input performance.

Every replay allocates `perception_000001.jsonl`, `perception_000002.jsonl`, etc.
inside the selected session, without overwriting analyses or images. `--annotate`
also writes ordinary JPEGs in a correspondingly numbered folder. The replay
configuration is the first record; later records link the source frame and image,
tracks, missing states, timing and original inference staleness/activity flags.
All outputs are explicitly offline replay. They are not fed into the live page.

Direction describes the obstacle's position in the image. It is not a travel
instruction or a body-relative belt command. Box expansion is only image-motion
evidence; head/camera movement and detector jitter can produce it. Ordinary-object
range is `unknown`, collision risk is always `unknown`, and path state is
`unverified`. No haptic commands or TTC claims are produced.

## Metric distance: controlled target experiment

First generate printable targets locally:

```bash
.venv/bin/python scripts/calibrate_camera.py targets
```

Print `data/calibration/targets/checkerboard_9x6.png` flat. Measure one printed
square side in meters. Capture at least eight **different** sharp views from the
same camera/crop/zoom/focus: board tilted both ways and covering the center and
edges. More diverse views are preferable. Put the JPEG/PNG photos in an ignored
folder such as `data/calibration/views/`. The tool does not open the camera or
start inference to collect these photos. Then fit intrinsics, replacing the size
below with your actual measurement:

```bash
.venv/bin/python scripts/calibrate_camera.py fit data/calibration/views --square-size-m 0.025
```

The board has 9×6 inner corners (10×7 squares). Calibration refuses fewer than
eight detected views, mixed resolutions, non-finite results or fit RMS over 2px.
Those are prototype quality gates, not proof of accurate range. Duplicate views
can still fit poorly constrained intrinsics. Confirm diversity and validate using
independent target distances before trusting the numbers.

Print `aruco_4x4_50_id0.png` with its white margin. Measure the **outer black square**
side (excluding the white margin). Put it flat on an object in a stationary,
controlled scene. Use the matching camera calibration and actual target size:

```bash
.venv/bin/python scripts/replay_perception.py data/sessions/session_000001 \
  --calibration data/calibration/camera.json --marker-size-m 0.10 --annotate
```

This detects ArUco 4×4/50 targets and uses OpenCV IPPE square pose with the known
size. It reports straight-line camera-to-marker-center distance and camera-axis
forward depth separately. Intrinsics are scaled for the saved resolution; a
changed aspect ratio is rejected. A same-aspect crop or different camera cannot
be detected automatically: use the exact calibrated camera/view. Targets smaller
than 20px per side, clipped targets, poor reprojection (>2px), behind-camera or
materially ambiguous poses yield unknown range. Passing these checks labels range
`experimental`, never validated. It measures the marker, not an ordinary object,
nearest surface, floor gap, body clearance or collision course. Compression,
printing, target flatness, focus and calibration errors remain relevant.
Primary sources:
<https://docs.opencv.org/4.x/d5/dae/tutorial_aruco_detection.html>,
<https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html>.

For held-out checks, independently measure distances from the camera center to
the marker center. Make `data/calibration/references.json` with actual values:

```json
[
  {"frame_id": 1, "label": "aruco_4x4_50:0", "meters": 1.0},
  {"frame_id": 3, "label": "aruco_4x4_50:0", "meters": 2.0}
]
```

The numbers above show the format; they are not project measurements.
Evaluate the matching replay:

```bash
.venv/bin/python scripts/evaluate_ranges.py \
  data/sessions/session_000001/perception_000002.jsonl \
  data/calibration/references.json --output data/calibration/range_report.json
```

The report includes coverage (including unknown/missing targets), per-sample
error, mean absolute error and worst error over available estimates. Test multiple
ranges, angles, lighting conditions and movement on held-out measurements. No
real measurement report exists yet. General obstacle depth, validated tracking,
collision policy, head/body orientation and bounded belt actuation remain future
work; this prototype should only be tested in controlled, supervised settings.

## Checks

```bash
.venv/bin/python -m unittest discover -s tests -q
node --test tests/test_webcam_loop.cjs tests/test_webcam_page.cjs
node --check scripts/webcam_reasoner.js
.venv/bin/python -m py_compile scripts/*.py
git diff --check
```

Perception tests skip if the optional OpenCV dependencies are absent; the actual
CPU-model replay test skips until the model has been downloaded. Synthetic tests
check original inference input versus reduced archive, paired records, private
permissions, concurrent numbering, preservation, post-pause completion, disk
errors, track identity/missing/expiry, synthetic marker geometry, scaled intrinsics,
unknown range, and measurement coverage. They do not replace camera/range trials.

# NVIDIA vision and experimental collision checks

Implemented November 7, 2026. This is an image-space prototype, not validated
navigation, metric ranging, or a belt controller.

## Live behavior

The app now displays one short assessment:

- `POSSIBLE COLLISION — person center, approaching.`
- `POSSIBLE COLLISION — chair left, crossing.`
- `NO APPARENT COLLISION DETECTED.`
- `UNKNOWN — motion is uncertain.`

Directions locate an object in the image; they are not travel instructions.
No apparent collision means no candidate found in the available measurements,
not a clear path. An uncertain/stale/disconnected result is explicitly unknown.
The old scene-description prompt is replaced in the live application. The
standalone image CLI also defaults to a concise assessment, but its single-image
answer cannot establish motion.

## Which component does what

1. **NVIDIA Cosmos3-Edge on the Brev GPU** receives the latest camera JPEG and
   returns up to four object labels and normalized bounding boxes. Short grounded
   detections replace long prose. The client bounds/validates coordinates and
   labels, handles Cosmos native coordinate tokens, and suppresses overlapping
   duplicate detections. Truncated or malformed responses become unknown for
   that frame; they do not certify a clear path or shut down the capture loop.
2. **OpenCV on the Mac** compares recent captured images inside NVIDIA's boxes.
   Lucas–Kanade optical flow has a forward/backward consistency check, followed
   by a RANSAC partial affine fit. This measures apparent scale change and
   lateral displacement. No local recognition model replaces NVIDIA in Live.
   The earlier NanoDet replay remains a separate optional offline experiment.
3. **Tracking and a small deterministic policy** keep candidate IDs, associate
   boxes against measured projected positions, and inspect central-view overlap
   and crossing motion. A separate background-flow fit estimates whether the
   camera is moving. Object movement relative to the camera is always separated
   from inferred independent object movement; the latter stays unknown unless
   the background supports a stationary-camera estimate.

Only the latest image goes to NVIDIA on each call. A bounded local history of
up to four captured images over five seconds supports temporal measurements and
track history; the model is not asked to invent motion from a single image.
Capture timestamps, not assumed FPS, determine elapsed time. Pause/generation
changes, resolution changes and long gaps reset temporal context. Missing tracks
are explicit; a recently lost collision candidate remains unknown until expiry.
The usual one-request/one-latest-waiting-frame bounds and 20-minute GPU guards
remain. There is no FPS-triggered stop. 1 Hz capture can miss fast events.

A native NVIDIA video-input experiment was tested first. In simple rendered
clips it labeled a receding and stationary circle as approaching. Consequently,
that semantic motion output is not used in the deployed policy. NVIDIA still
supplies vision, while actual image changes supply motion evidence. The final
request uses a small bounded detection schema with thinking disabled and a
320-token output cap. These prototype settings are not an accuracy guarantee.

## Policy and uncertainty

The central horizontal image interval is 0.35–0.65. A measured approaching object
intersecting it is a possible collision candidate. A laterally moving object's
current/swept image interval is checked using a two-second inspection horizon.
This is a view-space proxy, not a predicted physical collision or stopping margin.
A stationary object alone is not classified as approaching merely because it
occupies the frame. No metric distance or physical TTC is reported: `range_m`
and `ttc_seconds` remain null.

Optical flow needs at least six consistent feature points, with at least 70%
fit inliers and spatial spread. Forward/backward feature error must be under
one pixel at the 320-pixel analysis width. A log-scale-change rate over 0.04/sec
is approaching, below -0.04/sec is receding. Horizontal rates over 0.02 image
widths/sec indicate lateral movement. Smaller ambiguous changes are unknown;
a narrow low-motion band is labeled still. These are testable prototype
parameters, not established safety thresholds. Camera estimation additionally
requires at least twelve background inliers. A plain background cannot establish
that the camera is stationary.

Low texture, darkness, blur, occlusion, changing labels, fast movement, crowding,
partial detections and head/camera motion can invalidate tracking. Perspective
or object rotation can change apparent size without radial approach. NVIDIA
can miss or mislocalize objects; the four-object budget can omit hazards. Drop-offs,
transparent/reflective objects and real-body clearance are not solved. No alert is
proof of safe passage, and no motor commands are generated.

A response older than the existing three-second freshness limit is saved as
unknown, retaining its candidate state as historical evidence. The page also
expires a previously recent assessment to unknown as time passes or inference
becomes unavailable. Three seconds is a developer freshness setting, not an
approved deadline for a wearable alert.

## Recording and setup

Use the README virtual environment and install `requirements.txt`; OpenCV is now
a live dependency for motion, along with Pillow. Start/restart the local server
and refresh the page. After a confirmed prior stop, **New analysis session**
creates a numbered folder; **Start live** begins/reuses GPU inference.

The normal private recording format remains intact. `observations.txt` now
contains short collision summaries. Each `frames.jsonl` record also has an
allowlisted `collision` block with state/reason, candidate IDs, normalized boxes,
camera-relative/independent motion estimates, feature/fit evidence, null
range/TTC, and IDs/timestamps of available temporal frames. Source JPEGs are still
archived after analysis at at most 320×240. Old logs are not rewritten.
No long-lived video file or unbounded image history is added.

## Evidence and next validation

Controlled synthetic images through the real GPU and measured flow produced:
approach → possible; receding → no apparent collision; stationary → no apparent
collision; moderate crossing → possible; fast untrackable crossing → unknown.
The final per-request latency samples were approximately 0.9–1.6 seconds. The
synthetic report is in `docs/validation/collision-synthetic.json`. These are
rendered geometric targets, not real people, a measured accuracy benchmark,
Ray-Ban input, a walking test, or physical collision validation.

Unit/integration cases cover multiple objects with distinct candidate IDs and
motions, stationary versus camera movement, crossing/receding, missing hazards,
insufficient features, fast movement, long gaps, pause resets, malformed output,
stale completions, private JPEG pairing and stale UI output. Use the README
checks with `.venv/bin/python` and the Node tests.

The next useful test is seated, with a consenting person moving a textured object
slowly toward/away/across the camera while someone observes the results. Then
check multiple objects, camera panning, low light and occlusion. Do not rely on
this prototype for unsupervised navigation or drive belt hardware from it yet.

Primary references:

- NVIDIA model card: <https://huggingface.co/nvidia/Cosmos3-Edge>
- OpenCV Lucas–Kanade tracking example:
  <https://github.com/opencv/opencv/blob/4.x/samples/python/lk_track.py>
- OpenCV affine fitting API:
  <https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html>

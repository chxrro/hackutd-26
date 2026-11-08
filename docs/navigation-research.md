# Navigation guidance through the belt: research and proposal

Status: **research and proposal only (2026-11-07).** Nothing here is built,
decided or tested on hardware. It is written for the team to decide on. It
does not change [protocol.md](protocol.md), the firmware, the hub, the phone
app or [project-context.md](project-context.md).

> **Status update (2026-11-08).** The hold's condition is met: obstacle alerts
> run end to end in simulation (hub sim world, PR #19) and live from the
> glasses to phone vibration (Ray-Ban test session, 2026-11-08). A **phone-only
> first slice** is now built in `meta/HapticBelt/Navigation/` (see §11):
> spoken route directions from the phone's GPS and compass, which go quiet
> whenever the hub reports an obstacle or fault. It needs **no protocol
> change**. Still not built: belt guidance ticks, the hub guidance composer and
> the `location` message. Every protocol change in this doc is still a
> **proposal**; none of it is in [protocol.md](protocol.md).
>
> *Original note (owner decision, 2026-11-07): don't start the route follower,
> the guidance composer or the protocol `location` message until obstacle
> alerts work end to end in simulation.*

Today the belt only gives obstacle alerts: "a motor's vibration = an obstacle
in that direction, relative to the glasses camera." This doc asks how the same
5-motor, front-only belt could also say "go this way" without breaking that
meaning.

> **Safety framing.** This is an experimental, supervised prototype. Guidance
> is a convenience cue for a supervised demo, not a navigation or safety
> system. Nothing below should be read as making a route safe. Every design
> here is constrained so that guidance **never delays, masks, replaces or
> hides** an obstacle alert or the fault pattern, and so that silence still
> never means "clear path."

## How to read this doc

| Tag | Meaning |
|---|---|
| **[Cited]** | Stated by the linked source. "(abstract)" means only the abstract or official summary was read, not the full paper. |
| **[Unverified]** | From a secondary source, a search snippet, or a page that couldn't be opened. Check before relying on it. |
| **[Repo]** | Read from this repository's code or docs at commit `72c6ded`. |
| **[Checked]** | A small throwaway experiment run for this doc (details given). |
| **[Proposal]** | My design suggestion. Not decided, not tested. Starting values are starting points, not validated. |

Sources were read on 2026-11-07. Pricing and terms change, so re-check them before relying on them.

---

## 1. Summary

- **Literature [Cited]:** separate meanings by **rhythm**, not by intensity or
  motor position. Give obstacles priority. Don't play two cues at once. Keep
  guidance cues sparse (about 1 s or slower while walking).
- **Recommended demo [Proposal]:** "follow the tags." Printed AprilTags mark 1–3
  waypoints in a cleared room. The hub finds the next tag in the glasses frame
  and plays a **short single tick** on the motor toward it, about once a
  second. Ticks play **only while the obstacle output is completely silent**.
  The fault pattern overrides everything.
- **Cost of that demo [Proposal]:** it needs **no BLE byte changes and no
  firmware changes**, because the hub can make ticks with today's
  `levels`-at-15 Hz resend. It does change the *meaning* section of
  `protocol.md`, so it still needs team sign-off.
- **Outdoor routing in general:** it needs torso heading (best from an IMU on
  the belt, which is a protocol change) and a routing source whose terms fit
  a belt-only experience. Apple MapKit and Google both have terms problems
  here (see §5).
- **Campus video demo, ECSW → Comets LANding [Proposal]:** use UTD's own
  campus map (UTD gave explicit permission at the event) to set a 6–8
  waypoint route of about 320 m. The phone sends GPS and heading, the hub
  ticks toward the next waypoint, and an AprilTag at the door handles the
  last few metres. The build and test plan is in §9. It is on hold until
  obstacle alerts work end to end in simulation.

---

## 2. Current system: what guidance would have to coexist with

All of this is **[Repo]**.

| Signal the wearer can feel today | Pattern | Where defined |
|---|---|---|
| Obstacle in a direction | Per-motor **continuous** vibration, PWM 90–255. Stronger = closer or sooner. Rises instantly, fades slowly. | `hub/haptic_hub/haptics.py`, `config.py` (`HapticsConfig`) |
| Fault ("can't vouch for output") | **All 5 motors**, double pulse 150 ms on, 150 off, 150 on, every 2 s, level 180 | `esp32/lib/belt_core/src/haptic_controller.h` (`HapticTiming`) |
| Link lost or TTL expired | Same fault pattern for 30 s, then silent | `haptic_controller.cpp` |
| Boot self-test | Each motor 120 ms, left to right | `esp32/include/config.h` |
| Nothing to report | Silence. This means "no alert", not "clear path". | `docs/protocol.md` |

Relevant mechanics:

- **Command timing.** The hub's belt loop sends a full `levels` command
  at **15 Hz** (`send_hz`), so every ~67 ms. Each command is valid for
  `ttl_ms = 250`, and writes are BLE write-without-response
  (`hub/haptic_hub/belt/ble.py:105`). The firmware holds the last levels until
  the next command or the TTL. Any rhythm the hub makes is therefore quantised
  to ~67 ms steps, plus BLE jitter, which has **not been measured**.
- **Priority today.** `HapticPolicy.command()` returns the fault command
  before anything else when health is bad. A guidance layer can sit after
  that function without touching perception.
- **Direction frame.** Sectors are 5 equal pixel strips of the camera image
  (`perception/geometry.py`). The hub has **no field-of-view value**, so
  sectors are image positions, not degrees. Obstacle direction is **relative
  to the head (camera)**, while the motors sit on the **torso**.
- **Detected boxes.** YOLO + ByteTrack boxes (`TrackedBox`: track id,
  label, confidence, box) exist inside perception
  (`perception/models.py:38-84`). They only leave it through
  `PerceptionResult.debug["boxes"]`, which is documented as never sent
  anywhere (`types.py:36`). Boxes are in the 384-px-wide inference frame,
  not the 504-px glasses frame.
- **Phone status decoding.** The phone decodes status with explicit
  `CodingKeys` (`meta/HapticBelt/Hub/HubMessages.swift`). Reading the code, an
  additional status field would be ignored rather than break decoding. This
  was not tested.

---

## 3. Question 1: keeping "obstacle here" and "go here" separate

### 3.1 What prior work found

| Source | Relevant finding |
|---|---|
| Katzschmann, Araki & Rus, "Safe local navigation for visually impaired users with a time-of-flight and haptic feedback device" (ALVU), *IEEE TNSRE* 26(3), 2018. [PDF](https://ethz.ch/content/dam/ethz/special-interest/mavt/robotics-n-intelligent-systems/srl-dam/documents/Publications/journal/katzschmann2018safe.pdf), doi:10.1109/TNSRE.2018.2800665 | **[Cited]** Five motors across the front of the upper abdomen, almost our layout. Distance shown with discrete pulse rate and strength levels, which users found easier than gradual changes. Special obstacles (stairs) get their own rhythms and **overwrite** the centre motor. Obstacle-only, no route guidance. Treats no vibration as free space, which this repo's rules forbid. |
| Romeo, Pissaloux et al., "MAPS/TactiBelt", *Sensors* 22(9):3316, 2022. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC9131141/) | **[Cited]** The closest match to our problem. **Continuous** vibration for the nearest obstacle, **discontinuous (pulsed)** vibration for the next point to reach, and a special pattern for the destination. The 7 participants were blindfolded sighted people, not blind users. They reported the two signals as clearly distinct. The paper doesn't describe how simultaneous cues are prioritised. |
| Wang, Katzschmann et al., ICRA 2017, "Enabling independent navigation for visually impaired people through a wearable vision-based feedback system". [PDF](https://ethz.ch/content/dam/ethz/special-interest/mavt/robotics-n-intelligent-systems/srl-dam/documents/Publications/Conference/wang2017enabling.pdf) | **[Cited]** In chair-finding mode, side motors were **reassigned** to mean "chair this way" while the front motor meant obstacle. This is the mode-dependent meaning AGENTS.md rules out, so treat it as a counter-example. |
| Shoval, Borenstein & Koren, "Auditory guidance with the NavBelt", *IEEE TSMC-C* 28(3), 1998. [PDF](https://ykoren.engin.umich.edu/wp-content/uploads/sites/122/2023/05/Auditory_Guidance_w_the_NavBelt-_A_computerized_travle_aid_for_the_blind.pdf) | **[Cited]** Audio, not haptic. "Image mode" showed obstacles; "guidance mode" gave one obstacle-aware recommended direction. Subjects found image mode far more demanding, but blind users preferred it in unfamiliar places so they stayed "in the loop". |
| Brewster & Brown, "Tactons", AUIC 2004. [eprints](https://eprints.gla.ac.uk/3443); Brown, Brewster & Purchase, World Haptics 2005. [record](https://www.sciweavers.org/node/112294) | **[Cited] (abstract)** Tactile messages are built from rhythm, location, amplitude and duration. Rhythm was identified 93% of the time vs 80% for roughness. |
| Gallace, Tan & Spence, *Perception* 35(2), 2006, doi:10.1068/p5380; *Psychon. Bull. Rev.* 13(2), 2006, doi:10.3758/bf03193847 | **[Cited] (abstract)** People can't count simultaneous tactile points at a glance, and they miss changes between tactile patterns ("tactile change blindness"), worst of all when a masking stimulus comes in between. Argues against playing two cues at once. |
| Tsukada & Yasumura, "ActiveBelt", UbiComp 2004. [PDF](http://mobiquitous.com/pub/ubicomp2004-activebelt.pdf) | **[Cited]** 8 motors, with direction shown by which motor buzzes and distance by pulse interval. Walking subjects often missed pulse intervals under 500 ms; at 1000 ms or more they noticed changes, though they tended to stop before turning. 6 subjects. |
| Cosgun, Sisbot & Christensen, IEEE Haptics Symposium 2014, doi:10.1109/HAPTICS.2014.6775483; ICRA 2014, doi:10.1109/ICRA.2014.6907796 | **[Cited] (abstract)** Most of 15 subjects preferred two intermittent pulses for direction, though continuous cues gave the lowest angular error. Rotational patterns were recognised almost perfectly. Reaction times were about 1–2 s. |
| Pielot, Henze, Heuten & Boll, "Evaluation of continuous direction encoding with tactile belts", HAID 2008. [record](https://www.offis.de/offis/publikation/evaluation-of-continuous-direction-encoding-with-tactile-belts.html) | **[Cited] (abstract)** Blending between neighbouring motors helped perceived direction accuracy. Discrete motors were better for waypoint navigation and easier to process. |
| Heuten, Henze, Boll & Pielot, "Tactile Wayfinder", NordiCHI 2008. [record](https://www.offis.de/offis/publikation/tactile-wayfinder-a-non-visual-support-system-for-wayfinding.html) | **[Cited] (abstract)** A belt showing direction and path deviation let people follow routes well on an open field. |
| Pielot et al., "A Tactile Compass for Eyes-free Pedestrian Navigation", INTERACT 2011. [record](https://www.offis.de/offis/publikation/a-tactile-compass-for-eyes-free-pedestrian-navigation.html) | **[Cited] (abstract)** Direction encoded as rhythmic patterns, distance as the pause between them. Navigation performance matched a map, with less distracting interaction. |
| PocketNavigator, Pielot et al., CHI 2012, doi:10.1145/2207676.2208728 | **[Unverified]** (via [NaviRadar, UIST 2011](https://www.medien.ifi.lmu.de/pubdb/publications/pub/ruemelin2011uist/ruemelin2011uist.pdf)) Uses temporal patterns, including **three short pulses = turn around**. |
| feelSpace naviBelt [user manual (2022)](https://feelspace.de/wp-content/uploads/2022/02/2022_user_manual_feelSpace_naviBelt_barrier-free.pdf); Brandebusemeyer et al., *Sensors* 2021, [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC8587958) | **[Cited]** Direction to the destination or route = continuous vibration of one unit. Arrival, poor GPS and connection status each use **distinct multi-location temporal patterns**. The 2021 study: 11 blind users over 7 weeks reported more confidence, but only 4 had analysable behavioural data, the belt did no obstacle sensing, and one author works for feelSpace. |
| van Erp et al., "Waypoint navigation with a vibrotactile waist belt", *ACM TAP* 2(2), 2005, doi:10.1145/1060581.1060585 | **[Unverified]** (paywalled; abstract seen only in snippets) Showing direction by which motor vibrates worked without training. Coding distance as rhythm didn't beat a no-distance control. |
| Flores et al., "Vibrotactile guidance for wayfinding of blind walkers", *IEEE ToH* 8(3), 2015. [PubMed](https://pubmed.ncbi.nlm.nih.gov/25781953) | **[Cited] (abstract)** With 10 blind users, belt guidance followed paths more closely than audio but was slower. |

Caveats: most of these studies are small and use blindfolded sighted people
under supervision. None of them validates safety. **[Cited]** None of the
belt studies found tested an obstacle cue and a guidance cue on the
**same front-only 5-motor array**, so our exact case is untested.

### 3.2 Proposed pattern vocabulary [Proposal]

The rule: **obstacle = continuous, guidance = a brief tick, fault = an
all-motor double pulse.** Each guidance pattern uses at most one motor at a
time (except "arrived"), so it can't be mistaken for the all-motor fault. No
guidance pattern is a continuous vibration, so none can be mistaken for an
obstacle.

| Name | Feel | Meaning | Starting values (untested) |
|---|---|---|---|
| Obstacle (unchanged) | Continuous, graded, any motors | Obstacle in that direction | as today |
| Fault (unchanged) | All motors, double pulse every 2 s | Can't vouch for output | as today |
| **G1 Target tick** | One short tick on **one** motor, repeating | "Your target is in this direction" | tick ≈ 2 hub frames (~133 ms); period ≈ 1.2 s (ActiveBelt: intervals under 500 ms were missed while walking); one fixed level, tuned on the bench to be clearly felt but not alarming |
| **G2 Turn around** (route guidance only) | Three quick ticks on the centre motor, then a pause | Target is behind you | 3 ticks of ~133 ms (2 hub frames) with equal gaps; repeat every ~2 s. PocketNavigator precedent (unverified). Must be checked against the fault double pulse: different motor count, different count of pulses |
| **G3 Arrived** | Alternating outer motors 0 → 4 → 0 → 4, once | Waypoint reached; guidance moves to the next one or stops | feelSpace precedent: alternating arrival pattern |
| No guidance | Nothing | Guidance is off, paused, or the target isn't visible. **Not** "go straight" and **not** "clear path." | Shown on the phone and in the debug view; the supervisor announces it in the demo |

Deliberately avoided: double pulses (too close to fault), sweeps across all
motors (too close to the boot self-test), continuous guidance (would read as
an obstacle), intensity to mean "distance to target" (intensity already means
obstacle urgency, and the literature favours rhythm).

### 3.3 Priority rules [Proposal]

In order. Each rule must be a unit test before any hardware run:

1. **Fault beats everything.** If the hub is unhealthy, guidance is not
   evaluated at all (today's `HapticPolicy` already returns fault first).
   Belt-side TTL and link-lost behaviour stay as they are.
2. **Any obstacle output beats guidance, globally.** If *any* motor has a
   non-zero obstacle level in this frame, including the fade tail, the frame
   is pure obstacle output. A guidance tick in progress is **cut off
   immediately**. Obstacle onset never waits for a tick to finish.
3. **Hold-off after obstacles.** Guidance may resume only after the obstacle
   output has been silent for a hold-off, starting at about one guidance
   period (~1 s). This stops a tick being felt as the end of the alert
   (tactile masking).
4. **Guidance from fresh data only.** A target observation older than a
   staleness limit (to tune; it should be short relative to the tick period)
   means no guidance. The hub keeps saying "no guidance", never "keep
   going."

Consequence: **in a cluttered space guidance will rarely play.** With
`far_m = 2.5 m`, anything within about 2.5 m in the central band triggers
obstacle output. That is the correct failure direction (safe and quiet), but
the demo room has to be set up with clear space, and walking up to a
wall-mounted target will hand over to obstacle alerts before "arrived". See
open questions.

**Considered and rejected for now:** NavBelt-style "recommended free
direction", where the hub picks an obstacle-free direction nearest the
target. It is less demanding for the user **[Cited, NavBelt]**, but buzzing
"go here, it's free" amounts to claiming a path is clear, which this
repository's safety rules forbid with unvalidated perception.

---

## 4. Question 2: heading and orientation

### 4.1 Sources of heading

| Source | What it measures | Notes |
|---|---|---|
| **iPhone `CLHeading`** ([docs](https://developer.apple.com/documentation/corelocation/clheading)) | **[Cited]** "The orientation of the user's device." `headingAccuracy` < 0 means invalid, e.g. uncalibrated or "strong interference from local magnetic fields" ([docs](https://developer.apple.com/documentation/corelocation/clheading/headingaccuracy)). `headingOrientation` ignores `faceUp`/`faceDown` ([docs](https://developer.apple.com/documentation/corelocation/cllocationmanager/headingorientation)). | A phone in a pocket gives the pocket's heading, not the walker's. Usable only if the phone is clipped to the torso in a known orientation. |
| **iPhone `CLLocation.course`** ([docs](https://developer.apple.com/documentation/corelocation/cllocation/course), [courseAccuracy](https://developer.apple.com/documentation/corelocation/cllocation/courseaccuracy)) | **[Cited]** "The direction in which the device is traveling." Negative means invalid. | Direction of travel, derived from location. Useless indoors and when standing still (implied by "traveling"; **[Unverified]** that Apple states it outright). |
| **CoreMotion** `xMagneticNorthZVertical` ([docs](https://developer.apple.com/documentation/coremotion/cmattitudereferenceframe/xmagneticnorthzvertical)) | **[Cited]** Device attitude referenced to magnetic north. | Still the phone's orientation. Same pocket problem. |
| **Glasses, Meta DAT Motion module** ([repo](https://github.com/meta/meta-wearables-dat-ios), [`MotionSample`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/1.0/mwdatmotion_motionsample)) | **[Cited]** Since DAT 1.0.0 (2026-09-24): accelerometer, gyroscope, magnetometer and an orientation quaternion, marked **experimental**. The repo's docs say it must be enabled in the Wearables Developer Center and can't ship to production channels yet. | Measures the **head**, not the torso. **[Unverified]**: the quaternion's reference frame (magnetic north or arbitrary), which models support it, how fresh the samples are, and whether Developer Mode alone is enough. The team chose to skip the Developer Center ([project-context.md](project-context.md)), so this may conflict with that decision. |
| **IMU on the belt** e.g. CEVA BNO085 ([datasheet](https://www.ceva-ip.com/wp-content/uploads/BNO080_085-Datasheet.pdf), [Adafruit](https://learn.adafruit.com/adafruit-9-dof-orientation-imu-fusion-breakout-bno085/report-types)) | **[Cited]** Rotation Vector "referenced to magnetic north and gravity" (datasheet nominal 3.5° dynamic / 2.0° static error). Game Rotation Vector has no heading reference and drifts. The datasheet warns of distortion near "ferrous or magnetic material". | Measures the **torso**, the frame the motors are in. Problem: **the belt's own vibration motors contain magnets** ([US10473690B2](https://patents.google.com/patent/US10473690B2/en) describes motor fields corrupting a magnetometer **[Cited]**). The motors' stray field and a safe spacing are **unknown**. Alternatives: BNO055 ([Adafruit](https://learn.adafruit.com/adafruit-bno055-absolute-orientation-sensor/overview); notes I2C quirks on ESP32), ICM-20948. MPU-6050 has no magnetometer. |

Indoors, magnetometer heading itself is unreliable: **[Cited]** Hu et al.,
*Sensors* 20(6):1578, 2020 ([PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC7146404))
report indoor fields "severely distorted," with an elevator and a concrete
column producing heading deviations.

### 4.2 Recommendation [Proposal]

- **The vision-target demo needs no heading sensor.** The target's direction
  is measured in the camera image, the same head-relative frame as obstacle
  alerts. Both cues therefore mean "relative to where your head points,"
  which keeps the convention consistent. The wearer is told to keep their
  head facing forward, or to turn head and body together. Turning the head to
  scan for a tag is fine: the tick moves toward the centre as they face it.
- **Route guidance (outdoors, later) needs torso heading.** The belt should
  carry the IMU: it is the only sensor fixed to the frame the motors are in.
  Use the magnetic-referenced rotation vector outdoors and expect drift or
  distortion indoors. Mount it as far from the motors as practical and
  measure the interference before trusting it. Fallbacks, in order: phone
  clipped to the torso with `CLHeading`, then GPS `course` while walking.
  This is a hardware purchase and a protocol change (§7), so it's a team
  decision.
- **Head vs torso.** A future option is to estimate head yaw relative to the
  torso by comparing the glasses Motion yaw with the belt IMU yaw, then rotate
  obstacle sectors into the torso frame. That changes the meaning of obstacle
  alerts and isn't proposed for the demo.

### 4.3 "Behind you" with front-only motors [Proposal]

- **Vision targets:** the camera only sees forward, so a target that isn't
  visible has no known direction. Give **no guidance** (not a guess). An
  optional later cue could say "last seen to the left" with a tick on the
  outer motor, marked as last-seen, but that is extra vocabulary. Leave it out
  of the first demo.
- **Route / compass targets:** let the target bearing be relative to the
  torso, from −180° to 180°.
  - Inside the front arc: G1 on the nearest motor. The physical arc of the 5
    motors depends on how the belt is worn and must be measured.
  - Outside the arc: G1 on the outer motor (0 or 4) on the shorter turn
    side, meaning "keep turning this way".
  - Roughly behind (e.g. beyond ±150°): G2 "turn around". Use hysteresis
    and a fixed tie-break side so the cue doesn't flip between left and right
    near 180°.
- **Precedent:** prior work signals turn-around with a dedicated temporal
  pattern (PocketNavigator three pulses **[Unverified]**; Cosgun's rotational
  patterns **[Cited]**). **No study was found of turn-around cues on a
  front-only belt.**

---

## 5. Question 3: routing source

Not needed for the indoor demo. It is compared here so the team can decide
whether outdoor routing is worth pursuing.

| Source | Key / account | Free tier & price **[Cited]** | Terms relevant to belt turn-by-turn **[Cited]** | Walking | Runs where |
|---|---|---|---|---|---|
| **Google Routes API** `computeRoutes` ([pricing](https://developers.google.com/maps/billing-and-pricing/pricing), [billing](https://developers.google.com/maps/documentation/routes/usage-and-billing)) | API key + billing account | Since March 2025, a per-SKU free monthly threshold instead of the $200 credit ([change](https://developers.google.com/maps/billing-and-pricing/march-2025)). Compute Routes Essentials: 10,000 free/month, then $5.00 per 1,000. Pro (e.g. traffic-aware): 5,000 free, $10 | May be used "without a corresponding Google Map", but **not with a non-Google map**. Only lat/lng may be cached, for up to 30 days ([service terms §19](https://cloud.google.com/maps-platform/terms/maps-service-terms)). General terms ban using Maps content **with text-to-speech** ([terms §3.2.3](https://cloud.google.com/maps-platform/terms)). | `WALK` is **beta**; apps **must** show a warning that sidewalks may be missing ([RouteTravelMode](https://developers.google.com/maps/documentation/routes/reference/rest/v2/RouteTravelMode)) | Phone or hub (REST) |
| Google Directions API (legacy) ([overview](https://developers.google.com/maps/documentation/directions/overview)) | same | Similar SKUs | Same as Routes. "As of October 2026" it can't be enabled for new projects | yes | — |
| Google Navigation SDK for iOS ([overview](https://developers.google.com/maps/documentation/navigation/ios-sdk/overview), [billing](https://developers.google.com/maps/documentation/navigation/ios-sdk/usage-and-billing)) | key + billing | 1,000 free/month, then $25 per 1,000 navigation requests | Same non-Google-map restriction. **[Unverified]** whether access needs a sales or contract step | walking supported | Phone only |
| **Apple MapKit `MKDirections`** ([docs](https://developer.apple.com/documentation/mapkit/mkdirections)) | No key; part of iOS | No published price. Throttled via `loadingThrottled` with no numeric limit stated. **[Unverified]** whether a paid Developer Program membership is needed | Request routes "only when you plan to present the corresponding route information to the user." The [Developer Program License Agreement](https://developer.apple.com/support/terms/apple-developer-program-license-agreement/) Attachment 6 §2.4 says you **may not surface Map Data without displaying the corresponding Apple map**. §3.3 (Location and Maps) says location apps may not be designed or marketed for "emergency or life-saving purposes", and that real-time turn-by-turn apps need a specific end-user risk notice. | `.walking` | Phone |
| Apple Maps Server API ([docs](https://developer.apple.com/documentation/applemapsserverapi)) | Developer account key → JWT | 25,000 calls/day per team, shared with MapKit JS | Same Apple Maps terms | `Walking` | Hub |
| **OSRM** public demo ([policy](https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server), [FOSSGIS terms](https://www.fossgis.de/arbeitsgruppen/osm-server/nutzungsbedingungen/)) | none | Free; at most 1 request/s, reasonable non-commercial use, a valid User-Agent, no uptime guarantee | OSM attribution ([copyright](https://www.openstreetmap.org/copyright)) | foot profile | Hub (public server or self-hosted) |
| **Valhalla** (FOSSGIS public instance; [API](https://valhalla.github.io/valhalla/api/route/api-reference/)) | none | at most 1 request/s; can be shut off at any time | OSM attribution; no hard-coded URLs | `pedestrian` costing | Hub (public or self-hosted) |
| **GraphHopper** ([pricing](https://www.graphhopper.com/pricing/)) | API key | Free plan: 500 credits/day, non-commercial, no guarantees. Open-source engine can be self-hosted | OSM attribution | `foot` | Hub |
| **openrouteservice** ([FAQ](https://giscience.github.io/openrouteservice/frequently-asked-questions)) | Personal key; **not allowed in client-side apps** | Directions 2,000/day, 40/min (from the FAQ; the plans page couldn't be loaded) | OSM attribution | `foot-walking`, `wheelchair` | Hub |

OSM attribution: the [OSMF guidelines](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines)
**[Cited]** say routing instructions need not carry attribution and that
attribution at app startup is acceptable. **[Unverified]** how this applies to
audio-only or haptic-only output.

**GPS accuracy [Cited]:** "GPS-enabled smartphones are typically accurate to
within a 4.9 m (16 ft.) radius under open sky … their accuracy worsens near
buildings, bridges, and trees" ([gps.gov](https://www.gps.gov/gps-accuracy)).
Several metres is about a sidewalk's width, so a GPS route can say "turn at
the next corner." It cannot steer someone along a sidewalk or through a door.
Obstacle perception stays the only local cue.

### Assessment [Proposal]

- **Apple MapKit** is native and has no key. But the terms in Attachment 6
  §2.4 (route data shown on an Apple map) and §3.3 (no life-saving
  positioning; a risk notice for turn-by-turn) need a careful read. The phone
  app could show the route on an Apple map for the supervisor while the belt
  plays cues. Whether that satisfies the terms is **unverified and needs a
  human reading of the terms**. For a team-only prototype that is never
  distributed the practical risk is lower, but this doc doesn't decide it.
- **Google** needs a billing account and has a beta walking mode that
  requires a sidewalk warning. Its text-to-speech ban rules out spoken
  directions if Google is used.
- **OSM routing on the hub** has no key (OSRM, Valhalla) and is
  self-hostable. It is the least encumbered for a belt-only experience.
  Respect the 1 request/s public-server limits and add attribution. The route
  only needs to be fetched once per trip; per-step guidance is computed
  locally from GPS position and heading.
- **Where it runs:** position (GPS) and heading come from the phone or belt,
  never the laptop, so the phone must send them to the hub either way. The
  simplest split: the phone sends `{lat, lon, accuracy, course, heading,
  heading_accuracy}` at ~1 Hz. The hub holds the route, computes the next
  waypoint's bearing, converts it to the torso frame and drives the belt.
  That is a new phone→hub message (§7).

---

## 6. Question 4: indoor demo options

Ranked for a supervised demo in one room. Effort and wow are my judgment;
facts are cited.

| # | Option | Effort | Wow | Works in a supervised room? | Key facts |
|---|---|---|---|---|---|
| **1** | **AprilTag waypoints ("follow the tags")** | Low | Medium–high | **Most repeatable.** Tags give an unambiguous ID, so it can be "go to tag 1, then 2, then 3". Steering only needs the tag centre's sector; no calibration. | **[Cited]** OpenCV `ArucoDetector` includes `DICT_APRILTAG_36h11` ([header](https://github.com/opencv/opencv/blob/4.x/modules/objdetect/include/opencv2/objdetect/aruco_dictionary.hpp)). Pose needs intrinsics and tag size ([tutorial](https://github.com/opencv/opencv/blob/4.x/doc/tutorials/objdetect/aruco_detection/aruco_detection.markdown)). No sourced detection-range rule of thumb found. **[Checked]** below. |
| 2 | **COCO object as target** ("go to the chair") with the existing YOLO11n | Low | High | Works if only one of that object is in view. Several chairs make it ambiguous. The target is itself an obstacle, so guidance hands over to obstacle alerts as you approach (correct, but confusing to explain). | **[Cited]** COCO-80 includes chair, couch, bench, dining table, tv, potted plant, bottle; it has **no "door" or "exit sign"** ([Ultralytics COCO](https://docs.ultralytics.com/datasets/detect/coco/)). **[Repo]** Boxes don't leave perception today (§2). Needs a real `detections` field, which is Nathan's area. |
| 3 | **Open-vocabulary "door" / "exit sign"** (YOLOE, YOLO-World, or Open Images V7 YOLOv8 with a "Door" class) | Medium | Highest | Unknown until measured. Expect false positives (walls, cabinets) and extra laptop load next to the depth model. | **[Cited]** YOLO-World uses `set_classes()` ([docs](https://docs.ultralytics.com/models/yolo-world/)). YOLOE is segmentation-only, and its first `set_classes()` downloads a CLIP or MobileCLIP encoder, so it needs network access the first time ([docs](https://docs.ultralytics.com/models/yoloe/)). The OIV7 models are YOLOv8 only and include "Door" ([docs](https://docs.ultralytics.com/datasets/detect/open-images-v7/)). No published speed comparison with YOLO11n was found. |
| 4 | **Simulated GPS route replay** (route + fake position replayed, heading from a belt IMU or also simulated) | Low–medium | Low in a room | Shows the outdoor story and the turn and turn-around patterns, but the wearer isn't really going anywhere. Must be labelled as simulation. | **[Cited]** Xcode can simulate location from GPX `wpt` waypoints only, not `rte`/`trk` ([Xcode help](https://help.apple.com/xcode/mac/current/en.lproj/dev8dbd2f820.html)). **[Repo]** There is no web dashboard today; "on the dashboard" would mean new UI. |
| 5 | **BLE beacons** (iBeacon ranging on the phone) | Medium–high | Low in one room | Gives room or zone proximity, not direction inside one room. | **[Cited]** `CLProximity` is only unknown, immediate, near or far ([docs](https://developer.apple.com/documentation/corelocation/clproximity)). `CLBeacon.accuracy` "may fluctuate" and shouldn't identify a precise location ([docs](https://developer.apple.com/documentation/corelocation/clbeacon/accuracy)). Faragher & Harle, IEEE JSAC 2015: <2.6 m error 95% of the time with a dense beacon network ([pub](https://www.cl.cam.ac.uk/~rkh23/site/publication/pub42)). Precedent: NavCog, Ahmetovic et al., MobileHCI 2016, doi:10.1145/2935334.2935361 ([CMU](https://ri.cmu.edu/publications/navcog-a-navigational-cognitive-assistant-for-the-blind)). |
| 6 | **ARKit world-tracking route** | High | Medium | Needs the phone held or chest-mounted with its camera clear. The glasses camera can't feed ARKit. | **[Cited]** `ARWorldTrackingConfiguration`, `ARWorldMap` relocalisation ([docs](https://developer.apple.com/documentation/arkit/arworldmap)). Apple Indoor Maps is for large venues ([program](https://register.apple.com/resources/indoor/program/)). |

**[Checked] AprilTag API check.** A throwaway script in the session
scratchpad (not in the repo) used the hub's locked `opencv-python==5.0.0.93`.
`cv2.aruco.ArucoDetector` with `DICT_APRILTAG_36h11` detected a
synthetically placed tag (id 7, 120 px) on a 504×896 frame after a light blur
and JPEG quality 60, which matches the glasses stream settings. Its centre
mapped to motor 3 using the same 5-strip split as the obstacle sectors.
**What this shows:** the API exists in the current dependency, with no new
package. **What it doesn't show:** range, robustness to motion blur, or
performance on real glasses frames.

**What would actually work with a supervisor:** option 1, in a room cleared
so the walking lane is wider than the obstacle range, with a sighted
supervisor walking beside the wearer and a stop word. Option 2 is a good
second act if a single chair is the only COCO object in view. Option 3 is the
headline idea ("find the exit") but carries the most measurement risk;
present it as a stretch goal.

---

## 7. Question 5: architecture and contract impact

### 7.1 Where guidance logic lives [Proposal]

In the **hub**. It already has the frames, the health monitor, the 15 Hz belt
loop and the single place where the final command is chosen.

```text
frames ──▶ perception (unchanged) ──▶ HapticPolicy ──┐
   │                                                  ├─▶ GuidanceComposer ─▶ belt
   └──▶ guidance target finder (AprilTag, own thread) ┘     (rules in §3.3)
```

- `hub/haptic_hub/guidance/` (new package):
  - a target finder that runs on the **full-resolution** frame in its own
    worker. It must not slow the obstacle loop: if it's late, guidance goes
    quiet; obstacles don't wait.
  - a pure, unit-testable `GuidanceComposer` that takes the obstacle command
    and the guidance state and returns the final command.
- One small hook in `app.py::_belt_loop` after `self.policy.command(...)`.
  Nothing in `hub/haptic_hub/perception/` changes (Nathan is working there).
  If the COCO-object target is wanted later, perception would need to expose
  detections as a real field, which needs coordinating with Nathan.
- Guidance **off by default**. It is enabled with an explicit CLI flag, e.g.
  `--guide tags:1,2,3` (name to decide).

### 7.2 Is a protocol change needed?

**For the minimal demo: no wire-format change.** The hub can make G1–G3 by
changing `levels` between consecutive 15 Hz commands. Ticks are then
quantised to ~67 ms and subject to unmeasured BLE jitter. A dropped "off"
write stretches a tick by one frame; the 250 ms TTL bounds the worst case,
and the existing failsafe takes over after that.

**But the contract still changes in meaning, so flag it and coordinate.**
Affected: whoever owns the belt (`esp32/`), the phone app (Ethan) and
perception (Nathan).

| Change | Type | Who's affected | Needed for the minimal demo? |
|---|---|---|---|
| `protocol.md` "Meaning of the haptics": add the guidance vocabulary (G1–G3), the priority rules, and "no tick = no guidance" | **Meaning** | Everyone: anyone testing the belt has to know what a tick means | **Yes** |
| Status JSON (§2): add an optional `"guidance"` object, e.g. `{"state": "off"\|"tracking"\|"paused"\|"lost"\|"arrived", "target": "tag 2", "motor": 3}`. During a tick `mode` stays `"levels"` and `levels` shows the tick, so without this field the phone can't tell guidance from an obstacle. | Additive JSON | `meta/` (Ethan), optional UI only. The current decoder appears to ignore unknown keys (read, not tested) | Recommended |
| **Proposal:** phone → hub `location` text message with position and heading (§5, sketch in §9.3) | New message | `meta/`, `hub/` | No (only for the outdoor / campus demo; on hold) |
| Belt → hub IMU heading: new BLE **notify** characteristic and an IMU on the belt | New characteristic + hardware | `esp32/`, `hub/`, wiring docs | No (outdoor only) |
| **Protocol v2, firmware-rendered patterns** (Design B below) | Version bump, all golden vectors | All three components | No; only if hub-made ticks feel mushy on real hardware |

### 7.3 Candidate designs

**Design A: hub-composed ticks over today's protocol [recommended to start].**

- *How:* the composer from §7.1, rules from §3.3, patterns from §3.2, sent as
  ordinary `levels` commands.
- *Pros:* no firmware or byte changes. One component plus a doc change.
  Priority logic sits next to the health monitor, where fault decisions
  already happen. Easy to change between trials.
- *Cons:* tick timing is only as good as the 15 Hz loop and BLE delivery,
  which haven't been measured. The belt can't tell guidance from obstacles,
  so the safety ordering lives only in the hub: a hub bug could, in
  principle, send a tick during an obstacle frame. Mitigate with unit tests
  on the composer. Status `mode` is ambiguous unless the `guidance` field is
  added.

**Design B: firmware-rendered guidance (protocol v2).**

- *How:* extend the command with, for example, `guide_motor: u8`
  (`0xFF` = none) and `guide_pattern: u8` (enum of G1–G3), or add a new mode.
  The firmware plays the rhythm on its own clock and **enforces on the
  device**: any non-zero obstacle level mutes guidance in that same update,
  and fault or link-lost overrides it.
- *Pros:* crisp, jitter-free rhythm. Safety ordering enforced at the last hop
  as well as in the hub. Status can report guidance separately.
- *Cons:* version bump, changes in all three components, new golden vectors.
  Touches firmware that is itself not yet verified on a real board. Most
  coordination cost. Only worth it if Design A's ticks prove hard to feel or
  mistime on hardware.

**Design C: separate channel for guidance.**

- *C1, audio:* the phone speaks short phrases ("tag two, slightly right"),
  presumably through the glasses' speakers. The belt stays obstacle-only,
  which removes the ambiguity completely.
  - **[Unverified]** that audio plays through the glasses while the DAT
    camera stream runs.
  - Speech competes with hearing, which blind travellers rely on.
  - Google's terms ban using its Maps content with text-to-speech (§5).
- *C2, dedicated actuator:* a 6th motor on the back, or a wrist vibration.
  "Behind you" becomes natural.
  - **[Repo]** The BLE command already allows `count` 1–8 and Info reports
    the motor count. But the hub assumes one motor per perception sector
    (`haptics.py` raises otherwise), and `protocol.md` defines index order as
    left to right. A back motor is therefore a wiring, config and meaning
    change in `esp32/`, `hub/` and the docs.

| | A: hub ticks | B: firmware patterns | C1: audio | C2: extra motor |
|---|---|---|---|---|
| Separation of meanings | Rhythm + time exclusion | Rhythm + time exclusion, device-enforced | Different modality | Different body location + rhythm |
| Components touched | hub, docs | esp32, hub, meta, docs | meta (+ hub) | esp32, hub, docs, hardware |
| Wire change | none | v2 | new WS message | count/meaning |
| Timing quality | ~67 ms steps, jitter unknown | firmware clock | speech latency unknown | as A or B |
| Hackathon cost | Low | High | Medium (unverified audio path) | Medium + hardware |

---

## 8. Recommended minimal demo: "follow the tags" [Proposal]

Smallest end-to-end experiment that tests the core question: *can a wearer
tell a guidance tick from an obstacle alert, and follow ticks to a target,
while obstacle alerts keep priority?*

**Setup.**

- Print 2–3 AprilTag 36h11 tags as large as practical, on matte paper. Mount
  them at chest to head height, at points across a room cleared beyond the
  obstacle range along the walking lane.
- One sighted supervisor walks beside the wearer. Agree a stop word. Run in
  a controlled space only.

**Hub behaviour.**

- `--guide tags:1,2,3`.
- The finder looks for the current tag ID in each frame. While it is seen
  (and fresh), the hub sends G1 on the tag's sector motor, subject to §3.3.
- When the tag's apparent size passes a threshold calibrated on site, the
  hub plays G3 and advances to the next ID.
- While the tag isn't seen: no guidance, and the status says `lost`.

**Steps, in order (each must pass before the next):**

1. **Unit tests for the composer** (no hardware):
   - fault overrides guidance;
   - any obstacle level cuts a tick in the same frame;
   - the hold-off after obstacles;
   - stale target → no guidance;
   - G1–G3 timelines at 15 Hz;
   - a guidance tick never uses more than one motor (except G3), so it can't
     match the fault pattern.
2. **Replay:** record a short clip of the tags (team members only, no
   bystanders; keep it out of git) and run with `--source video:... --belt
   sim`. Check the printed belt output.
3. **Bench, belt on a table or worn while seated:**
   - feel G1–G3 at every motor;
   - blind-test whether a teammate can tell "tick on motor k" from "obstacle
     on motor k" and from the fault pattern;
   - record errors.

   This answers whether Design A is good enough or Design B is needed.
4. **Supervised walk:** one tag, then the sequence. Log every time
   guidance was paused for an obstacle and every lost-target period.

**Changes needed:**

| Component | Change |
|---|---|
| `hub/` | new `guidance/` package and tests; one hook in `app.py`; CLI flag; optional `guidance` status field. No new dependency (`cv2.aruco` is in the locked OpenCV). |
| `docs/protocol.md` | Meaning section: guidance vocabulary + priority rules (+ the status field if adopted). **Needs team sign-off.** |
| `esp32/` | none |
| `meta/` | none required; optional "Guidance: tracking tag 2" line in the UI (Ethan) |

What to report afterwards, kept separate as AGENTS.md requires: simulated
results, replayed-video results, bench results, and supervised-walk results.
Don't report accuracy or latency numbers that weren't measured.

---

## 9. Campus video demo: ECSW → Comets LANding [Proposal]

> **Partly built (2026-11-08).** The phone-only slice in §11 replaces rows 1,
> 2 and 6 of §9.3 for now, with the route follower on the phone instead of the
> hub. Belt guidance (rows 3, 5 and 7) is still a proposal.

The idea: guide a supervised wearer from the Engineering and Computer Science
West building (ECSW) to the UTD Esports Center for the video demo. GPS gets
them to the building, the camera finds the door, and obstacle alerts keep
priority the whole way.

### 9.1 Findings

- **Destination [Cited].** The Esports Center is **Comets LANding**, in the
  Student Union. Sources: [UTD Gaming & Esports](https://union.utdallas.edu/?p=2370),
  [UTD News, 2025](https://news.utdallas.edu/campus-community/comets-landing-esports-opens-2025).
- **UTD's map.**
  - **[Checked]** map.utdallas.edu embeds a Concept3D map
    (`map.concept3d.com/?id=1772`). The page and its script were fetched on
    2026-11-07. The script refers to a Concept3D wayfinding API
    (`api.concept3d.com/wayfinding/v2?map=…`) and has an "accessible routes"
    option. The API was **not called**.
  - **[Cited]** Concept3D's API is private. Access, documentation, the Map ID
    and an API key come from Concept3D support or the customer's Partner
    Success Manager. The FAQ suggests it may cost extra.
    Sources: [API FAQs](https://help.concept3d.com/hc/en-us/articles/360016590393-API-FAQs),
    [Developer Resources](https://help.concept3d.com/hc/en-us/sections/29586421148819-Developer-Resources).
  - **Permission.** UTD gave the team explicit permission at the event to use
    its map (reported by the owner, not documented here). The API key would
    belong to UTD's Concept3D account, so get it, or a route export, from
    UTD's map admin. **Do not pull the key out of the public web client.**
- **Cross-check route [Checked].** This was computed offline from
  OpenStreetMap data (© OpenStreetMap contributors, ODbL) with a throwaway
  script outside the repo. **Nobody has walked it.**
  - **Endpoints:** from the ECSW main entrance (OSM node 11907831269) to the
    Comets LANding main entrance (node 4645495922).
  - **Length:** 320 m along footways and pedestrian ways, with no steps
    (222 m in a straight line). Per the OSM tags there are no road crossings.
    About 217 m is on pedestrianised Rutford Avenue, and one footway segment
    is tagged covered.
  - **Shape:** roughly north about 90 m, a right turn east onto Rutford Ave
    for about 150 m, then short jogs over the last ~45 m to the door.
  - **Door level:** OSM tags the LANding main entrance `level=1` /
    `level:ref=2`, but the ECSW door `level=0`. Check on foot whether there's a
    ramp or stairs.
- **GPS limit [Cited].** Phone GPS is "typically accurate to within a 4.9 m
  (16 ft.) radius under open sky" and worse near buildings
  ([gps.gov](https://www.gps.gov/gps-accuracy)). This route runs between
  buildings, so GPS can manage "go north, turn right, arrive" but can't find
  a door. Turns shorter than about 15 m get merged into one leg.

### 9.2 Route data [Proposal]

- **Fastest, no API:** in UTD's map, get directions from ECSW to the Student
  Union / Comets LANding with "accessible routes" on. Copy 6–8 waypoints into
  a route file and compare it against the OSM cross-check.
- **If UTD provides a Concept3D key or export:** the hub loads the route from
  it. The rest of the system doesn't change.
- **Route file contents:** waypoints (lat, lon, arrival radius), the AprilTag
  id at the door, and the data source and its attribution. Campus coordinates
  are not personal data. Walk logs are, so they stay out of git.

### 9.3 What to build (on hold)

| # | Where | What | Size |
|---|---|---|---|
| 1 | `hub/` | **Route file**, e.g. `routes/ecsw_to_landing.json` (§9.2) | small |
| 2 | `hub/` | **Route follower**, pure logic. Input: a GPS fix + heading. Output: the next waypoint, its bearing relative to the torso, arrived, off route, or turn around. **No guidance** when the fix is stale or inaccurate or the heading is invalid. Uses hysteresis around 180°. | medium |
| 3 | `hub/` | **Guidance composer**: §3.3 rules and §3.2 patterns. Fault beats obstacle beats guidance; a tick is cut in the same frame an obstacle appears; hold-off after obstacles. | medium |
| 4 | `hub/` | **Door handoff**: AprilTag finder for the last ~45 m (§6; the API exists in the locked OpenCV) | small |
| 5 | `hub/` | Accept the phone's `location` message, add a `--guide route:<file>` flag and the `guidance` status field, and **record/replay GPS logs** for desk testing | small–medium |
| 6 | `meta/` (Ethan) | CoreLocation (when-in-use permission, usage string in Info.plist), sending location + heading at ~1 Hz over the existing WebSocket. The UI shows GPS accuracy and guidance state. Optional: log the walk for replay. | medium |
| 7 | `docs/protocol.md` | Add the `location` message, the status `guidance` field and the guidance meaning, **after team agreement**. Affects everyone. | small |
| 8 | `esp32/` | Nothing | none |

**Proposed `location` message (phone → hub, WebSocket text). Not in
`protocol.md`. Do not implement against it yet.**

```json
{"type":"location","lat":32.98642,"lon":-96.75115,"h_acc_m":6.0,
 "course_deg":5.0,"course_acc_deg":20.0,"heading_deg":3.0,"heading_acc_deg":15.0,"speed_mps":1.2}
```

The example values are made up to show the shape. As with frames, the hub
would measure staleness by its own receive time. Negative accuracy values
mean invalid, following CoreLocation's convention (§4.1).

**Heading for the demo:** clip the phone to the chest in portrait so
`CLHeading` follows the torso. A phone in a pocket gives a meaningless
heading (§4.1). A belt IMU is the better long-term answer (§4.2).

### 9.4 What agents can test (no walking, no hardware)

- **Route follower unit tests:**
  - bearing and distance;
  - advancing to the next waypoint;
  - off-route detection;
  - turn-around hysteresis near 180°;
  - a stale fix or invalid heading gives no guidance.
- **Composer unit tests:**
  - fault beats everything and obstacles beat guidance;
  - no tick in any frame with an obstacle level;
  - G1 uses at most one motor;
  - no guidance pattern matches the fault pattern.
- **Simulated walks:** a synthetic walk along the route with GPS noise and
  jumps, run through the simulated belt. Check that it reaches the door, how
  often guidance flips, and that it never ticks during an obstacle. Results
  are labelled as simulation.
- **Replay:** run the team's recorded GPS logs and door video clips through
  the hub.
- **Golden-vector tests:** for the `location` message in `hub/` and
  `meta/`, plus iOS simulator builds and tests. The simulator can replay a
  GPX file to fake a location ([Xcode help](https://help.apple.com/xcode/mac/current/en.lproj/dev8dbd2f820.html)).
- **Route cross-check:** compare UTD-derived waypoints against the OSM route.

### 9.5 What people have to test (body, place, hardware)

0. **Prerequisite:** the belt working on the bench with obstacle alerts. Not
   yet verified on a real board ([project-context.md](project-context.md)).
1. **Seated feel test:** G1/G2/G3 vs an obstacle buzz vs the fault pattern, on
   every motor, with a teammate guessing blind. Record mistakes. This decides
   Design A vs B (§7.3).
2. **Phone GPS and heading on the route:**
   - check accuracy at the ECSW door, on Rutford Ave, under the covered
     section and at LANding;
   - compare heading with the phone clipped to the chest vs in a pocket;
   - record 2–3 GPS logs of normal walks (no belt) for agent replay.
3. **Walk the route once:**
   - confirm it matches UTD's accessible route;
   - check the LANding door level;
   - note carts, scooters and crowd times;
   - pick where to tape the door tag, with permission.
4. **The whole rig outdoors:**
   - glasses → phone → laptop in a backpack → belt;
   - the network: probably the phone's hotspot, since campus Wi-Fi may block
     device-to-device traffic (not verified);
   - BLE range and battery;
   - `--outdoor` depth model.
5. **Door handoff:** how far away the tag is detected in daylight.
6. **Supervised dry run, then film:**
   - a sighted guide walks alongside, the wearer's eyes stay open, and there's
     a stop word;
   - confirm obstacle alerts still interrupt guidance, using a stationary box
     or a person standing still, never someone stepping in;
   - caption the video as an experimental, supervised prototype;
   - keep raw video and GPS logs out of git and get consent from anyone
     identifiable.

**If time runs short:**
- treat "arrived" as within the GPS radius of the door, with the supervisor
  confirming (no AprilTag handoff); or
- film a replay of a recorded walk, clearly labelled as a replay.

---

## 10. Open questions for the team

1. **Is guidance in scope for the demo at all**, or a stretch goal after the
   obstacle loop is verified on real hardware? Obstacle alerts on a real belt
   aren't verified yet ([project-context.md](project-context.md)).
2. **Vocabulary sign-off:** obstacle = continuous, guidance = single tick,
   fault = all-motor double pulse. Is G1–G3 the right set? Does "no guidance"
   need its own cue, or is supervisor announcement enough for a demo?
3. **Priority:** should guidance pause on *any* obstacle output (proposed, but
   it may starve guidance in a busy room), or only above a stronger level?
   The second option needs a safety argument, because a weak obstacle buzz
   and a tick could then come close together.
4. **Demo target:** AprilTags (reliable) vs a COCO chair (quick wow) vs
   open-vocabulary "door/exit" (biggest wow, most risk)? If a detector target
   is wanted, who exposes detections from perception (Nathan)?
5. **Arrival near walls:** with `far_m = 2.5 m`, walking toward a wall tag
   triggers obstacle alerts before arrival. Put tags on free-standing stands,
   set the arrival size to trigger earlier, or accept the handover?
6. **Design A vs B:** agree to start with hub ticks and move to firmware
   patterns only if the bench test shows ticks are hard to feel?
7. **Status field:** add the optional `guidance` object to status JSON now,
   so the phone UI can tell a tick from an obstacle?
8. **Outdoor routing later:** buy a belt IMU (BNO085-class) and accept a BLE
   notify characteristic? Which routing source: OSM on the hub vs MapKit on
   the phone (terms need a human read) vs Google (billing, beta walking,
   text-to-speech ban)?
9. **Glasses Motion module:** worth testing whether it works in Developer
   Mode without the Developer Center, given the earlier decision to skip it?
10. **Head vs torso:** keep everything head-relative (consistent, simple) or
    eventually rotate cues into the torso frame?
11. **User input:** before any walking demo, who gives feedback on whether the
    patterns are learnable, ideally a blind or low-vision tester with consent
    and supervision? The literature is mostly blindfolded sighted subjects.
12. **Campus route data:** will UTD's map admin provide a Concept3D key or a
    route export, or do we hand-copy waypoints from UTD's map?
13. **Campus demo format:** a real supervised walk on camera, or a replay of a
    recorded walk (safer, less impressive)?
14. **Phone work:** can Ethan add the `location` message once the hold lifts
    and the protocol proposal is agreed?

---

## 11. Built: phone-only campus route (2026-11-08) [Repo]

Where: `meta/HapticBelt/Navigation/`, reached from **Campus route
(experimental)** at the bottom of the app's main screen. Usage is in
[meta/README.md](../meta/README.md#campus-route-experimental).

- **Route data:** `ecsw-to-comets-landing.geojson`, the OSM route from §9.2
  (about 320 m, footways only, ODbL attribution shown in the app). It has
  **not been walked**. UTD has given permission to use its map, but no export
  is in hand yet. When one arrives, replace the GeoJSON LineString; the code
  doesn't change.
- **Map:** SwiftUI MapKit (Apple map shown, which keeps us inside Apple's
  terms, §5). No API key, no paid service.
- **Follower (pure, unit-tested):** projects each fix onto the route and aims
  10 m ahead (closer when more than 10 m off the path). It gives no direction
  when the fix is stale (> 5 s) or worse than ±25 m, or when the facing
  direction is unknown. The facing direction is the compass (accuracy ≤ 35°),
  else the GPS course (walking ≥ 0.5 m/s), and the user can pick either. It
  buckets the turn into straight / bear / turn / turn around, with 8°
  hysteresis. Arrival is within 8 m, and it latches.
- **Output: speech only.** Every phrase starts with "Route:". There is no
  phone vibration, because on this app vibration means obstacles (AGENTS.md:
  keep obstacle direction separate from travel direction). The belt is not
  used for guidance yet.
- **Priority (§3.3, applied to speech):** while the hub status is fresh and
  shows any obstacle level > 0, a fault, or unhealthy, route speech is cut
  mid-word and stays quiet. Guidance resumes 1.5 s after the alert clears,
  once the instruction has been steady for 1 s. With no hub status, the
  screen says in red that obstacle alerts are **not** running.
- **Privacy:** location stays in memory. It isn't logged, stored or sent to
  the hub.

**Tested [simulation only]:** 18 hostless unit tests in
`meta/HapticBeltTests/RouteGuidanceTests.swift`. They cover geometry,
projection, buckets and hysteresis, stale and uncertain fixes, heading
sources, the announcer's priority and hold-off, and a full simulated walk of
the real route in both directions (start facing away, wander off the path,
recover, arrive). The app was also run in the iOS Simulator with **Simulate
the walk**. Not tested: real GPS or compass on campus, speech through the
glasses, or guidance while the hub raises real alerts.

**Next human test:** see the checklist in the meta README. Walk it with a
sighted guide, phone held flat, hub connected, first without the
blindfolded wearer.


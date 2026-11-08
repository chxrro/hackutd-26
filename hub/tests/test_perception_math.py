import numpy as np
import pytest

from haptic_hub.perception.geometry import (
    back_project,
    box_sectors,
    depth_sector_nearest,
    fit_floor,
    linear_risk,
    obstacle_sector_nearest,
    prior_floor,
    sector_edges,
)
from haptic_hub.perception.ttc import TtcEstimator


def test_sector_edges_cover_width():
    edges = sector_edges(100, 5)
    assert edges[0][0] == 0 and edges[-1][1] == 100
    assert all(a[1] == b[0] for a, b in zip(edges, edges[1:]))


def test_depth_nearest_finds_obstacle_sector_and_ignores_floor():
    depth = np.full((100, 100), 5.0)
    depth[40:60, 40:60] = 0.8  # obstacle, middle sector
    depth[90:, :] = 0.3  # floor at the bottom of the frame: outside the band
    nearest = depth_sector_nearest(depth, 5, band_top=0.2, band_bottom=0.75, percentile=5)
    assert nearest[2] == pytest.approx(0.8)
    assert nearest[0] == pytest.approx(5.0) and nearest[4] == pytest.approx(5.0)


def test_depth_nearest_ignores_invalid_values():
    depth = np.full((10, 10), np.nan)
    depth[:, 8:] = 2.0
    nearest = depth_sector_nearest(depth, 5, 0.0, 1.0, 5)
    assert nearest[0] is None and nearest[4] == pytest.approx(2.0)


def test_linear_risk():
    assert linear_risk(0.5, 0.6, 2.5) == 1.0
    assert linear_risk(3.0, 0.6, 2.5) == 0.0
    assert linear_risk(None, 0.6, 2.5) == 0.0
    assert linear_risk(1.55, 0.6, 2.5) == pytest.approx(0.5)


def test_box_sectors():
    assert box_sectors(0, 10, 100, 5) == [0]
    assert box_sectors(15, 45, 100, 5) == [0, 1, 2]
    assert box_sectors(50, 50, 100, 5) == []


def test_ttc_for_approaching_object():
    est = TtcEstimator(window_s=1.0, min_samples=3)
    # Object at 4 m approaching at 1 m/s: h = k / distance. TTC at t should be (4 - t).
    ttc = None
    for i in range(6):
        t = i * 0.1
        ttc = est.update(1, t, 400 / (4.0 - t))
    assert ttc == pytest.approx(3.5, rel=0.15)


def test_ttc_none_for_static_or_receding_object():
    est = TtcEstimator()
    for i in range(6):
        assert est.update(1, i * 0.1, 100.0) is None
        est.update(2, i * 0.1, 100.0 - i)
    assert est.update(2, 0.6, 94.0) is None


def test_ttc_prunes_old_tracks():
    est = TtcEstimator(forget_after_s=1.0)
    est.update(1, 0.0, 10)
    est.prune(5.0)
    assert len(est) == 0


def test_ttc_inverse_height_fit_has_no_lag():
    """h = k / Z for a constant-speed approach: the 1/h fit recovers the true TTC
    (within the 1 px noise-floor margin), where the old h fit read ~0.6 s late."""
    est = TtcEstimator(window_s=1.0, min_samples=5)
    v, z0, f = 1.2, 8.0, 332.6 * 1.75
    errors = []
    for i in range(70):
        t = i / 15
        z = z0 - v * t
        ttc = est.update(1, t, f / z)
        if ttc is not None and 1.0 <= z / v <= 3.0:
            errors.append(ttc - z / v)
    assert errors and max(abs(e) for e in errors) < 0.1


def test_ttc_needs_min_samples_and_span():
    est = TtcEstimator(min_samples=5, min_span_s=0.25)
    out = [est.update(1, i / 15, 100 + 5 * i) for i in range(5)]
    assert out[:4] == [None] * 4 and out[4] is not None  # 5th sample spans 0.27 s
    est2 = TtcEstimator(min_samples=5, min_span_s=0.5)
    assert all(est2.update(1, i / 15, 100 + 5 * i) is None for i in range(6))  # 6 samples span only 0.33 s


def test_ttc_noisy_boxes_read_farther_than_clean():
    """Jitter widens the slope's standard error, so the conservative TTC is longer
    (a far person doesn't trip an alert on noise)."""
    rng = np.random.default_rng(0)
    f = 332.6 * 1.75
    clean, noisy = TtcEstimator(), TtcEstimator()
    for i in range(15):
        t, z = i / 15, 6.0 - 1.5 * i / 15
        a = clean.update(1, t, f / z)
        b = noisy.update(1, t, f / z + rng.normal(0, 4.0))
    assert a is not None and abs(a - (6.0 - 1.5 * 14 / 15) / 1.5) < 0.15
    assert b is None or b > a


def test_ttc_alert_ends_soon_after_the_object_stops():
    est = TtcEstimator()
    f = 332.6 * 1.75
    last = None
    for i in range(120):
        t = i / 15
        z = max(9.0 - 1.5 * t, 1.9)  # stops at t = 4.73 s
        ttc = est.update(1, t, f / z)
        if ttc is not None and ttc < 3.0:
            last = t
    assert last is not None and last - (9.0 - 1.9) / 1.5 < 0.35


# -- floor-relative depth ---------------------------------------------------


def _scene_depth(w=96, h=171, hfov=60.0, pitch_deg=0.0, eye=1.6, ceiling=None, box=None):
    """z-depth of a flat floor (and optional ceiling / box face) for a pinhole camera."""
    import math

    f = (w / 2) / math.tan(math.radians(hfov) / 2)
    u = (np.arange(w) + 0.5 - w / 2) / f
    v = (np.arange(h) + 0.5 - h / 2) / f
    xc, yc = np.meshgrid(u, v)
    p = math.radians(pitch_deg)
    # world ray (x right, y forward, z up) for camera ray (xc, yc, 1)
    dy = math.cos(p) - yc * math.sin(p)
    dz = -math.sin(p) - yc * math.cos(p)
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(dz < 0, -eye / dz, np.inf)
        if ceiling is not None:
            s = np.minimum(s, np.where(dz > 0, (ceiling - eye) / dz, np.inf))
        if box is not None:  # (distance, height, half width): a vertical face
            dist, top, half = box
            sb = dist / dy
            z = eye + sb * dz
            hit = (dy > 0) & (z >= 0) & (z <= top) & (np.abs(xc * sb) <= half)
            s = np.where(hit & (sb < s), sb, s)
    return np.where(np.isfinite(s), s, 80.0).astype(np.float32), f


def _floor(depth, f, stride=1):
    pts, valid = back_project(depth, f, stride)
    flat = pts[valid]
    flat = flat[flat[:, 2] < 20]
    return fit_floor(flat, np.random.default_rng(0), [prior_floor(1.6)], max_tilt_deg=50, height_range=(0.9, 2.1),
                     tol_m=0.05, tol_rel=0.03, min_inlier_frac=0.08)  # fmt: skip


@pytest.mark.parametrize("pitch", [0.0, 20.0, 35.0])
def test_floor_fit_recovers_pitch_and_height(pitch):
    depth, f = _scene_depth(pitch_deg=pitch)
    plane = _floor(depth, f)
    assert plane is not None and plane.source == "fit"
    assert plane.pitch_deg == pytest.approx(pitch, abs=1.0)
    assert plane.height_m == pytest.approx(1.6, abs=0.05)


def test_floor_fit_rejects_a_wall_filling_the_view():
    depth = np.full((171, 96), 0.9, np.float32)  # a wall 0.9 m ahead, no floor visible
    plane = _floor(depth, (96 / 2) / np.tan(np.radians(30)))
    assert plane is None or plane.height_m == pytest.approx(1.6)  # at most the prior hypothesis, never the wall


def test_obstacles_ignore_floor_and_ceiling_whatever_the_pitch():
    for pitch in (0.0, 20.0):
        depth, f = _scene_depth(pitch_deg=pitch, ceiling=2.6)
        plane = _floor(depth, f)
        pts, valid = back_project(depth, f)
        assert obstacle_sector_nearest(pts, valid, plane, 5, 0.15, 0.3, 2.5) == [None] * 5


def test_low_obstacle_is_seen_and_band_mode_misses_it():
    depth, f = _scene_depth(box=(1.6, 0.45, 0.6))
    plane = _floor(depth, f)
    pts, valid = back_project(depth, f)
    near = obstacle_sector_nearest(pts, valid, plane, 5, 0.15, 0.3, 2.5)
    assert near[2] == pytest.approx(1.6, abs=0.05)
    band = depth_sector_nearest(depth, 5, 0.2, 0.75, 5)  # the old fixed rows sit above the box
    assert band[2] > 2.5


def test_floor_mode_falls_back_to_band_when_depth_is_not_one_plane():
    """A distorted floor (as a monocular model can give) leaves many points below any
    plausible plane: the frame is not trusted and uses the old band geometry."""
    import cv2

    from haptic_hub.config import PerceptionConfig
    from haptic_hub.perception.vision import FloorDepth

    cfg = PerceptionConfig()
    depth, _ = _scene_depth(w=384, h=683, pitch_deg=20.0)
    near, info = FloorDepth(cfg).nearest(depth, 0.0)
    assert info["source"] == "fit" and near == [None] * 5

    warp = np.linspace(0.7, 1.5, depth.shape[0], dtype=np.float32)[:, None]  # depth bent row by row
    near, info = FloorDepth(cfg).nearest(cv2.GaussianBlur(depth * warp, (5, 5), 0), 0.0)
    assert info["source"].endswith("-> band") and info["below_frac"] > cfg.floor_max_below_frac

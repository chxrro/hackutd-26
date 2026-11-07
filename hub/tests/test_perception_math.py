import numpy as np
import pytest

from haptic_hub.perception.geometry import box_sectors, depth_sector_nearest, linear_risk, sector_edges
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

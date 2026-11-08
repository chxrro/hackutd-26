"""Simulated-world tests (haptic_hub/sim). SIMULATION ONLY: synthetic frames, the
real hub, and the real belt firmware logic built for the host. No glasses,
phone, BLE radio or motors are involved.

By default a fast subset of scenarios runs (~25 s). HUB_SIM_ALL=1 runs every
gating scenario (~2.5 min); `python -m haptic_hub.sim --probes` also runs the
limitation probes.
"""

import asyncio
import math
import os

import numpy as np
import pytest

from haptic_hub.sim import firmware
from haptic_hub.sim.render import CameraModel, Renderer, sector_ground_truth
from haptic_hub.sim.scenarios import SCENARIOS
from haptic_hub.sim.world import Walker, World, pole

try:
    firmware.build_library()
    FIRMWARE_ERROR = None
except firmware.FirmwareUnavailable as exc:  # no C++ compiler on this machine
    FIRMWARE_ERROR = str(exc)
needs_firmware = pytest.mark.skipif(FIRMWARE_ERROR is not None, reason=f"belt firmware not built: {FIRMWARE_ERROR}")

GOLDEN = bytes.fromhex("01010 7fa00 0500 4080c0ff".replace(" ", ""))  # docs/protocol.md


@needs_firmware
def test_firmware_shim_decodes_golden_vector_and_enforces_ttl():
    fw = firmware.BeltFirmware(5)
    assert fw.update(0) == (firmware.FirmwareState.IDLE, (0, 0, 0, 0, 0))  # silent before the first command
    assert fw.on_payload(GOLDEN, 1000) == 0
    assert fw.update(1100) == (firmware.FirmwareState.ACTIVE, (0, 64, 128, 192, 255))
    state, levels = fw.update(1250)  # ttl 250 ms expired -> fault pattern (first pulse)
    assert state == firmware.FirmwareState.LINK_LOST and set(levels) == {180}
    assert fw.on_payload(b"\x02" + GOLDEN[1:], 1300) != 0  # wrong version rejected


@needs_firmware
def test_firmware_shim_link_lost_then_dormant():
    fw = firmware.BeltFirmware(5)
    fw.on_payload(GOLDEN, 0)
    fw.on_link_lost(10)
    assert fw.update(20)[0] == firmware.FirmwareState.LINK_LOST
    assert fw.update(10 + 30000)[0] == firmware.FirmwareState.DORMANT  # bounded fault alert


def test_renderer_depth_matches_geometry():
    cam = CameraModel()
    world = World([pole(0.0, 2.0, radius=0.1)], Walker())
    r = Renderer(cam, scale=1.0).render(world)
    h, w = r.depth.shape
    assert r.image.shape == (cam.height, cam.width, 3)
    assert r.depth[h // 2, w // 2] == pytest.approx(1.9, abs=0.02)  # pole front surface
    # floor straight down the bottom row: z = eye height / tan(angle below the axis)
    y_c = (h - 0.5 - h / 2) / cam.focal_px
    assert r.depth[h - 1, 5] == pytest.approx(1.6 / y_c, rel=0.02)


def test_sector_ground_truth_left_center_right():
    cam = CameraModel()
    for x, sector in ((-0.55, 0), (0.0, 2), (0.55, 4)):
        ranges = sector_ground_truth(World([pole(x, 1.2, radius=0.05)], Walker()), cam, 5)
        hit = [i for i, d in enumerate(ranges) if d is not None]
        assert hit == [sector], (x, ranges)
        assert ranges[sector] == pytest.approx(math.hypot(x, 1.2) - 0.05, abs=0.05)


def test_person_gets_a_box_and_walls_do_not():
    sc = SCENARIOS["person_vs_pole"]
    r = Renderer(sc.camera).render(sc.build())
    assert [b.label for b in r.boxes] == ["person"]
    b = r.boxes[0]
    assert 0 <= b.x1 < b.x2 <= sc.camera.width and b.x2 <= sc.camera.width / 2 + 40  # ahead-left
    assert np.isfinite(r.depth).all()


FAST = ["pole_left", "person_vs_pole", "camera_freeze", "belt_writes_lost"]
GATING = [n for n, sc in SCENARIOS.items() if "probe" not in sc.tags]
SELECTED = GATING if os.environ.get("HUB_SIM_ALL") else FAST


@needs_firmware
@pytest.mark.parametrize("name", SELECTED)
def test_scenario(name):
    from haptic_hub.sim.runner import run_scenario

    report = asyncio.run(run_scenario(SCENARIOS[name]))
    failed = [f"{c.name}: {c.detail}" for c in report.checks if not c.passed]
    assert not failed, f"SIMULATION scenario {name} failed:\n" + "\n".join(failed)

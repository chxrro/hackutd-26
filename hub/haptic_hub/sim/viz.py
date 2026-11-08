"""Side-by-side picture of a running scenario: what the glasses see, a top-down
map with ground truth, and what the belt firmware is doing. Written to an MP4
and/or shown live."""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from haptic_hub.config import HubConfig
from haptic_hub.sim.firmware import BeltSample
from haptic_hub.sim.render import Render
from haptic_hub.sim.world import Box, Cylinder, World

PANEL_H = 560
MAP_W = 560
MAP_PX_PER_M = 55.0
BELT_H = 120
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _level_color(level: int) -> tuple[int, int, int]:
    if level <= 0:
        return (70, 70, 70)
    a = level / 255
    return (40, int(220 * (1 - a) + 40 * a), 255)  # yellow -> red (BGR)


class SimView:
    def __init__(self, scenario, record: Path | None, show: bool, cfg: HubConfig) -> None:
        self.scenario, self.show, self.cfg = scenario, show, cfg
        self.writer: cv2.VideoWriter | None = None
        self.record = record
        cam = scenario.camera
        self.cam_w = round(cam.width * PANEL_H / cam.height)
        self.size = (self.cam_w + MAP_W, PANEL_H + BELT_H)
        if record is not None:
            record.parent.mkdir(parents=True, exist_ok=True)
            self.writer = cv2.VideoWriter(str(record), cv2.VideoWriter_fourcc(*"mp4v"), 15, self.size)

    def draw(self, t, render: Render, world: World, ranges, belt: BeltSample | None, result, status) -> bool:
        canvas = np.zeros((self.size[1], self.size[0], 3), np.uint8)
        cam = cv2.resize(render.image, (self.cam_w, PANEL_H), interpolation=cv2.INTER_AREA)
        pc = self.cfg.perception
        if pc.depth_geometry == "band":
            y0, y1 = int(PANEL_H * pc.band_top), int(PANEL_H * pc.band_bottom)
            cv2.rectangle(cam, (0, y0), (self.cam_w - 1, y1), (255, 255, 255), 1)  # depth band
        else:  # floor mode: every row can hold an obstacle
            y0, y1 = 0, PANEL_H - 1
        floor = (result.debug.get("floor") if result is not None else None) or {}
        if floor:
            cv2.putText(cam, f"floor {floor['source']} pitch {floor['pitch_deg']:+.0f} h {floor['height_m']:.2f}m",
                        (4, PANEL_H - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)  # fmt: skip
        n = pc.sectors
        for i in range(1, n):
            x = round(i * self.cam_w / n)
            cv2.line(cam, (x, y0), (x, y1), (255, 255, 255), 1)
        k = self.cam_w / render.image.shape[1]
        for b in render.boxes:
            cv2.rectangle(cam, (int(b.x1 * k), int(b.y1 * k)), (int(b.x2 * k), int(b.y2 * k)), (0, 255, 0), 1)
        canvas[:PANEL_H, : self.cam_w] = cam
        canvas[:PANEL_H, self.cam_w :] = self._map(world, ranges, belt)
        self._belt_strip(canvas, t, ranges, belt, result, status)
        if self.writer is not None:
            self.writer.write(canvas)
        if self.show:
            cv2.imshow("haptic-hub sim", canvas)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                self.show = False
                cv2.destroyAllWindows()
                return self.writer is not None
        return True

    def _map(self, world: World, ranges, belt: BeltSample | None) -> np.ndarray:
        img = np.full((PANEL_H, MAP_W, 3), 32, np.uint8)
        pose = world.pose()
        cx, cy = MAP_W / 2, PANEL_H * 0.72

        def px(x: float, y: float) -> tuple[int, int]:
            return int(cx + (x - pose.x) * MAP_PX_PER_M), int(cy - (y - pose.y) * MAP_PX_PER_M)

        for gx in range(-10, 11):
            for gy in range(-10, 11):
                p = px(math.floor(pose.x) + gx, math.floor(pose.y) + gy)
                cv2.circle(img, p, 1, (70, 70, 70), -1)
        for ob in world.obstacles:
            color = tuple(int(v) for v in ob.color)
            if isinstance(ob, Cylinder):
                cv2.circle(img, px(*ob.center), max(2, int(ob.radius * MAP_PX_PER_M)), color, -1)
            elif isinstance(ob, Box):
                cv2.fillPoly(img, [np.array([px(x, y) for x, y in ob.corners()], np.int32)], color)
            if ob.label:
                cv2.putText(img, ob.label, px(ob.center[0] + 0.3, ob.center[1]), FONT, 0.4, (220, 220, 220), 1)

        cam = self.scenario.camera
        n = len(ranges)
        edges = np.linspace(0, cam.width, n + 1)
        for i in range(n):
            a0 = pose.camera_yaw + float(cam.column_azimuth(edges[i]))
            a1 = pose.camera_yaw + float(cam.column_azimuth(edges[i + 1] - 1))
            level = 0 if belt is None or belt.signalling_fault else belt.levels[i]
            r = ranges[i] if ranges[i] is not None else 6.0
            pts = [px(pose.x, pose.y)] + [px(pose.x + math.sin(a) * r, pose.y + math.cos(a) * r)
                                          for a in np.linspace(a0, a1, 6)]  # fmt: skip
            overlay = img.copy()
            cv2.fillPoly(overlay, [np.array(pts, np.int32)], _level_color(level) if level else (90, 90, 90))
            img = cv2.addWeighted(overlay, 0.35, img, 0.65, 0)
        w = world.walker
        cv2.circle(img, px(pose.x, pose.y), int(w.body_radius * MAP_PX_PER_M), (230, 230, 230), 2)
        hx, hy = math.sin(pose.heading), math.cos(pose.heading)
        cv2.arrowedLine(img, px(pose.x, pose.y), px(pose.x + hx * 0.6, pose.y + hy * 0.6), (230, 230, 230), 2)
        cv2.putText(img, "top-down (north up)  white arrow = torso/belt", (8, 18), FONT, 0.45, (200, 200, 200), 1)
        if world.collisions:
            cv2.putText(img, f"COLLISION: {world.collisions[-1][1]}", (8, 40), FONT, 0.6, (0, 0, 255), 2)
        return img

    def _belt_strip(self, canvas, t, ranges, belt: BeltSample | None, result, status) -> None:
        y = PANEL_H
        canvas[y:] = (20, 20, 20)
        n = len(ranges)
        fault = belt is not None and belt.signalling_fault
        for i in range(n):
            x = 40 + i * 70
            level = belt.levels[i] if belt else 0
            color = (200, 0, 200) if fault and level else _level_color(0 if fault else level)
            cv2.circle(canvas, (x, y + 40), 24, color, -1)
            r = ranges[i]
            cv2.putText(canvas, "-" if r is None else f"{r:.1f}m", (x - 22, y + 86), FONT, 0.45, (200, 200, 200), 1)
            if result is not None:
                cv2.putText(canvas, f"r{result.sectors[i].risk:.2f}", (x - 22, y + 106), FONT, 0.4, (150, 150, 150), 1)
        tx = 40 + n * 70
        state = belt.state.name.lower() if belt else "-"
        lines = [
            f"{self.scenario.name}  t={t:4.1f}s",
            f"belt firmware: {state}" + ("  FAULT PATTERN" if fault else ""),
            "hub: " + ("healthy" if status and status.get("healthy") else str(status.get("reason") if status else "-")),
        ]
        for j, line in enumerate(lines):
            cv2.putText(canvas, line, (tx, y + 28 + j * 26), FONT, 0.55, (230, 230, 230), 1)

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()
        if self.show:
            cv2.destroyAllWindows()

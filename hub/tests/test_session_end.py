"""Wearer's deliberate Stop vs. a lost phone (docs/protocol.md, "end_session").

end_session (paired only) -> session-ended cue once, then quiet, status
"stopped by wearer" (never healthy). A phone that goes away without it still
gets the fault pattern. A new frame or phone_status "starting" starts a new
session. No models, no hardware, no network beyond loopback."""

import asyncio
import json

import numpy as np
from test_control import LEVELS, OK, FakeClock, _play, ack, run
from test_end_to_end import _connect_with_retry, _free_port
from websockets.asyncio.client import connect

from haptic_hub import cues
from haptic_hub.app import Hub
from haptic_hub.belt.sim import SimBelt
from haptic_hub.config import HubConfig
from haptic_hub.control import PAUSED_REASON, RESUME_REASON, STOPPED_REASON, Control
from haptic_hub.perception.sweep import SweepPerception
from haptic_hub.protocol import BeltCommand, BeltMode, FramePacket, encode_frame_packet
from haptic_hub.sources.glasses import GlassesServer
from haptic_hub.types import Health

NO_FRAMES = Health(False, "no camera frames for 1.2 s")
END = {"type": "control", "id": 7, "action": "end_session"}


def _stopped(clock=None):
    c = Control(HubConfig(), clock=clock or FakeClock())
    a = ack(c, END)
    assert a["ok"] and a["error"] is None
    return c


# -- Control ------------------------------------------------------------------


def test_end_session_plays_the_session_end_cue_once_then_stays_quiet():
    c = _stopped()
    ticks = _play(c, 0.0, 3.0, health=NO_FRAMES)  # frames stop right after Stop: expected, not a fault
    assert {reason for _, _, reason in ticks} == {STOPPED_REASON} and STOPPED_REASON == "stopped by wearer"
    assert all(cmd.mode != BeltMode.FAULT and cmd.ttl_ms == LEVELS.ttl_ms for _, cmd, _ in ticks)
    felt = [cmd.levels for _, cmd, _ in ticks if any(cmd.levels)]
    pulses = [lv for i, lv in enumerate(felt) if i == 0 or felt[i - 1] != lv]
    assert pulses == [(0, 200, 0, 200, 0), (0, 155, 0, 155, 0), (0, 110, 0, 110, 0)]
    end = cues.duration(cues.session_end_cue(5))
    assert all(cmd == BeltCommand.off(5, LEVELS.ttl_ms) for t, cmd, _ in ticks if t >= end)  # quiet after, for good


def test_stopped_status_is_never_healthy_and_names_no_obstacles():
    c = _stopped()
    _, reason = c.apply(LEVELS, OK, "connected", 0.0)
    assert reason == STOPPED_REASON  # even with fresh frames still in hand: never presented as clear
    fields = c.status_fields(None, OK, 5)
    assert fields["stopped"] is True and fields["paused"] is False
    assert fields["sectors"] == [{"kind": None, "uncertain": False}] * 5
    assert Control(HubConfig()).status_fields(None, OK, 5)["stopped"] is False


def test_lost_phone_without_end_session_still_plays_the_fault_pattern():
    c = Control(HubConfig(), clock=FakeClock())
    fault = BeltCommand.fault(5, LEVELS.ttl_ms)  # what HapticPolicy sends when health is bad
    for _, cmd, reason in _play(c, 0.0, 1.0, cmd=fault, health=NO_FRAMES):
        assert (cmd, reason) == (fault, NO_FRAMES.reason)
    assert c.status_fields(None, NO_FRAMES, 5)["stopped"] is False


def test_unpaired_end_session_is_ignored_and_the_fault_stays():
    c = Control(HubConfig(), clock=FakeClock())
    a = ack(c, END, paired=False)
    assert (a["ok"], a["error"]) == (False, "not paired")
    assert c.stopped is False
    fault = BeltCommand.fault(5, LEVELS.ttl_ms)
    assert c.apply(fault, NO_FRAMES, "connected", 0.0) == (fault, NO_FRAMES.reason)


def test_unpaired_websocket_message_cannot_stop():
    c = Control(HubConfig(), clock=FakeClock())

    class Conn:
        sent = []

        async def send(self, text):
            self.sent.append(json.loads(text))

    async def go():
        conn = Conn()
        c.on_client_message(conn, {"type": "hello", "client": "x", "version": 1})  # no code
        c.on_client_message(conn, END)
        c.on_client_message(conn, {"type": "phone_status", "stream": "stopped", "reason": "user_stop"})
        await asyncio.sleep(0.05)
        return conn.sent

    sent = run(go())
    assert sent and sent[0]["error"] == "not paired" and c.stopped is False


def test_a_frame_after_the_stop_starts_a_new_session_with_the_resume_cue():
    clock = FakeClock()
    c = _stopped(clock)
    stopped_at = clock.t
    c.note_frame(stopped_at - 0.05)  # sent before Stop, arrived before end_session: not a new session
    assert c.stopped
    _play(c, 0.0, 2.0, health=NO_FRAMES)
    c.note_frame(stopped_at + 5.0)
    assert not c.stopped
    ticks = _play(c, 10.0, 1.0)
    assert ticks[0][2] == RESUME_REASON  # alerts live again, felt
    assert ticks[-1][1:] == (LEVELS, None)
    assert c.status_fields(None, OK, 5)["stopped"] is False


def test_phone_status_starting_from_any_connection_ends_the_stop_and_missing_frames_then_fault():
    c = _stopped()
    for stream in ("stopped", "stopping", "streaming", "paused", None):
        c.on_client_message(object(), {"type": "phone_status", "stream": stream})
        assert c.stopped, stream  # only "starting" (or a new frame) starts a session
    c.on_client_message(None, {"type": "phone_status", "stream": "starting", "frame_source": "glasses"})
    assert not c.stopped
    fault = BeltCommand.fault(5, LEVELS.ttl_ms)
    assert c.apply(fault, Health(False, "waiting for camera frames"), "connected", 0.0)[0] == fault


def test_end_session_clears_pause_and_is_idempotent():
    c = Control(HubConfig(), clock=FakeClock())
    ack(c, {"type": "control", "id": 1, "action": "pause"})
    _play(c, 0.0, 0.3)
    assert ack(c, END)["settings"]["paused"] is False
    first = _play(c, 1.0, 0.5, health=NO_FRAMES)
    ack(c, END)  # again: the cue doesn't restart
    rest = _play(c, 1.5, 2.0, health=NO_FRAMES)
    assert {r for _, _, r in first + rest} == {STOPPED_REASON}
    on = [t for t, cmd, _ in rest if any(cmd.levels)]
    assert max(on) < cues.duration(cues.session_end_cue(5)) - 0.5  # continued, not restarted
    assert PAUSED_REASON not in {r for _, _, r in rest}


def test_belt_problems_outrank_the_stop_and_the_cue_replays_after():
    c = _stopped()
    _play(c, 0.0, 2.0, health=NO_FRAMES)
    assert c.apply(LEVELS, NO_FRAMES, "disconnected", 2.0) == (BeltCommand.off(5, LEVELS.ttl_ms), "belt disconnected")
    fault = BeltCommand.fault(5, LEVELS.ttl_ms)
    assert c.apply(LEVELS, NO_FRAMES, "connected", 2.1, "belt not acknowledging") == (fault, "belt not acknowledging")
    again = _play(c, 3.0, 1.0, health=NO_FRAMES)
    assert {r for _, _, r in again} == {STOPPED_REASON} and any(any(cmd.levels) for _, cmd, _ in again)


# -- the hub over the real WebSocket -----------------------------------------------


def _jpeg():
    import cv2

    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    return buf.tobytes()


def _session(*, code: bool, end: bool, disconnect: bool, restart: bool):
    """A phone streams frames for 0.6 s, then (optionally) sends end_session and
    stops frames (optionally closing the socket), then (optionally) starts again.
    Returns the belt modes and hub statuses sampled after the stop, and after restart."""
    cfg = HubConfig()
    cfg.safety.frame_stale_s = 0.3

    async def scenario():
        port = _free_port()
        server = GlassesServer("127.0.0.1", port)
        belt = SimBelt(5)
        hub = Hub(cfg, server, SweepPerception(5), belt, status_sink=server)
        task = asyncio.create_task(hub.run())
        jpeg, seq = _jpeg(), 0

        async def stream(ws, seconds):
            nonlocal seq
            end_t = asyncio.get_running_loop().time() + seconds
            while asyncio.get_running_loop().time() < end_t:
                seq += 1
                await ws.send(encode_frame_packet(FramePacket(seq, 0, 8, 8, jpeg)))
                await asyncio.sleep(1 / 15)

        async def sample(seconds):
            out = []
            end_t = asyncio.get_running_loop().time() + seconds
            while asyncio.get_running_loop().time() < end_t:
                out.append((belt.last.mode, hub.status()))
                await asyncio.sleep(0.05)
            return out

        phone = await _connect_with_retry(f"ws://127.0.0.1:{port}")
        hello = {"type": "hello", "client": "ios", "version": 1}
        if code:
            hello["control_code"] = hub.control.code
        await phone.send(json.dumps(hello))
        await phone.send(json.dumps({"type": "phone_status", "stream": "starting", "frame_source": "glasses"}))
        await stream(phone, 0.6)
        assert hub.status()["healthy"], hub.status()
        if end:
            await phone.send(json.dumps(END))
        await phone.send(json.dumps({"type": "phone_status", "stream": "stopped", "frame_source": "glasses"}))
        if disconnect:
            await phone.close()
        await asyncio.sleep(0.1)  # the hub handles control requests in a task
        after_stop = await sample(2.0)
        after_restart = []
        if restart:
            if disconnect:
                phone = await connect(f"ws://127.0.0.1:{port}")
                await phone.send(json.dumps(hello))
            await phone.send(json.dumps({"type": "phone_status", "stream": "starting", "frame_source": "glasses"}))
            streaming = asyncio.create_task(stream(phone, 1.5))
            await asyncio.sleep(1.2)
            after_restart = await sample(0.25)
            await streaming
        await phone.close()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return after_stop, after_restart

    return run(asyncio.wait_for(scenario(), 20))


def test_hub_user_stop_is_quiet_not_faulted_even_after_the_phone_disconnects():
    after_stop, after_restart = _session(code=True, end=True, disconnect=True, restart=True)
    assert all(mode != BeltMode.FAULT for mode, _ in after_stop)
    assert all(s["reason"] == STOPPED_REASON and s["healthy"] is False and s["stopped"] for _, s in after_stop)
    assert after_stop[-1][0] == BeltMode.OFF and after_stop[-1][1]["mode"] == "off"
    # Start again: normal output, healthy, not stopped
    mode, s = after_restart[-1]
    assert mode == BeltMode.LEVELS and s["healthy"] and s["reason"] is None and s["stopped"] is False


def test_hub_lost_phone_without_end_session_faults():
    after_stop, _ = _session(code=True, end=False, disconnect=True, restart=False)
    late = after_stop[len(after_stop) // 2 :]
    assert all(mode == BeltMode.FAULT for mode, _ in late)
    assert all(s["reason"].startswith("no camera frames") and s["stopped"] is False for _, s in late)


def test_hub_unpaired_end_session_is_ignored_and_faults():
    after_stop, _ = _session(code=False, end=True, disconnect=False, restart=False)
    late = after_stop[len(after_stop) // 2 :]
    assert all(mode == BeltMode.FAULT for mode, _ in late)
    assert all(s["stopped"] is False for _, s in late)

"""`haptic-hub bench` against the simulated belt, a fake bleak client and a pty
standing in for the belt's serial port. No Bluetooth or hardware needed.

These test the bench itself. They say nothing about the real belt."""

import asyncio
import json
import os
import sys
import time

import pytest

import haptic_hub.bench.links as links
from haptic_hub.bench.cli import run_bench
from haptic_hub.bench.flow import AutoOperator, BenchAborted, BenchSettings, position_names
from haptic_hub.bench.links import BenchLinkError, EventLog, ObservedEvent, SimBenchLink, SimFirmware, effective_ttl_ms
from haptic_hub.bench.report import (
    EXIT_FAIL,
    EXIT_INCOMPLETE,
    EXIT_PASS,
    CheckResult,
    exit_code,
    percentile,
    render_summary,
    timing_stats,
    verdict,
    write_report,
)
from haptic_hub.bench.serial_log import parse_serial_line
from haptic_hub.protocol import BELT_SERVICE_UUID, BeltMode, decode_belt_command


class ScriptedOperator:
    """Answers from a list, in order; answers 'y' / default once the list runs out."""

    assumed = False

    def __init__(self, answers=(), delay_s=0.0):
        self.answers = list(answers)
        self.delay_s = delay_s
        self.questions: list[str] = []
        self.said: list[str] = []

    def say(self, text):
        self.said.append(text)

    async def _next(self, question, default):
        self.questions.append(question)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        answer = self.answers.pop(0) if self.answers else default
        if answer == "q":
            raise BenchAborted("operator quit")
        return answer

    async def ask(self, question, choices="ynr"):
        return await self._next(question, "y")

    async def ask_text(self, question, default):
        return await self._next(question, default)


def fast(**overrides) -> BenchSettings:
    base = dict(
        buzz_s=0.02, gap_s=0.01, ramp=(0, 90, 255), ramp_step_s=0.01, ttls_ms=(50, 120), ttl_hold_s=0.05,
        observe_s=0.05, fault_s=0.05, writes=5, send_hz=100.0, ttl_ms=250,
    )
    base.update(overrides)
    return BenchSettings(**base)


def by_name(report):
    return {c["name"]: c for c in report["checks"]}


def run(link, operator, settings, **kw):
    async def go():
        return await run_bench(link, operator, settings, link.observer if link.observer else None, **kw)

    return asyncio.run(go())


# ---------------------------------------------------------------- whole flow, simulated


def test_full_flow_passes_against_the_simulator_with_operator_yes():
    link = SimBenchLink(5)
    report = run(link, ScriptedOperator(), fast())
    checks = by_name(report)
    assert list(checks) == [
        "connect", "motor-map", "levels", "ttl", "write-timing", "fault-command", "fault-disconnect", "reconnect", "belt-log",
    ]
    assert {c["status"] for c in checks.values()} == {"pass"}, render_summary(report)
    assert report["verdict"] == "PASS" and report["simulated"] is True and report["belt"] == "sim"
    assert "SIMULATED" in render_summary(report)
    # TTL expiry is timed from the firmware model's state changes.
    for run_ in checks["ttl"]["data"]["runs"]:
        assert run_["auto_pass"] is True
        assert run_["effective_ttl_ms"] - 5 <= run_["observed_link_lost_ms"] <= run_["effective_ttl_ms"] + 60
    timing = checks["write-timing"]["data"]
    assert timing["without_response"]["n"] == 5 and timing["with_response"]["n"] == 5
    assert "NOT motor latency" in timing["measured_as"]
    assert link.is_connected is False, "the bench must close the link when done"


def test_yes_marks_person_observed_checks_unconfirmed_never_pass():
    report = run(SimBenchLink(5), AutoOperator(echo=None), fast())
    checks = by_name(report)
    for name in ("motor-map", "levels", "fault-command"):
        assert checks[name]["status"] == "unconfirmed", name
        assert "assumed" in checks[name]["observed_by"]
    # Automatic measurements still decide their own checks.
    assert checks["ttl"]["status"] == "pass" and checks["write-timing"]["status"] == "pass"
    assert report["verdict"] == "INCOMPLETE" and report["operator"] == "assumed (--yes)"
    assert exit_code(report["verdict"]) == EXIT_INCOMPLETE


def test_motor_map_mismatch_is_recorded_with_where_it_was_felt_and_a_fix():
    # motors 1 and 3 swapped: M1 felt at position 3, M3 felt at position 1
    answers = ["y", "n", "3", "y", "n", "1", "y"]
    report = run(SimBenchLink(5), ScriptedOperator(answers), fast(only=frozenset({"motor-map"})))
    mm = by_name(report)["motor-map"]
    assert mm["status"] == "fail" and report["verdict"] == "FAIL"
    felt = {m["motor"]: m["felt_at"] for m in mm["data"]["motors"]}
    assert felt == {0: 0, 1: 3, 2: 2, 3: 1, 4: 4}
    assert mm["data"]["suggested_index_order"] == [0, 3, 2, 1, 4]
    assert any("{25, 32, 27, 26, 33}" in n for n in mm["notes"])
    assert exit_code(report["verdict"]) == EXIT_FAIL


def test_motor_not_felt_at_all_is_a_mismatch_without_a_pin_suggestion():
    report = run(SimBenchLink(5), ScriptedOperator(["y", "y", "n", "-"]), fast(only=frozenset({"motor-map"})))
    mm = by_name(report)["motor-map"]
    assert mm["status"] == "fail" and "M2 felt at nowhere" in mm["summary"]
    assert "suggested_index_order" not in mm["data"]
    assert any("not felt at all" in n for n in mm["notes"])


def test_repeat_replays_the_stimulus():
    link = SimBenchLink(5)
    once = run(link, ScriptedOperator(), fast(only=frozenset({"fault-command"})))
    writes_once = link.writes
    link2 = SimBenchLink(5)
    report = run(link2, ScriptedOperator(["r", "r", "y"]), fast(only=frozenset({"fault-command"})))
    fc = by_name(report)["fault-command"]
    assert fc["data"]["attempts"] == 3 and fc["status"] == "pass"
    assert link2.writes > writes_once
    assert by_name(once)["fault-command"]["data"]["attempts"] == 1


def test_keepalive_keeps_the_belt_quiet_while_the_operator_thinks():
    """A slow answer must not let the TTL lapse into the fault pattern during the
    motor map (the operator would feel a fault and think something broke)."""
    link = SimBenchLink(5)
    settings = fast(only=frozenset({"motor-map"}), ttl_ms=60, send_hz=50.0)
    report = run(link, ScriptedOperator(delay_s=0.2), settings)
    assert by_name(report)["motor-map"]["status"] == "pass"
    states = [e.value for e in link.observer.events if e.kind == "state"]
    # Only the final close (off with a 50 ms TTL, then drop) may produce link-lost.
    assert states.count("link-lost") <= 1 and states[-1] == "link-lost"


def test_ttl_below_the_firmware_minimum_is_timed_against_the_clamped_value():
    report = run(SimBenchLink(5), ScriptedOperator(), fast(only=frozenset({"ttl"}), ttls_ms=(10,)))
    r = by_name(report)["ttl"]["data"]["runs"][0]
    assert r["effective_ttl_ms"] == 50 == effective_ttl_ms(10)
    assert 45 <= r["observed_link_lost_ms"] <= 120
    assert effective_ttl_ms(5000) == 1000


class NeverExpires(SimFirmware):
    def on_command(self, cmd):  # a broken firmware that ignores the TTL
        self.last = cmd
        self._set_state("active")


def test_ttl_check_fails_when_the_belt_never_reports_link_lost():
    link = SimBenchLink(5)
    link.firmware = NeverExpires(link.observer)
    report = run(link, ScriptedOperator(), fast(only=frozenset({"ttl"}), ttls_ms=(50,)))
    ttl = by_name(report)["ttl"]
    assert ttl["status"] == "fail" and "not seen" in ttl["summary"]


def test_ttl_operator_no_fails_even_when_the_log_looks_right():
    report = run(SimBenchLink(5), ScriptedOperator(["n", "y"]), fast(only=frozenset({"ttl"})))
    assert by_name(report)["ttl"]["status"] == "fail"


def test_levels_fail_when_the_hub_minimum_alert_level_cannot_be_felt():
    report = run(SimBenchLink(5), ScriptedOperator(["y", "120"]), fast(only=frozenset({"levels"}), ramp=(0, 60, 90, 120, 255)))
    lv = by_name(report)["levels"]
    assert lv["status"] == "fail" and lv["data"]["lowest_felt"] == 120
    assert any("min_level" in n for n in lv["notes"])
    ok = run(SimBenchLink(5), ScriptedOperator(["y", "60"]), fast(only=frozenset({"levels"})))
    assert by_name(ok)["levels"]["status"] == "pass"


def test_ramp_reaches_the_belt_as_valid_commands():
    link = SimBenchLink(5)
    sent = []
    original = link.belt.send

    async def spy(cmd):
        sent.append(cmd)
        await original(cmd)

    link.belt.send = spy
    run(link, ScriptedOperator(), fast(only=frozenset({"levels"}), ramp=(0, 1, 128, 255)))
    centre = {c.levels[2] for c in sent if c.mode == BeltMode.LEVELS}
    assert {1, 128, 255} <= centre
    assert all(c.levels[i] == 0 for c in sent for i in (0, 1, 3, 4))


def test_motor_count_mismatch_fails_connect_and_skips_the_rest():
    report = run(SimBenchLink(5, reported_motors=4), ScriptedOperator(), fast())
    checks = by_name(report)
    assert checks["connect"]["status"] == "fail" and "4 motors" in checks["connect"]["summary"]
    assert all(checks[n]["status"] == "skipped" for n in ("motor-map", "levels", "ttl", "write-timing"))
    assert report["verdict"] == "FAIL"


def test_quit_writes_a_partial_report_and_closes_the_link():
    link = SimBenchLink(5)
    report = run(link, ScriptedOperator(["y", "q"]), fast())
    checks = by_name(report)
    assert report["aborted"] == "operator quit" and report["verdict"] == "INCOMPLETE"
    assert checks["motor-map"]["status"] == "skipped" and checks["write-timing"]["status"] == "skipped"
    assert link.is_connected is False
    assert exit_code(report["verdict"], aborted=True) == 130


def test_disconnect_check_reports_what_the_belt_logged():
    report = run(SimBenchLink(5), ScriptedOperator(), fast(only=frozenset({"fault-disconnect"})))
    checks = by_name(report)
    fd = checks["fault-disconnect"]
    assert fd["status"] == "pass"
    assert fd["data"]["belt_saw_disconnect_ms"] is not None and fd["data"]["link_lost_ms"] is not None
    assert checks["reconnect"]["status"] == "pass"


def test_belt_log_flags_rejected_commands_and_reboots():
    link = SimBenchLink(5)

    async def go():
        async def inject():
            await asyncio.sleep(0.02)
            now = time.monotonic()
            link.observer.add(ObservedEvent(now, "rejected", "bad version, 11 bytes", "[ble] rejected command (bad version, 11 bytes)"))
            link.observer.add(ObservedEvent(now, "boot", None, "[belt] 5 motors, self-test"))

        task = asyncio.create_task(inject())
        report = await run_bench(link, ScriptedOperator(), fast(only=frozenset({"write-timing"})), link.observer)
        await task
        return report

    report = asyncio.run(go())
    checks = by_name(report)
    assert checks["write-timing"]["status"] == "fail" and "rejected" in checks["write-timing"]["summary"]
    assert checks["belt-log"]["status"] == "fail" and "rebooted" in checks["belt-log"]["summary"]


# ---------------------------------------------------------------- report


def test_report_is_valid_json_in_its_own_file_and_never_overwritten(tmp_path):
    report = run(SimBenchLink(5), AutoOperator(echo=None), fast(only=frozenset({"write-timing"})), note="bench test")
    first = write_report(report, tmp_path, stamp="20261108-120000")
    second = write_report(report, tmp_path, stamp="20261108-120000")
    assert first.name == "bench-20261108-120000-sim.json" and second.name == "bench-20261108-120000-sim-2.json"
    loaded = json.loads(first.read_text())
    assert loaded["schema"] == "haptic-hub-bench/1" and loaded["note"] == "bench test"
    assert loaded["simulated"] is True and loaded["connection"]["address"] == "sim"
    assert {c["name"] for c in loaded["checks"]} == {"connect", "write-timing", "reconnect", "belt-log"} - {"reconnect"}
    assert any("simulated" in x for x in loaded["not_tested_by_bench"])
    assert not list(tmp_path.glob("*.tmp"))


def test_summary_table_lists_every_check_and_the_verdict():
    report = {
        "simulated": False,
        "verdict": "FAIL",
        "checks": [
            {"name": "connect", "status": "pass", "summary": "ok"},
            {"name": "motor-map", "status": "fail", "summary": "1 mismatch"},
        ],
    }
    text = render_summary(report)
    assert "connect" in text and "PASS" in text and "FAIL" in text and "1 mismatch" in text
    assert "verdict: FAIL" in text and "SIMULATED" not in text


def test_verdict_and_stats_rules():
    c = lambda s: CheckResult("x", s, "")
    assert verdict([c("pass"), c("pass")]) == "PASS"
    assert verdict([c("pass"), c("unconfirmed")]) == "INCOMPLETE"
    assert verdict([c("pass"), c("skipped")]) == "INCOMPLETE"
    assert verdict([c("unconfirmed"), c("error")]) == "FAIL"
    assert verdict([c("pass")], aborted=True) == "INCOMPLETE"
    assert verdict([]) == "INCOMPLETE"
    assert exit_code("PASS") == EXIT_PASS and exit_code("FAIL", aborted=True) == EXIT_FAIL
    with pytest.raises(ValueError):
        CheckResult("x", "great", "")
    values = [float(v) for v in range(1, 11)]
    assert percentile(values, 0.9) == 9.0 and percentile(values, 0.5) == 5.0
    stats = timing_stats([3.0, 1.0, 2.0])
    assert stats == {"n": 3, "min_ms": 1.0, "median_ms": 2.0, "p90_ms": 3.0, "max_ms": 3.0}
    assert timing_stats([])["median_ms"] is None


def test_settings_reject_out_of_range_values():
    with pytest.raises(ValueError):
        BenchSettings(level=300)
    with pytest.raises(ValueError):
        BenchSettings(ramp=(0, 256))
    with pytest.raises(ValueError):
        BenchSettings(ramp_motor=5)
    with pytest.raises(ValueError):
        BenchSettings(only=frozenset({"everything"}))
    assert position_names(5)[2] == "centre" and position_names(3) == ["far left", "centre", "far right"]


def test_cli_bench_sim_writes_a_report_and_exits_with_the_verdict(tmp_path, monkeypatch, capsys):
    from haptic_hub import cli

    monkeypatch.setattr(
        sys, "argv",
        ["haptic-hub", "bench", "--belt", "sim", "--yes", "--only", "write-timing", "--writes", "3", "--report-dir", str(tmp_path)],
    )
    cli.main()  # PASS -> returns normally (exit 0)
    out = capsys.readouterr().out
    assert "verdict: PASS" in out and "SIMULATED" in out
    [path] = tmp_path.glob("bench-*-sim.json")
    assert json.loads(path.read_text())["verdict"] == "PASS"

    monkeypatch.setattr(sys, "argv", ["haptic-hub", "bench", "--belt", "sim", "--only", "nonsense"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2  # argparse usage error


# ---------------------------------------------------------------- serial log


def test_parse_serial_lines_from_the_firmware():
    t = 1.0
    assert parse_serial_line("[belt] state -> link-lost\r", t) == ObservedEvent(t, "state", "link-lost", "[belt] state -> link-lost")
    assert parse_serial_line("[ble] hub connected", t).kind == "connected"
    assert parse_serial_line("[ble] hub disconnected", t).kind == "disconnected"
    rej = parse_serial_line("[ble] rejected command (bad version, 11 bytes)", t)
    assert rej.kind == "rejected" and rej.value == "bad version, 11 bytes"
    assert parse_serial_line("[belt] 5 motors, self-test", t).kind == "boot"
    assert parse_serial_line("[console] type h for bench-test commands", t).kind == "other"


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs a POSIX pty")
def test_serial_observer_reads_a_tty_and_wakes_waiters():
    from haptic_hub.bench.serial_log import SerialObserver

    master, slave = os.openpty()
    path = os.ttyname(slave)

    async def go():
        obs = SerialObserver(path)
        await obs.start()
        try:
            mark = obs.mark()
            os.write(master, b"[belt] state -> active\r\n[ble] hub disc")
            await asyncio.sleep(0.05)
            os.write(master, b"onnected\r\n[belt] state -> link-lost\r\n")
            lost = await obs.wait_for("state", "link-lost", since=mark, timeout=2.0)
            drop = await obs.wait_for("disconnected", since=mark, timeout=0.1)
            missing = await obs.wait_for("state", "dormant", since=mark, timeout=0.05)
            return lost, drop, missing, [e.kind for e in obs.events]
        finally:
            await obs.stop()

    try:
        lost, drop, missing, kinds = asyncio.run(go())
    finally:
        os.close(master)
        os.close(slave)
    assert lost is not None and lost.line == "[belt] state -> link-lost"
    assert drop is not None, "a line split across reads must be reassembled"
    assert missing is None
    assert kinds == ["state", "disconnected", "state"]


def test_event_log_wait_for_times_out_and_respects_since():
    async def go():
        log = EventLog("test")
        log.add(ObservedEvent(time.monotonic(), "state", "link-lost", "old"))
        mark = log.mark()
        assert await log.wait_for("state", "link-lost", since=mark, timeout=0.02) is None
        asyncio.get_running_loop().call_later(0.01, log.add, ObservedEvent(time.monotonic(), "state", "link-lost", "new"))
        ev = await log.wait_for("state", "link-lost", since=mark, timeout=1.0)
        return ev

    assert asyncio.run(go()).line == "new"


# ---------------------------------------------------------------- BLE link with a fake bleak


class FakeAdv:
    def __init__(self, name="HapticBelt", rssi=-48):
        self.local_name = name
        self.rssi = rssi
        self.service_uuids = [BELT_SERVICE_UUID]


class FakeDevice:
    def __init__(self, address="AA:BB:CC:DD:EE:FF", name="HapticBelt"):
        self.address = address
        self.name = name


class FakeClient:
    info = b"\x01\x05"
    fail_writes = False
    made: list["FakeClient"] = []

    def __init__(self, target, disconnected_callback=None):
        self.address = target.address
        self.is_connected = False
        self.writes: list[tuple[bytes, bool]] = []
        self.disconnects = 0
        self.mtu_size = 23
        FakeClient.made.append(self)

    async def connect(self):
        self.is_connected = True

    async def read_gatt_char(self, uuid):
        assert uuid == links.BELT_INFO_UUID
        return self.info

    async def write_gatt_char(self, uuid, data, response=False):
        assert uuid == links.BELT_COMMAND_UUID
        if self.fail_writes:
            raise links.BleakError("gatt write failed")
        self.writes.append((bytes(data), response))

    async def disconnect(self):
        self.disconnects += 1
        self.is_connected = False


@pytest.fixture
def fake_ble(monkeypatch):
    FakeClient.info, FakeClient.fail_writes, FakeClient.made = b"\x01\x05", False, []
    device, adv = FakeDevice(), FakeAdv()

    class FakeScanner:
        @staticmethod
        async def find_device_by_filter(match, timeout=10.0):
            return device if match(device, adv) else None

    monkeypatch.setattr(links, "BleakClient", FakeClient)
    monkeypatch.setattr(links, "BleakScanner", FakeScanner)
    return FakeClient


def test_ble_link_reports_address_rssi_and_writes_the_wire_format(fake_ble):
    async def go():
        link = links.BleBenchLink(5)
        info = await link.connect(1.0)
        from haptic_hub.protocol import BeltCommand

        await link.write(BeltCommand.fault(5), response=True)
        await link.write(BeltCommand.off(5))
        await link.close()
        return info, link

    info, link = asyncio.run(go())
    assert info.address == "AA:BB:CC:DD:EE:FF" and info.rssi == -48 and info.name == "HapticBelt"
    assert info.protocol_version == 1 and info.motor_count == 5 and info.info_hex == "01 05" and info.mtu == 23
    client = fake_ble.made[0]
    (first, resp1), (_, resp2), (last, _) = client.writes
    assert decode_belt_command(first)[0].mode == BeltMode.FAULT and resp1 is True and resp2 is False
    assert decode_belt_command(last)[0].mode == BeltMode.OFF, "close() sends off before dropping the link"
    assert client.disconnects == 1 and not link.is_connected


def test_ble_link_short_info_disconnects_and_raises(fake_ble):
    fake_ble.info = b"\x01"
    with pytest.raises(BenchLinkError):
        asyncio.run(links.BleBenchLink(5).connect(1.0))
    assert fake_ble.made[0].disconnects == 1, "never leave a half-open link: the belt stops advertising"


def test_ble_link_not_found_and_write_errors_are_bench_errors(fake_ble):
    with pytest.raises(BenchLinkError, match="no 11:22 found"):
        asyncio.run(links.BleBenchLink(5, address="11:22").connect(0.1))

    async def go():
        link = links.BleBenchLink(5)
        await link.connect(1.0)
        fake_ble.fail_writes = True
        from haptic_hub.protocol import BeltCommand

        with pytest.raises(BenchLinkError):
            await link.write(BeltCommand.off(5))
        await link.disconnect()
        with pytest.raises(BenchLinkError, match="not connected"):
            await link.write(BeltCommand.off(5))

    asyncio.run(go())


def test_full_flow_over_the_fake_ble_link(fake_ble):
    """The real-belt code path end to end (no observer, so TTL is operator-only)."""
    report = asyncio.run(run_bench(links.BleBenchLink(5), ScriptedOperator(), fast()))
    checks = by_name(report)
    assert report["simulated"] is False and report["belt"] == "ble"
    assert all(c["status"] == "pass" for c in checks.values()), render_summary(report)
    assert "belt-log" not in checks
    assert any("--serial" in n for n in checks["ttl"]["notes"])
    assert checks["connect"]["data"]["rssi"] == -48
    assert len(fake_ble.made) == 2, "fault-disconnect drops the link, reconnect opens a new one"


# ---------------------------------------------------------------- sim backed by the real belt_core


def _firmware_link(motors=5):
    from haptic_hub.sim.firmware import FirmwareUnavailable

    try:
        return links.FirmwareSimLink(motors)
    except FirmwareUnavailable as exc:
        pytest.skip(f"belt_core emulation unavailable: {exc}")


def test_full_flow_against_the_real_belt_core_logic():
    link = _firmware_link()
    report = run(link, ScriptedOperator(), fast())
    checks = by_name(report)
    assert all(c["status"] == "pass" for c in checks.values()), render_summary(report)
    assert "belt_core" in report["observer"]
    for r in checks["ttl"]["data"]["runs"]:
        # firmware loop ticks every 5 ms; allow scheduler jitter on a loaded CI box
        assert r["effective_ttl_ms"] - 5 <= r["observed_link_lost_ms"] <= r["effective_ttl_ms"] + 60, r
    assert link.rejected == 0, "the firmware decoder accepted every bench command"
    states = [e.value for e in link.observer.events if e.kind == "state"]
    assert states[0] == "active" and "link-lost" in states


def test_real_belt_core_clamps_a_too_short_ttl():
    report = run(_firmware_link(), ScriptedOperator(), fast(only=frozenset({"ttl"}), ttls_ms=(10,)))
    r = by_name(report)["ttl"]["data"]["runs"][0]
    assert r["effective_ttl_ms"] == 50 and 45 <= r["observed_link_lost_ms"] <= 120


def test_sim_falls_back_to_the_python_model_without_a_compiler(monkeypatch):
    import haptic_hub.sim.firmware as firmware

    def unavailable(motors):
        raise firmware.FirmwareUnavailable("no C++ compiler found (set CXX)")

    monkeypatch.setattr(firmware, "BeltFirmware", unavailable)
    link, warning = links.make_sim_link(5)
    assert isinstance(link, SimBenchLink) and "Python TTL model" in warning


# ---------------------------------------------------------------- belt status characteristic (2026-11-08)


def test_parse_serial_mode_line():
    ev = parse_serial_line("[belt] mode -> fault (seq 12)", 1.0)
    assert (ev.kind, ev.value) == ("mode", "fault")


def test_belt_core_status_times_ttl_and_sees_the_fault_command():
    """FirmwareSimLink reports belt_core's own status payloads, so TTL expiry and
    the fault command are machine-checked; the feel stays with the operator."""
    link = _firmware_link()
    report = run(link, ScriptedOperator(), fast())
    checks = by_name(report)
    assert all(c["status"] == "pass" for c in checks.values()), render_summary(report)
    assert checks["connect"]["data"]["status"] is True and checks["connect"]["data"]["build"] == "sim"
    for r in checks["ttl"]["data"]["runs"]:
        assert r["effective_ttl_ms"] - 5 <= r["status_expired_ms"] <= r["effective_ttl_ms"] + 60, r
    fault = checks["fault-command"]
    assert fault["data"]["status_fault_ms"] <= 60 and "operator" in fault["observed_by"]
    assert checks["reconnect"]["data"]["status_mode_on_reconnect"] == "disconnected"


def test_fault_command_with_status_stays_unconfirmed_under_yes():
    report = run(_firmware_link(), AutoOperator(echo=None), fast(only=frozenset({"fault-command"})))
    assert by_name(report)["fault-command"]["status"] == "unconfirmed"


def test_fault_command_fails_when_the_belt_status_never_says_fault():
    link = _firmware_link()
    real = link._status.on_payload

    def no_fault(data):  # a firmware that keeps reporting "off" while playing whatever it plays
        real(bytes([data[0], 1]) + bytes(data[2:]))

    link._status.on_payload = no_fault
    report = run(link, ScriptedOperator(), fast(only=frozenset({"fault-command", "ttl"})))
    checks = by_name(report)
    assert checks["fault-command"]["status"] == "fail" and "never reported mode fault" in checks["fault-command"]["summary"]
    assert checks["ttl"]["status"] == "fail", "status never said expired, although the serial log did"


def test_ble_link_subscribes_to_status_and_reads_the_build(fake_ble):
    from haptic_hub.protocol import BELT_STATUS_UUID, BeltFirmwareMode, BeltStatus, encode_belt_status

    def st(mode, seq):
        return encode_belt_status(BeltStatus(mode, seq, 10, 1000, 0x3268A632, 190, (0,) * 5))

    class Char:
        properties = ["read", "notify"]

    class Services:
        def get_characteristic(self, uuid):
            return Char() if uuid == BELT_STATUS_UUID else None

    class StatusClient(fake_ble):
        info = b"\x01\x05" + b"3268a632"
        services = Services()
        callback = None

        async def start_notify(self, uuid, callback):
            StatusClient.callback = callback

        async def read_gatt_char(self, uuid):
            if uuid == BELT_STATUS_UUID:
                return st(BeltFirmwareMode.IDLE, 0)
            return await super().read_gatt_char(uuid)

    links.BleakClient = StatusClient  # the fixture restores it

    async def go():
        link = links.BleBenchLink(5)
        info = await link.connect(1.0)
        StatusClient.callback(None, bytearray(st(BeltFirmwareMode.LEVELS, 1)))
        StatusClient.callback(None, bytearray(st(BeltFirmwareMode.LEVELS, 2)))  # same mode: no new event
        StatusClient.callback(None, bytearray(b"\x01\x02"))  # malformed: ignored
        StatusClient.callback(None, bytearray(st(BeltFirmwareMode.EXPIRED, 2)))
        return link, info

    link, info = asyncio.run(go())
    assert info.build == "3268a632" and info.status is True
    assert [e.value for e in link.status_log.events] == ["idle", "levels", "expired"]
    assert link._status.malformed == 1 and "max duty 190" in link.status_log.events[-1].line


def test_ble_link_without_status_is_older_firmware(fake_ble):
    info = asyncio.run(links.BleBenchLink(5).connect(1.0))
    assert info.status is False and info.build is None

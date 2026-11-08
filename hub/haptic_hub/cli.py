"""Command line entry point.

  haptic-hub run                         glasses -> vision -> BLE belt (the real thing)
  haptic-hub run --source webcam --belt sim --show
  haptic-hub run --source stream:rtsp://192.168.1.50:8554/live
  haptic-hub run --perception sweep      bench-test belt + phone without any model
  haptic-hub run --record                also save incoming frames to hub/recordings/ (opt-in)
  haptic-hub run --source replay:recordings/session-001 --belt sim --show   replay a session
  haptic-hub run --source webcam --belt sim --show --classifier local   skip the Nemotron labels
  haptic-hub run --source webcam --belt sim --dashboard   operator dashboard on http://127.0.0.1:8780/
  haptic-hub nim-bench --image photo.jpg measure cloud classifier round trips
  haptic-hub belt-test                   buzz each motor in order
  haptic-hub bench                       hardware-day belt checks with pass/fail + JSON report (--belt sim to dry-run)
  haptic-hub scan                        look for the belt over BLE
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket

from haptic_hub.config import HubConfig
from haptic_hub.protocol import BeltCommand, BeltMode

log = logging.getLogger("haptic_hub")


def _local_ip() -> str:
    """Best guess at this laptop's LAN address (what to type into the phone app)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))  # no packet is sent for UDP connect
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _make_belt(kind: str, motors: int, address: str | None, sim_log_s: float = 0.5):
    if kind == "sim":
        from haptic_hub.belt.sim import SimBelt

        return SimBelt(motors, sim_log_s)
    from haptic_hub.belt.ble import BleBelt

    return BleBelt(motors, address)


def _make_source(spec: str, cfg: HubConfig):
    if spec == "glasses":
        from haptic_hub.sources.glasses import GlassesServer

        return GlassesServer(cfg.ws_host, cfg.ws_port)
    if spec.startswith("replay:"):
        from haptic_hub.sources.replay import ReplaySource

        try:
            return ReplaySource(spec.partition(":")[2])
        except (OSError, ValueError) as exc:
            raise SystemExit(f"can't replay: {exc}") from exc
    from haptic_hub.sources.camera import OpenCVSource

    kind, _, target = spec.partition(":")
    if kind == "webcam":
        return OpenCVSource.webcam(int(target or 0))
    if kind == "video" and target:
        return OpenCVSource.video(target)
    if kind == "stream" and target:
        return OpenCVSource.stream(target)
    raise SystemExit(f"unknown source {spec!r}: use glasses, webcam[:N], video:PATH or stream:URL")


async def _run(args: argparse.Namespace) -> None:
    from haptic_hub.app import Hub
    from haptic_hub.perception import build_perception

    cfg = HubConfig()
    cfg.ws_port = args.port
    if args.outdoor:
        cfg.perception.depth_model = cfg.perception.depth_model.replace("Indoor", "Outdoor")
    if args.device:
        cfg.perception.device = args.device
    cfg.perception.detector = args.detector
    if args.prompts:
        if args.detector != "openvocab":
            raise SystemExit("--prompts needs --detector openvocab")
        from haptic_hub.perception.openvocab import parse_prompts

        cfg.perception.openvocab_prompts = parse_prompts(args.prompts)
    cfg.perception.motion = args.motion
    from haptic_hub.cues import check_reminder_s

    try:
        cfg.haptics.pause_reminder_s = check_reminder_s(args.pause_reminder)
    except ValueError as exc:
        raise SystemExit(f"--pause-reminder: {exc}") from exc
    if args.motion == "flow" and args.perception not in ("vision", "detector"):
        raise SystemExit("--motion flow needs the detector's tracked boxes: use --perception vision or detector")

    source = _make_source(args.source, cfg)
    if args.source == "glasses":
        log.info("in the phone app, set the hub address to  ws://%s:%d", _local_ip(), cfg.ws_port)

    _apply_classifier_args(args, cfg)
    log.info("loading perception '%s' (first run downloads model weights)", args.perception)
    perception = await asyncio.to_thread(build_perception, args.perception, cfg.perception)
    if args.perception != "sweep":  # person/object labels need the detector's boxes
        perception = await asyncio.to_thread(_build_classifier, args.classifier, cfg, perception)
    belt = _make_belt(args.belt, cfg.perception.sectors, args.belt_address, args.sim_log_interval)

    viewer = None
    if args.show:
        from haptic_hub.viz import DebugView

        viewer = DebugView()

    status_sink = source if hasattr(source, "publish") else None
    control = _make_control(cfg, perception)
    dashboard = None
    if args.dashboard:
        from haptic_hub.dashboard import Dashboard

        phone = f"ws://{_local_ip()}:{cfg.ws_port}" if args.source == "glasses" else None
        dashboard = Dashboard(args.dashboard_port, phone_address=phone)
        log.info("dashboard: open %s", dashboard.url)
    source = _wrap_recording(args, source)
    await Hub(cfg, source, perception, belt, status_sink, viewer, control, dashboard).run()


def _build_classifier(choice: str, cfg: HubConfig, perception):
    """Wrap perception with person/object labels. "auto" (the default) uses
    Nemotron when its endpoint answers at startup and otherwise falls back to the
    local labels with a warning; "nim" refuses to start without it."""
    from haptic_hub.perception.classify import build_classified
    from haptic_hub.perception.nim import NimSetupError

    cfg.classifier.backend = "nim" if choice == "auto" else choice
    try:
        return build_classified(perception, cfg.classifier)
    except NimSetupError as exc:
        if choice != "auto":
            raise SystemExit(f"cloud classifier: {exc}") from exc
        log.warning(
            "Nemotron classifier unavailable, using local person/object labels: %s "
            "(--classifier local skips this check)",
            exc,
        )
        cfg.classifier.backend = "local"
        return build_classified(perception, cfg.classifier)


def _make_control(cfg: HubConfig, perception):
    """Runtime settings for the phone / dashboard (control.py). The control code is
    printed once, never logged; the cloud classifier can be switched on at runtime
    only if this hub was started with a cloud endpoint configured."""
    import os

    from haptic_hub.control import Control

    c = cfg.classifier
    configured = c.backend == "nim" or bool(
        c.base_url or os.environ.get("NIM_BASE_URL") or os.environ.get("NIM_API_KEY") or os.environ.get("NVIDIA_API_KEY")
    )
    factory = None
    if configured:
        from haptic_hub.perception.nim import NimLabeler

        def factory():
            return NimLabeler.from_config(c)

    control = Control(cfg, perception, factory)
    print(f"control code (pair the phone app / shown on the dashboard):  {control.code}", flush=True)
    return control


def _wrap_recording(args: argparse.Namespace, source):
    """--record: save every incoming frame (any source) without changing the source."""
    if not args.record:
        return source
    from haptic_hub.record import DEFAULT_DIR, RecordingSource, Recorder, RecordLimits

    limits = RecordLimits(max_minutes=args.record_minutes, max_frames=args.record_max_frames, every=args.record_every)
    return RecordingSource(source, Recorder(args.record_dir or DEFAULT_DIR, limits))


def _add_record_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("recording (off unless --record)")
    g.add_argument("--record", action="store_true", help="save incoming frames as JPEG + frames.jsonl in a numbered session folder")
    g.add_argument("--record-dir", help="where session folders go (default: hub/recordings, git-ignored)")
    g.add_argument("--record-minutes", type=float, default=10.0, help="stop recording after this many minutes (default 10)")
    g.add_argument("--record-max-frames", type=int, default=6000, help="stop recording after this many saved frames (default 6000)")
    g.add_argument("--record-every", type=int, default=1, help="save every k-th frame (default 1 = all)")


def _load_env_file(path: str) -> None:
    """KEY=VALUE lines (e.g. the Brev endpoint and key) into the environment.
    Variables already set win. Values are never logged."""
    import os
    from pathlib import Path

    for line in Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.removeprefix("export ").strip(), value.strip().strip("'\""))


DEFAULT_NIM_ENV = "~/.config/haptic-hub/nim.env"


def _apply_classifier_args(args: argparse.Namespace, cfg: HubConfig) -> None:
    from pathlib import Path

    if args.nim_env:
        _load_env_file(args.nim_env)
    elif Path(DEFAULT_NIM_ENV).expanduser().is_file():
        _load_env_file(DEFAULT_NIM_ENV)
    cfg.classifier.backend = "nim" if args.classifier == "auto" else args.classifier
    if args.nim_url:
        cfg.classifier.base_url = args.nim_url
    if args.nim_model:
        cfg.classifier.model = args.nim_model


async def _nim_bench(args: argparse.Namespace) -> None:
    """Sends the same frame N times, one at a time, and prints each round trip."""
    import statistics
    import time

    import cv2

    from haptic_hub.perception.models import ObjectTracker
    from haptic_hub.perception.nim import CloudError, NimLabeler, NimSetupError, build_request, parse_labels, render_marked_jpeg, reply_text

    cfg = HubConfig()
    args.classifier = "nim"
    _apply_classifier_args(args, cfg)
    image = cv2.imread(args.image)
    if image is None:
        raise SystemExit(f"can't read image {args.image!r}")
    pcfg = cfg.perception
    h0, w0 = image.shape[:2]
    small = cv2.resize(image, (pcfg.infer_width, max(1, round(h0 * pcfg.infer_width / w0))), interpolation=cv2.INTER_AREA)
    boxes = ObjectTracker(pcfg.yolo_model, args.device or pcfg.device, pcfg.detector_conf).track(small)
    boxes = sorted(boxes, key=lambda b: (b.x2 - b.x1) * (b.y2 - b.y1), reverse=True)[: cfg.classifier.max_boxes]
    if not boxes:
        raise SystemExit("the detector found nothing in that image; pick one with people or objects in it")
    try:
        labeler = NimLabeler.from_config(cfg.classifier)
    except NimSetupError as exc:
        raise SystemExit(f"cloud classifier: {exc}") from exc
    labeler.close()
    c = cfg.classifier
    jpeg = render_marked_jpeg(image, boxes, (small.shape[1], small.shape[0]), c.image_width, c.jpeg_quality)
    body = build_request(labeler.model, jpeg, len(boxes))
    print(f"endpoint {labeler.client.base_url}  model {labeler.model}  image {len(jpeg) / 1024:.0f} KiB  boxes {len(boxes)}")
    times, failures = [], 0
    for i in range(args.n):
        started = time.perf_counter()
        try:
            text = reply_text(labeler.client.chat(body))
        except CloudError as exc:
            failures += 1
            print(f"{i + 1:3d}  FAILED after {(time.perf_counter() - started) * 1000:.0f} ms: {exc}")
            continue
        ms = (time.perf_counter() - started) * 1000
        times.append(ms)
        found = parse_labels(text, len(boxes))
        print(f"{i + 1:3d}  {ms:7.0f} ms  {text.strip()[:100]!r}")
        if i == 0:
            for n, box in enumerate(boxes, start=1):
                print(f"     box {n}: detector={box.label:<12} cloud={found.get(n, '?')}")
    if times:
        times.sort()
        p90 = times[min(len(times) - 1, round(0.9 * (len(times) - 1)))]
        print(
            f"round trip over {len(times)} ok / {failures} failed: min {times[0]:.0f}  median {statistics.median(times):.0f}"
            f"  p90 {p90:.0f}  max {times[-1]:.0f} ms (first includes connection setup)"
        )


async def _belt_test(args: argparse.Namespace) -> None:
    belt = _make_belt("ble", args.motors, args.address)
    runner = asyncio.create_task(belt.run())
    try:
        for _ in range(300):
            if belt.state == "connected":
                break
            await asyncio.sleep(0.1)
        else:
            raise SystemExit("belt not found; is it powered and advertising as HapticBelt?")

        async def hold(cmd: BeltCommand, seconds: float) -> None:
            for _ in range(int(seconds * 15)):
                await belt.send(cmd)
                await asyncio.sleep(1 / 15)

        for i in range(args.motors):
            print(f"motor {i} ({'left' if i == 0 else 'right' if i == args.motors - 1 else 'middle'})")
            levels = tuple(args.level if j == i else 0 for j in range(args.motors))
            await hold(BeltCommand(BeltMode.LEVELS, levels), 0.6)
            await hold(BeltCommand.off(args.motors), 0.3)
        print("fault pattern (what the wearer feels when the system can't vouch for itself)")
        await hold(BeltCommand.fault(args.motors), 4.2)
        print("off")
        await hold(BeltCommand.off(args.motors), 0.3)
    finally:
        await belt.close()
        runner.cancel()


async def _scan(_: argparse.Namespace) -> None:
    from bleak import BleakScanner

    from haptic_hub.belt.ble import is_belt

    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    belts = [(d, adv) for d, adv in found.values() if is_belt(d, adv)]
    if not belts:
        print(f"no belt found ({len(found)} BLE devices seen)")
    for device, adv in belts:
        print(f"{device.address}  {adv.local_name or device.name}  rssi={adv.rssi}")


def _add_classifier_args(p: argparse.ArgumentParser, backend: bool = True) -> None:
    if backend:
        p.add_argument(
            "--classifier",
            default="auto",
            choices=["auto", "local", "nim"],
            help="person vs. object labels: auto = Nemotron if its endpoint answers at startup, else local (default); "
            "nim = Nemotron or refuse to start; local = detector class only",
        )
    p.add_argument("--nim-url", help="OpenAI-compatible base URL (default: $NIM_BASE_URL or NVIDIA's hosted API)")
    p.add_argument("--nim-model", help="model name (default: $NIM_MODEL, else the hosted default / the server's first model)")
    p.add_argument("--nim-env", help=f"file of KEY=VALUE lines to load first (default: {DEFAULT_NIM_ENV} if it exists)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="haptic-hub", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the hub")
    run.add_argument(
        "--source",
        default="glasses",
        help="glasses (default), webcam[:N], video:PATH, stream:URL (rtsp://, http:// MJPEG, ...), or replay:DIR (a --record session)",
    )
    run.add_argument("--perception", default="vision", choices=["vision", "depth", "detector", "sweep"])
    run.add_argument(
        "--motion",
        default="off",
        choices=["off", "flow"],
        help="flow: also raise risk from optical-flow approach/crossing of tracked boxes (experimental)",
    )
    run.add_argument("--belt", default="ble", choices=["ble", "sim"])
    run.add_argument("--belt-address", help="skip scanning and connect to this BLE address/UUID")
    run.add_argument(
        "--sim-log-interval",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="--belt sim: log belt changes at most this often (default 0.5; 0 = every change, e.g. to see cues)",
    )
    run.add_argument(
        "--pause-reminder",
        type=float,
        default=15.0,
        metavar="SECONDS",
        help="while alerts are paused, a low pulse on the outer motors this often (default 15; 0 = off, else >= 2)",
    )
    run.add_argument("--port", type=int, default=8765, help="WebSocket port for the phone")
    run.add_argument("--device", help="torch device: mps, cuda or cpu (default: auto)")
    run.add_argument("--outdoor", action="store_true", help="use the outdoor metric depth model")
    run.add_argument("--show", action="store_true", help="open a debug window")
    run.add_argument(
        "--detector",
        default="yolo",
        choices=["yolo", "openvocab"],
        help="yolo = YOLO11n, 80 COCO classes (default); openvocab = YOLOE found by text prompt (poles, doors, stairs, ...)",
    )
    run.add_argument("--prompts", help='openvocab only: comma-separated prompts, e.g. "person,pole,door,stairs" (default: config.py)')
    run.add_argument("--dashboard", action="store_true", help="serve the operator dashboard on http://127.0.0.1:8780/")
    run.add_argument("--dashboard-port", type=int, default=8780, help="dashboard port (always bound to 127.0.0.1)")
    _add_classifier_args(run)
    _add_record_args(run)

    test = sub.add_parser("belt-test", help="buzz each motor in order over BLE")
    test.add_argument("--address")
    test.add_argument("--motors", type=int, default=5)
    test.add_argument("--level", type=int, default=200)

    sub.add_parser("scan", help="look for the belt over BLE")

    bench = sub.add_parser("nim-bench", help="measure cloud classifier round trips on one image")
    bench.add_argument("--image", required=True, help="a photo with people and/or objects in it")
    bench.add_argument("-n", type=int, default=10, help="number of sequential requests")
    bench.add_argument("--device", help="torch device for the detector")
    _add_classifier_args(bench, backend=False)

    from haptic_hub.bench.cli import add_bench_parser, bench_command

    add_bench_parser(sub)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:  # third-party libraries are chatty at INFO
        for noisy in ("websockets", "bleak", "httpx", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    from haptic_hub.app import UserQuit

    commands = {"run": _run, "belt-test": _belt_test, "scan": _scan, "nim-bench": _nim_bench, "bench": bench_command}
    try:
        code = asyncio.run(commands[args.command](args))
    except (KeyboardInterrupt, UserQuit):
        code = 130 if args.command == "bench" else None  # bench has already written its report
    if isinstance(code, int) and code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()

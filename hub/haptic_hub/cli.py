"""Command line entry point.

  haptic-hub run                         glasses -> vision -> BLE belt (the real thing)
  haptic-hub run --source webcam --belt sim --show
  haptic-hub run --source stream:rtsp://192.168.1.50:8554/live
  haptic-hub run --perception sweep      bench-test belt + phone without any model
  haptic-hub run --source webcam --belt sim --show --classifier nim   cloud person/object labels
  haptic-hub nim-bench --image photo.jpg measure cloud classifier round trips
  haptic-hub belt-test                   buzz each motor in order
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


def _make_belt(kind: str, motors: int, address: str | None):
    if kind == "sim":
        from haptic_hub.belt.sim import SimBelt

        return SimBelt(motors)
    from haptic_hub.belt.ble import BleBelt

    return BleBelt(motors, address)


def _make_source(spec: str, cfg: HubConfig):
    if spec == "glasses":
        from haptic_hub.sources.glasses import GlassesServer

        return GlassesServer(cfg.ws_host, cfg.ws_port)
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
    if args.motion == "flow" and args.perception not in ("vision", "detector"):
        raise SystemExit("--motion flow needs the detector's tracked boxes: use --perception vision or detector")

    source = _make_source(args.source, cfg)
    if args.source == "glasses":
        log.info("in the phone app, set the hub address to  ws://%s:%d", _local_ip(), cfg.ws_port)

    _apply_classifier_args(args, cfg)
    log.info("loading perception '%s' (first run downloads model weights)", args.perception)
    perception = await asyncio.to_thread(build_perception, args.perception, cfg.perception)
    if args.perception != "sweep":  # person/object labels need the detector's boxes
        from haptic_hub.perception.classify import build_classified
        from haptic_hub.perception.nim import NimSetupError

        try:
            perception = await asyncio.to_thread(build_classified, perception, cfg.classifier)
        except NimSetupError as exc:
            raise SystemExit(f"cloud classifier: {exc}") from exc
    belt = _make_belt(args.belt, cfg.perception.sectors, args.belt_address)

    viewer = None
    if args.show:
        from haptic_hub.viz import DebugView

        viewer = DebugView()

    status_sink = source if hasattr(source, "publish") else None
    await Hub(cfg, source, perception, belt, status_sink, viewer).run()


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


def _apply_classifier_args(args: argparse.Namespace, cfg: HubConfig) -> None:
    if args.nim_env:
        _load_env_file(args.nim_env)
    cfg.classifier.backend = args.classifier
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
            default="local",
            choices=["local", "nim"],
            help="person vs. object labels: local = detector class (default), nim = Nemotron vision model in the cloud",
        )
    p.add_argument("--nim-url", help="OpenAI-compatible base URL (default: $NIM_BASE_URL or NVIDIA's hosted API)")
    p.add_argument("--nim-model", help="model name (default: $NIM_MODEL, else the hosted default / the server's first model)")
    p.add_argument("--nim-env", help="file of KEY=VALUE lines to load first, e.g. ~/.config/haptic-hub/nim.env")


def main() -> None:
    parser = argparse.ArgumentParser(prog="haptic-hub", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the hub")
    run.add_argument(
        "--source",
        default="glasses",
        help="glasses (default), webcam[:N], video:PATH, or stream:URL (rtsp://, http:// MJPEG, ...)",
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
    _add_classifier_args(run)

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

    commands = {"run": _run, "belt-test": _belt_test, "scan": _scan, "nim-bench": _nim_bench}
    try:
        asyncio.run(commands[args.command](args))
    except (KeyboardInterrupt, UserQuit):
        pass


if __name__ == "__main__":
    main()

"""Command line entry point.

  haptic-hub run                         glasses -> vision -> BLE belt (the real thing)
  haptic-hub run --source webcam --belt sim --show
  haptic-hub run --source stream:rtsp://192.168.1.50:8554/live
  haptic-hub run --perception sweep      bench-test belt + phone without any model
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

    source = _make_source(args.source, cfg)
    if args.source == "glasses":
        log.info("in the phone app, set the hub address to  ws://%s:%d", _local_ip(), cfg.ws_port)

    log.info("loading perception '%s' (first run downloads model weights)", args.perception)
    perception = await asyncio.to_thread(build_perception, args.perception, cfg.perception)
    belt = _make_belt(args.belt, cfg.perception.sectors, args.belt_address)

    viewer = None
    if args.show:
        from haptic_hub.viz import DebugView

        viewer = DebugView()

    status_sink = source if hasattr(source, "publish") else None
    await Hub(cfg, source, perception, belt, status_sink, viewer).run()


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
    run.add_argument("--belt", default="ble", choices=["ble", "sim"])
    run.add_argument("--belt-address", help="skip scanning and connect to this BLE address/UUID")
    run.add_argument("--port", type=int, default=8765, help="WebSocket port for the phone")
    run.add_argument("--device", help="torch device: mps, cuda or cpu (default: auto)")
    run.add_argument("--outdoor", action="store_true", help="use the outdoor metric depth model")
    run.add_argument("--show", action="store_true", help="open a debug window")

    test = sub.add_parser("belt-test", help="buzz each motor in order over BLE")
    test.add_argument("--address")
    test.add_argument("--motors", type=int, default=5)
    test.add_argument("--level", type=int, default=200)

    sub.add_parser("scan", help="look for the belt over BLE")

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

    commands = {"run": _run, "belt-test": _belt_test, "scan": _scan}
    try:
        asyncio.run(commands[args.command](args))
    except (KeyboardInterrupt, UserQuit):
        pass


if __name__ == "__main__":
    main()

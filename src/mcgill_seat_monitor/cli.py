from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, load_config
from .models import CourseTarget, Observation, SeatState
from .monitor import Monitor
from .notifier import NotificationError, SmtpNotifier
from .state import StateStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor McGill VSB course-seat availability")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--once", action="store_true", help="poll once, then exit")
    parser.add_argument("--check-config", action="store_true", help="validate configuration, then exit")
    parser.add_argument("--test-notification", action="store_true", help="send one test email, then exit")
    parser.add_argument(
        "--test-weekly-report",
        action="store_true",
        help="send a weekly working-report test without resetting its counter",
    )
    parser.add_argument(
        "--simulate",
        metavar="STATES",
        help="offline transition demo, e.g. FULL,AVAILABLE,ERROR",
    )
    return parser


def _simulate(value: str) -> int:
    target = CourseTarget("DEMO", "COMP", "000", "001", "Lec", "0000")
    with Path("/tmp/mcgill-seat-monitor-simulation.json").open("w", encoding="utf-8") as handle:
        handle.write("{}\n")
    store = StateStore(Path("/tmp/mcgill-seat-monitor-simulation.json"))
    for raw_state in value.split(","):
        try:
            state = SeatState(raw_state.strip().upper())
        except ValueError:
            print(f"invalid simulated state: {raw_state}", file=sys.stderr)
            return 2
        observation = Observation(
            target=target,
            state=state,
            open_seats=1 if state == SeatState.AVAILABLE else 0 if state == SeatState.FULL else None,
            status="A" if state in {SeatState.AVAILABLE, SeatState.FULL} else None,
            crn="0000",
            observed_at=datetime.now(timezone.utc),
            detail="simulated",
        )
        transition = store.transition(observation)
        print(
            f"{transition.previous.value if transition.previous else 'UNSEEN'} -> {state.value}; "
            f"notify={'yes' if transition.notify else 'no'}"
        )
        store.save(observation)
    return 0


def main() -> int:
    args = _parser().parse_args()
    if args.simulate:
        return _simulate(args.simulate)

    try:
        config = load_config(args.config)
    except (ConfigError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.check_config:
        print(f"configuration valid: {len(config.courses)} target(s)")
        return 0
    if args.test_notification:
        target = config.courses[0]
        observation = Observation(
            target=target,
            state=SeatState.AVAILABLE,
            open_seats=1,
            status="A",
            crn=target.crn or "TEST",
            observed_at=datetime.now(timezone.utc),
            detail="test notification",
        )
        try:
            SmtpNotifier(config.notification).send(observation)
        except NotificationError as exc:
            print(f"notification error: {exc}", file=sys.stderr)
            return 1
        print("test notification sent")
        return 0
    if args.test_weekly_report:
        checks = StateStore(config.state_file).checks_since_report
        try:
            SmtpNotifier(config.notification).send_weekly_report(checks)
        except NotificationError as exc:
            print(f"notification error: {exc}", file=sys.stderr)
            return 1
        print(f"weekly report test sent with {checks} successful check(s)")
        return 0

    stop_event = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        logging.getLogger(__name__).info("received signal %s; stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    monitor = Monitor(config, stop_event)
    if args.once:
        return 0 if monitor.poll_once() else 1
    monitor.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

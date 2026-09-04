from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mcgill_seat_monitor.models import CourseTarget, Observation, SeatState
from mcgill_seat_monitor.state import StateStore


TARGET = CourseTarget("202609", "COMP", "250", "001", "Lec", "2318")


def observation(state: SeatState) -> Observation:
    return Observation(
        target=TARGET,
        state=state,
        open_seats=1 if state == SeatState.AVAILABLE else 0,
        status="A",
        crn="2318",
        observed_at=datetime.now(timezone.utc),
    )


class StateTests(unittest.TestCase):
    def test_full_to_available_notifies_once_and_survives_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path)
            full = observation(SeatState.FULL)
            self.assertFalse(store.transition(full).notify)
            store.save(full)

            available = observation(SeatState.AVAILABLE)
            self.assertTrue(store.transition(available).notify)
            store.save(available)

            restarted = StateStore(path)
            self.assertFalse(restarted.transition(available).notify)

    def test_error_does_not_replace_last_good_state(self) -> None:
        with TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            full = observation(SeatState.FULL)
            store.save(full)
            store.save(observation(SeatState.ERROR))
            self.assertEqual(store.previous(TARGET.target_id), SeatState.FULL)

    def test_unsaved_notification_can_be_retried(self) -> None:
        with TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            full = observation(SeatState.FULL)
            store.save(full)
            available = observation(SeatState.AVAILABLE)
            self.assertTrue(store.transition(available).notify)
            self.assertTrue(store.transition(available).notify)

    def test_weekly_report_is_due_sunday_at_eight_montreal_time(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path)
            friday = datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc)
            store.record_successful_check(friday)
            before = datetime(2026, 9, 6, 23, 59, tzinfo=timezone.utc)
            due = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)
            self.assertFalse(store.weekly_report_due(before, "America/Toronto", 6, 20, 0))
            self.assertTrue(store.weekly_report_due(due, "America/Toronto", 6, 20, 0))

    def test_weekly_check_count_survives_restart_and_resets_after_report(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            checked_at = datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc)
            store = StateStore(path)
            self.assertEqual(store.record_successful_check(checked_at), 1)
            self.assertEqual(store.record_successful_check(checked_at), 2)
            restarted = StateStore(path)
            self.assertEqual(restarted.checks_since_report, 2)
            restarted.mark_weekly_report_sent(checked_at)
            self.assertEqual(StateStore(path).checks_since_report, 0)


if __name__ == "__main__":
    unittest.main()

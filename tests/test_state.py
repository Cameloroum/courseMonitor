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


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import unittest

from mcgill_seat_monitor.models import CourseTarget, SeatState
from mcgill_seat_monitor.parser import ResponseError, parse_class_data


FIXTURES = Path(__file__).parent / "fixtures"
TARGET = CourseTarget("202609", "COMP", "250", "001", "Lec")


class ParserTests(unittest.TestCase):
    def test_available_fixture(self) -> None:
        result = parse_class_data(
            (FIXTURES / "available.xml").read_text(encoding="utf-8"), (TARGET,)
        )[TARGET.target_id]
        self.assertEqual(result.state, SeatState.AVAILABLE)
        self.assertEqual(result.open_seats, 17)
        self.assertEqual(result.crn, "2318")

    def test_full_fixture(self) -> None:
        result = parse_class_data(
            (FIXTURES / "full.xml").read_text(encoding="utf-8"), (TARGET,)
        )[TARGET.target_id]
        self.assertEqual(result.state, SeatState.FULL)
        self.assertEqual(result.open_seats, 0)

    def test_malformed_xml_is_an_error(self) -> None:
        with self.assertRaisesRegex(ResponseError, "malformed XML"):
            parse_class_data("<not-finished>", (TARGET,))

    def test_html_rejection_page_is_an_error(self) -> None:
        with self.assertRaisesRegex(ResponseError, "HTML rejection"):
            parse_class_data("<html><body>Request Rejected<br></body></html>", (TARGET,))

    def test_server_error_is_an_error(self) -> None:
        with self.assertRaisesRegex(ResponseError, "correct your device"):
            parse_class_data(
                "<addcourse><errors><error>Please correct your device</error></errors></addcourse>",
                (TARGET,),
            )

    def test_missing_section_is_an_error(self) -> None:
        missing = CourseTarget("202609", "COMP", "250", "999", "Lec")
        with self.assertRaisesRegex(ResponseError, "no VSB class matched"):
            parse_class_data(
                (FIXTURES / "available.xml").read_text(encoding="utf-8"), (missing,)
            )


if __name__ == "__main__":
    unittest.main()

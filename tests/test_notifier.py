import unittest
from unittest.mock import patch

from mcgill_seat_monitor.config import SmtpConfig
from mcgill_seat_monitor.notifier import SmtpNotifier


SMTP = SmtpConfig(
    host="smtp.example.com",
    port=465,
    security="ssl",
    username_env="USERNAME",
    password_env="PASSWORD",
    from_env="FROM",
    to_env="TO",
)


class NotifierTests(unittest.TestCase):
    def test_weekly_report_is_short_and_includes_check_count(self) -> None:
        notifier = SmtpNotifier(SMTP)
        with patch.object(notifier, "_send") as send:
            notifier.send_weekly_report(576)
        message = send.call_args.args[0]
        self.assertEqual(message["Subject"], "McGill seat monitor weekly report")
        self.assertEqual(
            message.get_content().strip(),
            "The McGill seat monitor is working.\n\n"
            "Successful checks since the previous weekly report: 576.",
        )


if __name__ == "__main__":
    unittest.main()

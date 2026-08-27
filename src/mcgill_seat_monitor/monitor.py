from __future__ import annotations

import logging
import random
import threading

from .config import AppConfig
from .notifier import NotificationError, SmtpNotifier
from .provider import ProviderError, VsbProvider
from .state import StateStore


class Monitor:
    def __init__(self, config: AppConfig, stop_event: threading.Event) -> None:
        self.config = config
        self.stop_event = stop_event
        self.log = logging.getLogger(__name__)
        self.provider = VsbProvider(config.request_timeout_seconds, config.request_retries)
        self.notifier = SmtpNotifier(config.notification)
        self.store = StateStore(config.state_file)
        self.failures = 0
        self.successful_cycles = 0

    def poll_once(self) -> bool:
        try:
            observations = self.provider.fetch(self.config.courses)
        except ProviderError as exc:
            self.failures += 1
            self.log.error("poll failed (%d consecutive): %s", self.failures, exc)
            return False

        self.failures = 0
        self.successful_cycles += 1
        for observation in observations.values():
            transition = self.store.transition(observation)
            if transition.previous != transition.current:
                self.log.info(
                    "%s changed %s -> %s (open_seats=%s, crn=%s)",
                    observation.target.display_name,
                    transition.previous.value if transition.previous else "UNSEEN",
                    transition.current.value,
                    observation.open_seats,
                    observation.crn,
                )
            else:
                self.log.debug("%s unchanged: %s", observation.target.display_name, observation.state.value)

            if transition.notify:
                try:
                    self.notifier.send(observation)
                    self.log.info("availability notification sent for %s", observation.target.display_name)
                except NotificationError as exc:
                    self.log.error("notification failed for %s: %s", observation.target.display_name, exc)
                    # Do not commit AVAILABLE yet: the next poll will retry delivery.
                    continue
            self.store.save(observation)

        if self.successful_cycles % 12 == 0:
            self.log.info("health: %d successful poll cycles; monitoring %d target(s)", self.successful_cycles, len(self.config.courses))
        return True

    def run(self) -> None:
        self.log.info("monitor started for %d target(s)", len(self.config.courses))
        while not self.stop_event.is_set():
            success = self.poll_once()
            if success:
                delay = self.config.poll_interval_seconds
            else:
                delay = min(
                    self.config.max_backoff_seconds,
                    self.config.poll_interval_seconds * (2 ** min(self.failures - 1, 6)),
                )
                delay += random.randint(0, min(30, max(1, delay // 10)))
                self.log.warning("next poll in %d seconds (backoff)", delay)
            self.stop_event.wait(delay)
        self.log.info("monitor stopped cleanly")

"""Login throttling with three fixed, automatically expiring windows.

* ``ip``          failures from one client IP (any account)       - stops spraying
* ``account_ip``  failures for one account from one IP            - stops guessing
* ``account``     failures for one account from all IPs (higher)  - slows distributed guessing

An attacker on one IP can only throttle *their own* attempts against an
account (``account_ip``); throttling the account for everyone requires many
failures and still expires after the window, so there is no permanent lockout
to abuse. Counters exist for unknown emails too, so throttling does not reveal
whether an account exists. Successful logins clear the ``account_ip`` counter.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import LoginThrottle
from common.errors import TooManyRequestsError

logger = logging.getLogger("auth.throttle")


@dataclass(frozen=True)
class Rule:
    scope: str
    value: str
    max_failures: int
    window: timedelta

    @property
    def key_hash(self) -> str:
        return hashlib.sha256(f"{self.scope}\x00{self.value}".encode()).hexdigest()


class LoginThrottler:
    def __init__(self, session: Session, settings: Settings, clock: Callable[[], datetime]):
        self.session = session
        self.settings = settings
        self.clock = clock

    def rules(self, ip: str, email: str) -> list[Rule]:
        s, email = self.settings, email.strip().lower()
        account_window = timedelta(seconds=s.login_account_window_seconds)
        return [
            Rule("ip", ip, s.login_ip_max_failures, timedelta(seconds=s.login_ip_window_seconds)),
            Rule("account_ip", f"{email}|{ip}", s.login_account_ip_max_failures, account_window),
            Rule("account", email, s.login_account_max_failures, account_window),
        ]

    def check(self, ip: str, email: str) -> None:
        """Raise 429 if any window is exhausted. Called before verifying the password."""
        if not self.settings.login_throttle_enabled:
            return
        now, retry_after = self.clock(), 0.0
        for rule in self.rules(ip, email):
            row = self.session.get(LoginThrottle, rule.key_hash)
            if row is None or row.failures < rule.max_failures:
                continue
            remaining = (row.window_started_at + rule.window - now).total_seconds()
            if remaining > 0:
                retry_after = max(retry_after, remaining)
        if retry_after > 0:
            logger.warning("login throttled")
            raise TooManyRequestsError(
                "Too many failed login attempts. Try again later.",
                code="TOO_MANY_LOGIN_ATTEMPTS",
                headers={"Retry-After": str(math.ceil(retry_after))},
            )

    def record_failure(self, ip: str, email: str) -> None:
        if not self.settings.login_throttle_enabled:
            return
        now = self.clock()
        for rule in self.rules(ip, email):
            for _ in range(2):  # one retry if a concurrent request created the row
                row = self.session.get(LoginThrottle, rule.key_hash, with_for_update=True)
                if row is None:
                    self.session.add(
                        LoginThrottle(
                            key_hash=rule.key_hash,
                            scope=rule.scope,
                            failures=1,
                            window_started_at=now,
                        )
                    )
                elif row.window_started_at + rule.window <= now:
                    row.failures, row.window_started_at = 1, now  # window expired: start again
                else:
                    row.failures += 1
                try:
                    self.session.commit()
                    break
                except IntegrityError:
                    self.session.rollback()

    def record_success(self, ip: str, email: str) -> None:
        if not self.settings.login_throttle_enabled:
            return
        account_ip = self.rules(ip, email)[1]
        row = self.session.get(LoginThrottle, account_ip.key_hash)
        if row is not None:
            self.session.delete(row)
            self.session.commit()

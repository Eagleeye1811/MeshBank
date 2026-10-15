"""Workflow recovery: a background loop and a one-shot CLI.

python -m app.recovery        # one recovery pass, then exit
"""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI

from app.schemas import RecoveryReport
from app.workflow import TransferWorkflow

logger = logging.getLogger("transaction.recovery")


def run_once(app_state) -> RecoveryReport:
    with app_state.db.session() as session:
        return TransferWorkflow(
            session,
            accounts=app_state.account_client,
            fraud=app_state.fraud_client,
            notifications=app_state.notification_client,
            lease_seconds=app_state.settings.workflow_lease_seconds,
        ).recover()


class RecoveryLoop:
    def __init__(self, app: FastAPI, interval_seconds: float) -> None:
        self.app = app
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="recovery-loop", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def _loop(self) -> None:
        # Wait one interval first so dependencies have time to come up.
        while not self._stop.wait(self.interval):
            try:
                run_once(self.app.state)
            except Exception:
                logger.exception("recovery pass failed")


def main() -> int:
    from app.main import create_app

    app = create_app()
    report = run_once(app.state)
    print(
        f"Recovery pass: examined={report.examined} advanced={report.advanced} "
        f"notifications_sent={report.notifications_sent}"
    )
    app.state.db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

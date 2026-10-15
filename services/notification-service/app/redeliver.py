"""Retry simulated delivery for notifications that are not yet delivered.

python -m app.redeliver
"""

from app.config import Settings
from app.delivery import LogDeliveryAdapter
from app.models import Base
from app.service import NotificationService
from common.database import Database
from common.logging_setup import configure_logging


def main() -> int:
    settings = Settings()
    configure_logging(settings.service_name, settings.log_level)
    db = Database(settings.database_url)
    db.create_schema(Base)
    adapter = LogDeliveryAdapter(settings.notification_delivery_log, settings.failing_types())
    with db.session() as session:
        delivered = NotificationService(session, adapter).redeliver_pending()
    print(f"Redelivered {delivered} notification(s)")
    db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from common.configuration import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "notification-service"
    database_url: str = "sqlite:///./data/notifications.db"
    # Simulated delivery: notifications are appended to this local JSON-lines
    # file. Nothing is ever sent by email, SMS or push in Phase 1.
    notification_delivery_log: str = "./data/notification-deliveries.log"
    # Comma-separated notification types whose simulated delivery fails, or
    # "*" for all. Used to exercise failure handling.
    notification_simulated_failure_types: str = ""

    def failing_types(self) -> frozenset[str]:
        return frozenset(
            t.strip().upper()
            for t in self.notification_simulated_failure_types.split(",")
            if t.strip()
        )

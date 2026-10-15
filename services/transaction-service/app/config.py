from pydantic import Field

from common.configuration import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "transaction-service"
    database_url: str = "sqlite:///./data/transactions.db"
    account_service_url: str = "http://localhost:8002"
    fraud_service_url: str = "http://localhost:8005"
    notification_service_url: str = "http://localhost:8006"
    # Background recovery of in-flight transfers. 0 disables the loop (the
    # recovery CLI and the internal recovery endpoint still work).
    recovery_interval_seconds: float = Field(default=30.0, ge=0)
    # A request holds a lease on its transaction so recovery never runs it
    # concurrently. Must exceed the worst-case request duration.
    workflow_lease_seconds: int = Field(default=60, ge=5, le=3600)

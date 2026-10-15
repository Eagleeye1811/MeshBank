from decimal import Decimal

from pydantic import Field

from common.configuration import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "payment-service"
    database_url: str = "sqlite:///./data/payments.db"
    account_service_url: str = "http://localhost:8002"
    transaction_service_url: str = "http://localhost:8003"
    fraud_service_url: str = "http://localhost:8005"
    notification_service_url: str = "http://localhost:8006"
    # Deterministic simulated provider (no real gateway is ever contacted):
    # payee references starting with this prefix are declined...
    payment_provider_decline_prefix: str = "DECLINE"
    # ...as are amounts strictly above this provider limit.
    payment_provider_max_amount: Decimal = Field(default=Decimal("100000.00"), gt=0)
    recovery_interval_seconds: float = Field(default=30.0, ge=0)
    workflow_lease_seconds: int = Field(default=60, ge=5, le=3600)

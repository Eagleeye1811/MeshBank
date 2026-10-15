from typing import Self

from pydantic import model_validator

from common.configuration import AppEnv, BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "account-service"
    database_url: str = "sqlite:///./data/accounts.db"
    # Enables the development-only balance seeding endpoint and CLI.
    dev_seed_enabled: bool = False

    @model_validator(mode="after")
    def _seed_only_outside_production(self) -> Self:
        if self.dev_seed_enabled and self.app_env == AppEnv.PRODUCTION:
            raise ValueError("DEV_SEED_ENABLED must be false in production")
        return self

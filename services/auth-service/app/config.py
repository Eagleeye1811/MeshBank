from typing import Self

from pydantic import Field, PrivateAttr, model_validator

from common.client_ip import IPNetwork, parse_cidrs
from common.configuration import AppEnv, BaseServiceSettings
from common.keys import KeyConfigError, SigningKey, load_signing_key, same_key


class Settings(BaseServiceSettings):
    service_name: str = "auth-service"
    database_url: str = "sqlite:///./data/auth.db"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    # Argon2id cost parameters (defaults follow argon2-cffi's RFC 9106 profile).
    argon2_time_cost: int = Field(default=3, ge=1, le=10)
    argon2_memory_cost_kib: int = Field(default=65_536, ge=8, le=1_048_576)
    argon2_parallelism: int = Field(default=4, ge=1, le=16)

    # RS256 signing key (only the auth service has one). Its public half must
    # be present in JWT_PUBLIC_KEYS_DIR under the same key ID.
    jwt_private_key_file: str | None = None
    jwt_signing_key_id: str | None = None

    # Login throttling (fixed windows, automatically expiring; no permanent lockout).
    login_throttle_enabled: bool = True
    login_ip_max_failures: int = Field(default=20, ge=1)
    login_ip_window_seconds: int = Field(default=300, ge=1)
    login_account_ip_max_failures: int = Field(default=5, ge=1)
    login_account_max_failures: int = Field(default=50, ge=1)
    login_account_window_seconds: int = Field(default=900, ge=1)
    # Comma-separated CIDRs of reverse proxies whose X-Forwarded-For is trusted.
    trusted_proxy_cidrs: str = ""

    _signing_key: SigningKey | None = PrivateAttr(default=None)
    _trusted_proxies: list[IPNetwork] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _load_signing_key(self) -> Self:
        if not self.jwt_private_key_file or not self.jwt_signing_key_id:
            raise ValueError(
                "JWT_PRIVATE_KEY_FILE and JWT_SIGNING_KEY_ID are required "
                "(run 'make dev-keys' for local development)"
            )
        try:
            key = load_signing_key(
                self.jwt_private_key_file,
                self.jwt_signing_key_id,
                strict_permissions=self.app_env == AppEnv.PRODUCTION,
            )
        except KeyConfigError as exc:
            raise ValueError(str(exc)) from exc
        public = self.jwt_keyring().get(key.kid)
        if public is None or not same_key(key.private_key, public):
            raise ValueError(
                f"public key for signing key ID {key.kid!r} is missing from JWT_PUBLIC_KEYS_DIR "
                "or does not match the private key"
            )
        self._signing_key = key
        try:
            self._trusted_proxies = parse_cidrs(self.trusted_proxy_cidrs)
        except ValueError as exc:
            raise ValueError("TRUSTED_PROXY_CIDRS must be comma-separated CIDRs") from exc
        return self

    def signing_key(self) -> SigningKey:
        if self._signing_key is None:  # unreachable: loaded by the validator
            raise RuntimeError("signing key not loaded")
        return self._signing_key

    def trusted_proxies(self) -> list[IPNetwork]:
        return self._trusted_proxies

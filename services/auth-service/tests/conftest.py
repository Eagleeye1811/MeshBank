import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(database_url_factory) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=database_url_factory("auth"),
        access_token_ttl_seconds=600,
        # Cheap Argon2id parameters keep the suite fast; production uses defaults.
        argon2_time_cost=1,
        argon2_memory_cost_kib=1024,
        argon2_parallelism=1,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


PASSWORD = "CorrectHorse42battery"


@pytest.fixture
def register(client):
    def _register(email="asha@example.com", password=PASSWORD, full_name="Asha Rao"):
        return client.post(
            "/auth/register", json={"email": email, "password": password, "full_name": full_name}
        )

    return _register


@pytest.fixture
def token(client, register) -> str:
    assert register().status_code == 201
    response = client.post("/auth/login", json={"email": "asha@example.com", "password": PASSWORD})
    return response.json()["access_token"]

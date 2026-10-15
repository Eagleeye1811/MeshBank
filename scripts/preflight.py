"""Read-only preflight gate for MeshBank.

    python scripts/preflight.py                      # local: tests and `make run`
    python scripts/preflight.py --profile docker     # before `make up` / image builds
    python scripts/preflight.py --profile scan       # before Trivy (vulnerability DB download)
    python scripts/preflight.py --profile kind       # before creating the two kind clusters
    python scripts/preflight.py --database auth=postgresql://...   # + migration status

Nothing here deletes files, changes Docker state, or writes to a database. Every
failure says what to do; cleanup is always left to a person. Exit code 1 means
at least one check failed for the chosen profile.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3
SERVICES = ["auth", "account", "transaction", "payment", "fraud", "notification"]

# Free space needed on the volume holding the project and Docker's disk image.
# Builds need room for six images plus layers; kind needs two node images and
# two clusters' worth of pulled images; Trivy's DB alone is ~1 GiB.
MIN_FREE_GIB = {"local": 1, "docker": 10, "scan": 12, "kind": 20}
TOOLS = {
    "local": ["make"],
    "docker": ["make", "docker"],
    "scan": ["make", "docker", "trivy"],
    "kind": ["make", "docker", "kind", "kubectl"],
}
DOCKER_TIMEOUT_SECONDS = 10


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def ok(self, check: str, detail: str) -> None:
        print(f"  [ OK ] {check}: {detail}")

    def warn(self, check: str, detail: str) -> None:
        print(f"  [WARN] {check}: {detail}")

    def fail(self, check: str, detail: str, fix: str) -> None:
        self.failed += 1
        print(f"  [FAIL] {check}: {detail}\n         -> {fix}")


def check_disk(report: Report, profile: str) -> None:
    usage = shutil.disk_usage(ROOT)
    free = usage.free / GIB
    need = MIN_FREE_GIB[profile]
    detail = f"{free:.1f} GiB free of {usage.total / GIB:.0f} GiB"
    detail += f" (profile '{profile}' needs {need})"
    if free < need:
        report.fail(
            "disk space",
            detail,
            "free space manually (e.g. review ~/Downloads, caches, unused Docker images "
            "in Docker Desktop). This script never deletes anything.",
        )
    else:
        report.ok("disk space", detail)
    raw = Path.home() / "Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw"
    if raw.exists():
        allocated = raw.stat().st_blocks * 512 / GIB
        report.ok("Docker disk image", f"{allocated:.1f} GiB allocated on disk (not modified)")


def check_python(report: Report) -> None:
    version = sys.version_info
    if version < (3, 11):
        report.fail("python", f"{version.major}.{version.minor}", "use Python 3.11+ (make install)")
    else:
        report.ok("python", f"{version.major}.{version.minor}.{version.micro} ({sys.executable})")


def _tool_version(tool: str) -> str:
    args = {
        "docker": ["docker", "--version"],
        "kubectl": ["kubectl", "version", "--client", "-o", "yaml"],
        "kind": ["kind", "version"],
        "trivy": ["trivy", "--version"],
        "make": ["make", "--version"],
    }[tool]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "version unknown"
    lines = (out.stdout or out.stderr).strip().splitlines()
    if tool == "kubectl":
        lines = [line.strip() for line in lines if "gitVersion" in line]
    return lines[0] if lines else "version unknown"


def check_tools(report: Report, profile: str) -> None:
    for tool in ("make", "docker", "kubectl", "kind", "trivy"):
        required = tool in TOOLS[profile]
        if shutil.which(tool) is None:
            if required:
                report.fail(f"tool {tool}", "not installed", f"install it: brew install {tool}")
            else:
                report.warn(f"tool {tool}", "not installed (not needed for this profile)")
        else:
            report.ok(f"tool {tool}", _tool_version(tool))


def check_docker(report: Report, profile: str) -> None:
    required = profile != "local"
    if shutil.which("docker") is None:
        return  # already reported by check_tools
    try:
        out = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=DOCKER_TIMEOUT_SECONDS,
            check=False,
        )
        reachable, detail = out.returncode == 0, (out.stdout.strip() or out.stderr.strip())
    except subprocess.TimeoutExpired:
        reachable, detail = False, f"no response within {DOCKER_TIMEOUT_SECONDS}s"
    if reachable:
        report.ok("docker engine", f"server {detail}")
    elif required:
        report.fail(
            "docker engine",
            f"unreachable ({detail.splitlines()[0] if detail else 'no output'})",
            "start or restart Docker Desktop from its menu and wait for 'Engine running'. "
            "If the host disk is full, free space first. Do not reset or purge Docker data.",
        )
    else:
        report.warn("docker engine", "unreachable (not needed for local tests / make run)")


def check_keys(report: Report) -> None:
    kid = os.environ.get("JWT_SIGNING_KEY_ID", "dev-local-1")
    public_dir = Path(os.environ.get("JWT_PUBLIC_KEYS_DIR", ROOT / "secrets/jwt/public"))
    private = Path(os.environ.get("JWT_PRIVATE_KEY_FILE", ROOT / f"secrets/jwt/private/{kid}.pem"))
    public_keys = sorted(p.stem for p in public_dir.glob("*.pem")) if public_dir.is_dir() else []
    if not public_keys:
        report.fail("JWT public keys", f"none in {public_dir}", "run 'make dev-keys'")
    elif kid not in public_keys:
        report.fail(
            "JWT public keys",
            f"{public_keys} lacks signing kid {kid!r}",
            "copy the matching public key into the public key directory",
        )
    else:
        report.ok("JWT public keys", f"kids {public_keys} in {public_dir}")
    if not private.is_file():
        report.fail("JWT signing key", f"{private} missing", "run 'make dev-keys'")
        return
    mode = stat.S_IMODE(private.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        report.fail(
            "JWT signing key", f"{private.name} mode {mode:o}", f"run 'chmod 600 {private}'"
        )
    else:
        report.ok("JWT signing key", f"{private.name} mode {mode:o} (contents not read)")
    if os.environ.get("APP_ENV") == "production" and any(k.startswith("dev-") for k in public_keys):
        report.fail(
            "JWT key policy",
            "development keys present with APP_ENV=production",
            "provision production keys from your secret manager",
        )


def check_environment(report: Report) -> None:
    app_env = os.environ.get("APP_ENV", "development")
    report.ok("APP_ENV", app_env)
    if app_env == "production":
        for name in ("INTERNAL_API_TOKEN", "DATABASE_URL"):
            if not os.environ.get(name):
                report.fail(name, "not set", f"set {name} (production requires it)")
    for env_file in [ROOT / ".env", *(ROOT / "services").glob("*-service/.env")]:
        if env_file.exists() and "JWT_SECRET" in env_file.read_text():
            report.warn(
                str(env_file.relative_to(ROOT)),
                "contains JWT_SECRET, which RS256 no longer uses",
            )


def check_databases(report: Report, databases: list[str]) -> None:
    if not databases:
        return
    sys.path.insert(0, str(ROOT / "shared"))
    from sqlalchemy import create_engine, pool, text

    from common.configuration import normalize_database_url
    from common.database import redact_database_url
    from common.migrations import current_revision, head_revision

    for item in databases:
        service, _, url = item.partition("=")
        if service not in SERVICES or not url:
            report.fail("database", f"bad --database {service!r}", "use SERVICE=URL")
            continue
        url = normalize_database_url(url)
        label = f"{service} db {redact_database_url(url)}"
        engine = create_engine(url, poolclass=pool.NullPool, connect_args=_connect_args(url))
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # report any connection failure
            report.fail(label, f"cannot connect ({type(exc).__name__})", "check the server/URL")
            continue
        finally:
            engine.dispose()
        current = current_revision(url)
        head = head_revision(ROOT / "services" / f"{service}-service")
        if current == head:
            report.ok(label, f"connected; migrations at head {head}")
        else:
            report.fail(
                label,
                f"schema revision {current!r}, release needs {head!r}",
                f"run migrations deliberately: cd services/{service}-service && "
                "DATABASE_URL=... ../../.venv/bin/alembic upgrade head",
            )


def _connect_args(url: str) -> dict:
    return {"connect_timeout": 5} if url.startswith("postgresql") else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", choices=sorted(MIN_FREE_GIB), default="local")
    parser.add_argument(
        "--database",
        action="append",
        default=[],
        metavar="SERVICE=URL",
        help="check connectivity and migration status (read-only); repeatable",
    )
    args = parser.parse_args(argv)
    report = Report()
    print(f"MeshBank preflight (profile: {args.profile}) - read-only")
    check_disk(report, args.profile)
    check_python(report)
    check_tools(report, args.profile)
    check_docker(report, args.profile)
    check_environment(report)
    check_keys(report)
    check_databases(report, args.database)
    if report.failed:
        print(f"\nPREFLIGHT FAILED: {report.failed} check(s). Nothing was changed.")
        return 1
    print("\nPreflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

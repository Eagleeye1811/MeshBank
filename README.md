# MeshBank

**MeshBank: A Secure Multi-Cloud Service Mesh Infrastructure for a Cloud-Native Banking Platform**
*Secure connections. Reliable banking. Across clouds.*

MeshBank is a **simulated** banking platform. The repository contains:

* **Banking microservices** (Phase 1): six independently runnable FastAPI
  services with real persistence, validation, authentication, an idempotent
  transfer saga, and automated tests.
* **Kubernetes deployment with a sidecar service mesh** (Phase 2, local `kind`
  cluster): every service runs behind an NGINX sidecar, every
  service-to-service call uses mutual TLS with per-service caller
  authorization, and `payment-service` has a weighted canary release
  ([section 16](#16-service-mesh-architecture--mtls-implementation),
  [section 17](#17-phase-2-traffic-management--canary-deployment)).
* A React dashboard (`frontend/`): a customer view (accounts, send money, pay
  a bill) and a “Behind the scenes” view (service status, security checks,
  canary release). See [`frontend/README.md`](frontend/README.md).

**Multi-cloud is simulated, not real.** `make k8s-multi-up` splits the services
across two local `kind` clusters named after AWS and GCP, joined by mTLS
east-west gateways ([section 18](#18-two-cluster-mesh-aws--gcp-simulation)).
They are Docker containers on one machine, not EKS or GKE.

> All money is simulated. Nothing connects to real banks, payment gateways,
> email/SMS providers, or production banking APIs.

---

## 1. Scope and what is *not* implemented

| Implemented | Not implemented (future phases) |
|---|---|
| Six FastAPI services, each with its own database | Real cloud deployment (EKS/GKE); cross-cloud networking over the internet |
| JWT auth (RS256 with key IDs, JWKS, rotation), login throttling, Argon2id password hashing | Custom (Go) sidecar proxies, iptables traffic interception |
| Ownership checks on every customer resource | Automatic certificate issuance/rotation (a control plane) |
| Decimal-safe money, INR only | Adaptive load balancing |
| Durable, idempotent transfer saga with compensation and recovery | Prometheus metrics, Grafana dashboards |
| Deterministic fraud rules | Rate limiting (`429`), refresh tokens, token revocation |
| Simulated payment provider and simulated notification delivery | |
| Structured JSON logs with request-ID propagation; Alembic migrations | |
| Docker images and Docker Compose | |
| Kubernetes manifests (kind) with PostgreSQL | |
| NGINX sidecar mesh: mTLS on every service-to-service call, per-service caller authorization, NetworkPolicies | |
| Weighted canary release of `payment-service` with header overrides | |
| Two-cluster mesh (kind clusters simulating AWS and GCP) with SNI-routed east-west gateways | |

Transport security depends on how you run it. With `make run` or Docker
Compose, internal traffic between services is **plain HTTP**, authenticated
only by a shared internal token. On Kubernetes (`make k8s-up`) every
service-to-service call goes through the sidecars over **mutual TLS**, and the
internal token remains as a second check. JWTs authenticate customers; they do
not provide transport security.

---

## 2. Architecture

```
                 customer (JWT)                       internal (X-Internal-Token, plain HTTP)
                       │
 ┌──────────────┐  ┌───┴──────────┐   GET/debit/credit/reverse   ┌──────────────────┐
 │ auth-service │  │ transaction- │ ────────────────────────────▶│ account-service  │
 │   :8001      │  │ service:8003 │ ──── /evaluate ──────┐       │   :8002          │
 └──────────────┘  └───▲───┬──────┘                      ▼       └──────────────────┘
                       │   └──── /notifications ──┐  ┌──────────────┐     ▲
        /internal/payment-debits                  │  │fraud-service │     │ GET account
        /internal/transactions/{id}/reversal      │  │   :8005      │     │
                       │                          ▼  └──────────────┘     │
                 ┌─────┴────────┐  /evaluate  ┌─────────────────────┐     │
                 │ payment-     │────────────▶│ notification-service│     │
                 │ service:8004 │────────────▶│   :8006             │     │
                 └──────────────┘ /notifications└───────────────────┘     │
                        └─────────────────────────────────────────────────┘
```

| Service | Port | Responsibility | Owns (database) |
|---|---|---|---|
| auth-service | 8001 | Registration, login, `/auth/me`, `/auth/verify`, JWT issuance | users, password hashes |
| account-service | 8002 | Accounts, balances, idempotent internal debit/credit/reversal | accounts, account operations |
| transaction-service | 8003 | Transfers, transaction history, transfer saga, recovery | transactions, transaction events, idempotency |
| payment-service | 8004 | Simulated bill and merchant payments, simulated provider | payments, idempotency |
| fraud-service | 8005 | Deterministic rule evaluation (`APPROVE`/`REVIEW`/`REJECT`) | evaluation decisions |
| notification-service | 8006 | Persisted notifications, simulated delivery, deduplication | notifications |

### Key design decisions

* **Each service is independent.** Each has its own `app` package,
  `requirements.txt`, Dockerfile and database. Services never import each
  other's code. They talk only over HTTP, using base URLs from configuration.
  `shared/common` holds only cross-cutting code: config, logging, errors, JWT
  verification, DB helpers, money, and the HTTP client.
* **Business logic lives outside route handlers.** It is in `service.py` and
  `workflow.py` modules, behind small client interfaces (`clients.py`). That
  lets tests swap in fakes. It also means a later sidecar or mTLS transport
  only changes the client layer.
* **Synchronous SQLAlchemy 2.x** is used everywhere. FastAPI runs sync
  endpoints in a thread pool.
* **Money is `Decimal`.** It is stored as an integer number of minor units
  (paise) through the `MoneyType` column type, so it is exact on SQLite and
  PostgreSQL alike. Amounts must be positive with at most 2 decimal places,
  and must not exceed `9,999,999,999,999.99`. JSON responses return amounts
  as strings, e.g. `"2500.50"`.
* **Customer identity always comes from the verified JWT** (`sub`). A customer
  ID in a URL is only compared against the token. Request bodies forbid
  unknown fields.
* **Internal routes are separate.** They live under `/internal/...`, plus
  `POST /evaluate` and `POST /notifications`, and require `X-Internal-Token`.
  A customer JWT gets `403 INTERNAL_ONLY` there. Customer auth and service
  identity are separate concerns. mTLS identities replace the shared token in
  a later phase.

### The transfer saga

The Account Service owns balances, so no single database transaction can span
both services. Instead, `transaction-service/app/workflow.py` drives each
transfer through persisted steps:

```
CREATED → FRAUD_CHECK → DEBIT_PENDING → CREDIT_PENDING → DONE (COMPLETED)
                          │                │
                          │                └→ REVERSAL_PENDING → DONE (FAILED, debit reversed)
                          └→ DONE (REJECTED: insufficient funds / inactive account)
```

1. The workflow commits each step **before** making the remote call.
2. Every account call uses a deterministic **operation ID** (`<tx>:debit`,
   `<tx>:credit`, `<tx>:reversal`). The Account Service applies an operation
   ID at most once. A replay returns the stored outcome, and reusing an ID
   with a different payload returns `409`. The balance update and the
   operation record commit in one local DB transaction, and the update is a
   conditional `UPDATE … WHERE balance >= amount`. Tests show concurrent
   debits cannot overdraw an account.
3. An **unknown outcome** (timeout, outage after the call may have been
   received) never produces `COMPLETED` or `FAILED`. The transfer stays
   `PROCESSING`, and recovery rolls it forward by re-sending the same
   idempotent call.
4. If the debit is *provably* not applied, the transfer ends `FAILED` (`503`).
   This covers a connection refused on the very first attempt.
5. If the credit is **definitively rejected** (for example, the destination
   was frozen meanwhile), a compensating, idempotent **reversal** returns the
   funds. The transfer then ends `FAILED`.
6. A **lease** column stops the recovery loop from running a transfer at the
   same time as an in-flight request.
7. `transaction_events` keeps an append-only audit trail of state transitions.

This gives at-most-once balance effects and eventual completion or
compensation. It is **not** distributed ACID. Between debit and credit, the
money is visible in neither account.

Payments use the same building blocks. The payment service runs fraud
evaluation, then asks the Transaction Service for an idempotent
**payment debit** keyed by payment ID. Next it calls the deterministic
simulated provider. If the provider declines, it asks the Transaction Service
to **reverse** the debit, so the customer is refunded. The payment service
never changes balances itself.

### Transaction and payment states

`PENDING`, `PROCESSING`, `COMPLETED`, `REJECTED` (business rule, no money
moved), `FAILED` (technical failure or compensated, no net money moved),
`UNDER_REVIEW` (held by fraud rules, no money moved; there is no review API in
Phase 1), and `REVERSED` (a completed payment debit that was refunded). The
allowed transitions are in `transaction-service/app/state.py`.

---

## 3. Repository structure

```
meshbank/
├── services/
│   ├── auth-service/            app/ tests/ Dockerfile requirements.txt .env.example pytest.ini
│   ├── account-service/         … + app/dev_seed.py (dev-only CLI)
│   ├── transaction-service/     … + app/workflow.py (saga), app/recovery.py, app/state.py
│   ├── payment-service/         … + app/workflow.py, app/provider.py (simulated provider)
│   ├── fraud-service/           … + app/rules.py
│   └── notification-service/    … + app/delivery.py (simulated), app/redeliver.py
├── shared/
│   ├── pyproject.toml           installable package "meshbank-common"
│   └── common/                  configuration, logging_setup, errors, security, database,
│                                money, http_client, api, service_app
├── tests/e2e/                   cross-service tests (spawns all six services)
├── scripts/                     run_local.sh, demo.py, reset_dev.sh
├── docker-compose.yml  Makefile  pyproject.toml (ruff)  constraints.txt  requirements-dev.txt
└── .env.example
```

Each service is built the same way: `config.py` (typed settings), `models.py`
(SQLAlchemy), `schemas.py` (Pydantic v2), business logic in `service.py` or
`workflow.py`, routes, and `main.py` with `create_app()`.

---

## 4. Prerequisites

* Python **3.11+**. The Makefile uses `python3.11`; override it with
  `make install PYTHON_BIN=python3.12`.
* GNU Make and Bash.
* Docker with the Compose v2 plugin. This is only needed for the container
  workflow.
* `curl` for the examples.

## 5. Local setup

```bash
make install          # creates .venv and installs pinned deps + shared package (editable)
make dev-keys         # once: RS256 JWT key pair in secrets/jwt (git-ignored, never overwritten)
make preflight        # read-only checks: disk, Docker, tools, keys (see §6)
make lint             # ruff check + format check
make test             # all per-service suites + cross-service e2e suite
```

## 6. Configuration

All settings are environment variables, read into typed `pydantic-settings`
classes and validated at startup. When running a service locally, an optional
`.env` file in its directory is also read. Copy `services/<name>/.env.example`
to create one. Docker Compose reads the root `.env`; copy `.env.example`.
**Never commit `.env` files**, which `.gitignore` already excludes.

| Variable | Used by | Default | Notes |
|---|---|---|---|
| `APP_ENV` | all | `development` | `development`, `test`, `production` |
| `LOG_LEVEL` | all | `INFO` | |
| `DATABASE_URL` | all | `sqlite:///./data/<svc>.db` | Any SQLAlchemy URL; PostgreSQL-compatible models |
| `JWT_PUBLIC_KEYS_DIR` | all | none (`make run`: `secrets/jwt/public`) | **Required.** Directory of `<kid>.pem` RSA public keys (≥2048 bits) |
| `JWT_PRIVATE_KEY_FILE` / `JWT_SIGNING_KEY_ID` | auth | none / `dev-local-1` in `make run` and Compose | **Required.** Signing key; its public half must be in `JWT_PUBLIC_KEYS_DIR` |
| `LOGIN_*_MAX_FAILURES` / `LOGIN_*_WINDOW_SECONDS` | auth | IP 20/300 s, account+IP 5/900 s, account 50/900 s | Fixed windows that expire; no permanent lockout |
| `TRUSTED_PROXY_CIDRS` | auth | empty | Proxies whose `X-Forwarded-For` is trusted; empty means the TCP peer is the client |
| `DB_MIGRATION_MODE` | all | `upgrade` (Compose: `create_all`) | `check` in production; `create_all` is rejected there |
| `JWT_ISSUER` / `JWT_AUDIENCE` | all | `meshbank-auth` / `meshbank-api` | Verified on every request |
| `ACCESS_TOKEN_TTL_SECONDS` | auth | `900` | |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_COST_KIB` / `ARGON2_PARALLELISM` | auth | `3` / `65536` / `4` | |
| `INTERNAL_API_TOKEN` | all | dev-only value | **Required (≥32 chars, random) in production** |
| `HTTP_TIMEOUT_SECONDS` / `HTTP_MAX_ATTEMPTS` | callers | `3` / `2` | Retries only for idempotent calls |
| `ACCOUNT_/TRANSACTION_/FRAUD_/NOTIFICATION_SERVICE_URL` | callers | `http://localhost:800x` | Compose uses service DNS names |
| `DEV_SEED_ENABLED` | account | `false` (Compose: `true`) | Rejected at startup in production |
| `FRAUD_REVIEW_THRESHOLD` / `FRAUD_HARD_LIMIT` / `FRAUD_HARD_LIMIT_ACTION` / `FRAUD_BLOCKED_PAYEES` | fraud | `50000.00` / `200000.00` / `REJECT` / empty | |
| `PAYMENT_PROVIDER_DECLINE_PREFIX` / `PAYMENT_PROVIDER_MAX_AMOUNT` | payment | `DECLINE` / `100000.00` | Simulated provider |
| `RECOVERY_INTERVAL_SECONDS` / `WORKFLOW_LEASE_SECONDS` | transaction, payment | `30` / `60` | `0` disables the loop |
| `NOTIFICATION_DELIVERY_LOG` / `NOTIFICATION_SIMULATED_FAILURE_TYPES` | notification | `./data/notification-deliveries.log` / empty | Types, comma-separated, or `*` |

**Secret policy:** in `development` or `test`, empty secrets fall back to
built-in values clearly labelled `dev-only-insecure-…`, and each service logs
a warning at startup. With `APP_ENV=production`, every service **refuses to
start** if `INTERNAL_API_TOKEN` is missing, shorter than 32
characters, or looks like a placeholder. It also refuses to start if
`DEV_SEED_ENABLED=true`. Generate secrets with
`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. All services
must share the same `INTERNAL_API_TOKEN`.

**JWT keys (RS256):** only the auth service holds the private key; every
service verifies tokens with the public keys in `JWT_PUBLIC_KEYS_DIR`, selected
by the token's `kid`. The algorithm is fixed server-side (no `none`, no HS256
key confusion) and unknown key IDs are rejected. `GET /.well-known/jwks.json`
on the auth service publishes the public keys only. Production refuses
development keys (`kid` starting with `dev-`) and a world-readable private key.
To rotate: add the new public key everywhere, switch `JWT_SIGNING_KEY_ID`, and
remove the old public key once tokens signed with it have expired.

**Preflight:** `make preflight` (or `PREFLIGHT_ARGS="--profile docker|scan|kind"`,
`--database SERVICE=URL` for migration status) checks disk space, Docker
Engine, tools, and keys. It is read-only and never cleans anything up.

## 7. Running services individually (no Docker)

```bash
make run                                   # all six on 127.0.0.1:8001-8006, Ctrl-C stops all
make run-service SERVICE=fraud PORT=8005   # just one service
```

Data is stored under `services/<name>/data/`. Interactive OpenAPI docs are at
`http://localhost:<port>/docs` for every service.

## 8. Docker Compose

```bash
make dev-keys             # once; Compose mounts secrets/jwt read-only
cp .env.example .env      # optional
make preflight PREFLIGHT_ARGS="--profile docker"
make up                   # docker compose up --build -d, then shows status
make ps                   # health status of the six containers
make logs                 # follow JSON logs
make down                 # stop (data volumes are kept)
make up                   # restart; data persists in named volumes
```

The containers run as uid `10001` with a read-only root filesystem,
`no-new-privileges`, and all capabilities dropped. They share the `meshbank`
bridge network and reach each other by service name. Each service has its own
named volume (`auth-data`, `account-data`, …), mounted at `/data`. Health
checks call `/ready`. Startup order is only a convenience: each service
tolerates its dependencies being down (requests get `503`, and the recovery
loop retries).

## 9. Tests

```bash
make test-services   # per-service unit + integration tests (isolated temp SQLite DBs, fakes for HTTP deps)
make test-e2e        # spawns all six services on free ports with temp DBs and tests over real HTTP
make test            # both
```

The tests cover:

* **Auth:** password hashing, JWT issue/verify (expired, tampered, wrong key,
  issuer or audience), duplicates, and generic login errors.
* **Accounts:** creation, ownership, idempotent operations, insufficient funds,
  reversal-once, concurrent debits (10 threads cannot overdraw), and
  concurrent duplicate operation IDs.
* **Fraud:** every rule, threshold boundaries, malformed input, and stable
  repeat evaluations, even after thresholds change.
* **Transfers:** success, rejections, idempotency, fraud outage, account
  outage, lost debit responses, failure between debit and credit, the
  compensating reversal, a reversal outage, restart recovery, lease
  exclusion, the state machine, and HTTP client timeout and retry semantics.
* **Payments:** success, invalid amounts, insufficient funds, fraud
  reject/review, duplicates, provider decline with refund, refund outage,
  unknown debit outcome, and ownership.
* **Notifications:** creation, deduplication, simulated delivery failure and
  redelivery, invalid payloads, and ownership.
* **E2E:** workflows A–E across real processes. This includes stopping and
  restarting services to test outages and data persistence.

## 10. API examples (curl)

Error responses always have this shape (validation errors also include `details`):

```json
{"error": {"code": "INSUFFICIENT_FUNDS", "message": "…", "request_id": "5a84…", "details": {…}}}
```

Every response carries an `X-Request-ID` header. Send your own value and it is
propagated to downstream services.

```bash
# Workflow A — register, log in, identify
curl -s -X POST localhost:8001/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com","password":"CorrectHorse42battery","full_name":"Alice"}'
# 201 {"id":"6b65…","email":"alice@example.com","full_name":"Alice","created_at":"…"}

TOKEN=$(curl -s -X POST localhost:8001/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com","password":"CorrectHorse42battery"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
# 200 {"access_token":"eyJ…","token_type":"bearer","expires_in":900,"expires_at":"…"}

curl -s localhost:8001/auth/me -H "Authorization: Bearer $TOKEN"
CUSTOMER_ID=$(curl -s localhost:8001/auth/me -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# Workflow B — accounts
curl -s -X POST localhost:8002/accounts -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"account_type":"SAVINGS"}'
# 201 {"id":"8b18…","account_number":"048213907761","customer_id":"6b65…","account_type":"SAVINGS",
#      "currency":"INR","status":"ACTIVE","balance":"0.00","created_at":"…"}
curl -s localhost:8002/accounts/$ACCOUNT_ID/balance -H "Authorization: Bearer $TOKEN"
# 200 {"account_id":"8b18…","balance":"25000.00","currency":"INR","as_of":"…"}
curl -s "localhost:8002/customers/$CUSTOMER_ID/accounts?limit=20&offset=0" -H "Authorization: Bearer $TOKEN"

# Workflow C — transfer (idempotency_key is optional but recommended)
curl -s -X POST localhost:8003/transfers -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"source_account_id":"'$ACCOUNT_ID'","destination_account_id":"'$OTHER_ACCOUNT_ID'",
       "amount":"1500.00","currency":"INR","description":"rent","idempotency_key":"rent-2026-10"}'
# 201 {"id":"f69b…","kind":"TRANSFER","status":"COMPLETED","step":"DONE","amount":"1500.00",
#      "fraud_decision":"APPROVE","fraud_reason_codes":["WITHIN_LIMITS"],"notification_status":"SENT",…}
# Same request again → 200 with header Idempotent-Replayed: true and the same id; balances unchanged.
curl -s localhost:8003/transactions/$TX_ID -H "Authorization: Bearer $TOKEN"
curl -s localhost:8003/customers/$CUSTOMER_ID/transactions -H "Authorization: Bearer $TOKEN"

# Workflow D — simulated payment
curl -s -X POST localhost:8004/payments -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"account_id":"'$ACCOUNT_ID'","payment_type":"BILL","payee_name":"City Electricity",
       "payee_reference":"ELEC-1001","amount":"999.99","currency":"INR","idempotency_key":"elec-2026-10"}'
# 201 {"id":"a175…","status":"COMPLETED","provider_status":"APPROVED","provider_reference":"SIMPAY-23531DC964AE1A8E",
#      "transaction_id":"…",…}
curl -s localhost:8004/customers/$CUSTOMER_ID/payments -H "Authorization: Bearer $TOKEN"

# Notifications
curl -s localhost:8006/customers/$CUSTOMER_ID/notifications -H "Authorization: Bearer $TOKEN"
# 200 {"items":[{"type":"TRANSFER_SUCCEEDED","message":"Your transfer of INR 1500.00 from account ****fbde to ****0547 completed.",
#      "delivery_status":"DELIVERED_SIMULATED",…}],"total":…,"limit":20,"offset":0}
```

**Transfer and payment response codes:**

| Code | Meaning |
|---|---|
| `201` | Created and final: `COMPLETED`, `REJECTED` (see `failure_code`), or `FAILED` after compensation |
| `202` | Accepted but not final: `PROCESSING` (recovery will finish it) or `UNDER_REVIEW` |
| `200` | Idempotent replay of an earlier request with the same key and payload |
| `409` | Idempotency key reused with a different payload, or account inactive |
| `503` | A dependency was down before any money moved. `details.transaction_id` points to the `FAILED` record |
| `400` / `403` / `404` / `422` | Invalid semantics (e.g. `SAME_ACCOUNT`) / not your account / account missing / schema validation |

Simulated provider: a `payee_reference` starting with `DECLINE`, or an amount
above `PAYMENT_PROVIDER_MAX_AMOUNT`, is declined. The customer is then
refunded automatically and the payment ends `FAILED` with code
`PROVIDER_DECLINED`.

## 11. Complete registration-to-transfer walkthrough

`make seed-demo` runs the whole flow against running services. It works with
both `make run` and `make up`. It registers Alice and Bob, creates accounts,
shows that Bob gets `403` on Alice's account, seeds INR 25,000 (dev only),
transfers INR 1,500, and retries with the same idempotency key. It then makes
a bill payment and a INR 60,000 transfer that is held for review, and prints
Alice's history and notifications.

## 12. Seeding a demo balance safely

New accounts start at `0.00`, and there is **no public deposit endpoint**.
Two development-only mechanisms exist. Both refuse to run unless
`APP_ENV != production` **and** `DEV_SEED_ENABLED=true`:

```bash
# CLI inside the running container (Compose):
make seed-account ACCOUNT_ID=<uuid> AMOUNT=5000.00
# CLI locally:  cd services/account-service && DEV_SEED_ENABLED=true ../../.venv/bin/python -m app.dev_seed --account-id <uuid> --amount 5000.00

# Internal HTTP endpoint (needs the internal token; not mounted at all unless enabled):
curl -s -X POST localhost:8002/internal/dev/accounts/$ACCOUNT_ID/seed \
  -H "X-Internal-Token: ${INTERNAL_API_TOKEN:-dev-only-insecure-internal-token-do-not-use-in-production}" \
  -H 'Content-Type: application/json' -d '{"amount":"25000.00"}'
```

Seeds are recorded as `DEV_SEED` account operations.

## 13. Logs, recovery and troubleshooting

* Logs are one JSON object per line, with `service`, `request_id`, `method`,
  `path`, `status_code`, `duration_ms` and, for outbound calls, `target`.
  Passwords, JWTs, Authorization headers and the internal token are never
  logged. Fraud logs contain only decision metadata. Notification messages
  mask account IDs.
* `make logs` follows Compose logs. With `make run`, logs go to the terminal.
* Simulated notification deliveries are written to
  `NOTIFICATION_DELIVERY_LOG`, at `/data/notification-deliveries.log` in the
  container.

**Recovery: what is automated and what needs an operator**

| Situation | Handling |
|---|---|
| Timeout or outage during debit, credit or reversal; service crash mid-transfer | **Automated.** The recovery loop (every `RECOVERY_INTERVAL_SECONDS`) resumes from the persisted step after the lease expires. Run `make recover` to trigger it now. |
| Payment stuck while debiting or refunding | **Automated** by the payment-service recovery loop, through idempotent calls to the transaction service |
| Notification service down when a transfer or payment finished | **Automated.** The status is `notification_status=FAILED` and recovery resends it (deduplicated by event key) |
| Simulated delivery failure inside notification-service | **Operator:** `cd services/notification-service && python -m app.redeliver` (in Docker: `docker compose exec notification-service python -m app.redeliver`) |
| Unexpected contract errors (e.g. operation-ID conflict), or a reversal that is not applied | **Operator.** The transaction stays `PROCESSING` with `failure_reason` "Operator attention required…". Inspect it with `GET /internal/transactions/{id}` and the account operations. |
| `UNDER_REVIEW` | Terminal in Phase 1. No funds moved, and there is no review API yet. |

**Common errors**

* `401 TOKEN_EXPIRED`: log in again (the TTL is 15 minutes by default).
* `401 INVALID_TOKEN` across services: every service must have the signing
  key's public key (same `kid`) in `JWT_PUBLIC_KEYS_DIR`, and the same
  `JWT_ISSUER` and `JWT_AUDIENCE`.
* `429 TOO_MANY_LOGIN_ATTEMPTS`: wait for `Retry-After` seconds.
* `403 INTERNAL_ONLY`: the internal token is missing or wrong. Internal routes
  never accept customer JWTs.
* `503 *_UNAVAILABLE`: a dependency is down. Check `make ps`, or `/ready` on
  that service.
* `make up` fails with "port is already allocated": stop `make run`, or
  whatever else is using ports 8001–8006.
* Production startup error "Unsafe production configuration": set strong
  secrets (see §6).

**Resetting development data** (deliberate and irreversible):
`make reset-dev CONFIRM=yes`. This deletes `services/*/data` and the Compose
volumes, and refuses to run when `APP_ENV=production`. Normal startup
**never** deletes data; it only creates missing tables.

## 14. Known limitations and security boundaries

* **No transport security outside Kubernetes.** With `make run` or Compose,
  internal and external HTTP traffic is plaintext; the Docker bridge network
  is not a service mesh. On Kubernetes, service-to-service traffic is mTLS,
  but customer traffic through the NodePorts is still plain HTTP (no ingress
  TLS).
* **Public keys are distributed as files.** Only the auth service can mint
  tokens. Other services verify locally with configured public keys; they do
  not fetch JWKS at runtime, so rotation means updating the key files. `/auth/verify`
  additionally checks that the user still exists and is active.
* **The shared internal token** authenticates "some internal service", not a
  specific service identity. On Kubernetes the sidecars add per-service
  identity (mTLS + caller allow-lists); without Kubernetes the token is the
  only control.
* **No distributed ACID.** The saga guarantees at-most-once balance effects
  and eventual completion or compensation. In-flight money is temporarily
  visible in neither account.
* **Schema management:** each service has Alembic migrations, applied at
  startup in development (`DB_MIGRATION_MODE=upgrade`); production expects
  `check` with migrations run as a separate step. Nothing is ever dropped.
* **SQLite** is single-writer per database file, so each service should run
  one replica. Leases make concurrent recovery safe, but horizontal scaling
  needs PostgreSQL.
* **Fraud rules are simple deterministic thresholds**, not machine learning
  or production-grade fraud detection. A stored decision is never re-scored
  for the same operation ID. Defaults: an amount `> 50,000.00` gives
  `REVIEW`; `> 200,000.00` gives `REJECT` (configurable to `REVIEW`). A
  non-positive amount, too many decimal places, an unsupported currency, a
  transfer to the same account, or a blocked payee gives `REJECT`. There are
  no velocity, geolocation or behavioural rules.
* No rate limiting, account lockout, refresh tokens, logout or token
  revocation. A login-throttling and lockout policy is needed before any real
  exposure.
* Ownership failures return `403`, which reveals that a resource ID exists.
  IDs are random UUIDv4s.
* Only INR is supported. Payment debits leave the bank toward an external
  payee; there is no merchant ledger.

## 15. Planned phases

1. **Phase 1 (done):** banking microservices foundation.
2. **Phase 2 (done, single cluster):** Kubernetes manifests with PostgreSQL,
   NGINX sidecar mesh with mTLS on every internal call, canary routing.
3. **Multi-cluster mesh (local simulation done):** two local Kubernetes
   clusters simulating
   **Cluster A — "AWS"** (auth, account, transaction) and
   **Cluster B — "GCP"** (payment, fraud, notification). These are simulations,
   not real cloud environments. The mesh adds custom Go sidecar proxies,
   iptables interception, mTLS with service identities, a control plane with
   certificate management, service discovery, adaptive load balancing, canary
   traffic splitting, Prometheus and Grafana, and cross-cluster failure
   testing.

Phase 1 is already shaped for this. All internal URLs are configuration.
`/health` and `/ready` have distinct semantics. Services keep no local state
outside their databases. Request IDs are propagated. Outbound calls go through
one client layer (`common/http_client.py`), and business logic does not
depend on transport.

---

## 16. Service Mesh Architecture & mTLS Implementation

### 16.1 Architecture

Every service runs as a two-container pod. The application binds to
`127.0.0.1:8080` and is reachable only through its NGINX sidecar:

| Sidecar port | Purpose |
|---|---|
| `80xx` (plaintext) | Public API, kubelet probes, NodePort, payment router. Internal endpoints return `403 MTLS_REQUIRED`. |
| `8443` (mTLS) | Service-to-service traffic. Requires a client certificate from the mesh CA **and** an authorized caller identity. |
| `127.0.0.1:90xx` (egress) | The app calls e.g. `http://127.0.0.1:9002`; the sidecar opens mTLS to `account-service:8443` with its own certificate and verifies the server's. |
| `9102` | `stub_status` metrics and sidecar health. |

The call graph and allow-lists live in one file,
[`scripts/mesh_topology.py`](scripts/mesh_topology.py). From it,
`scripts/generate_mesh_certs.py` issues the certificates and
`scripts/generate_mesh_sidecars.py` generates `k8s/mesh-sidecar-configs.yaml`
(`make lint` fails if the generated file is stale).

| Service | Accepts mTLS calls from | Calls (through its sidecar) |
|---|---|---|
| auth-service | nobody | — |
| account-service | transaction, payment | — |
| transaction-service | payment | account, fraud, notification |
| payment-service (stable + canary) | nobody | account, transaction, fraud, notification |
| fraud-service | transaction, payment | — |
| notification-service | transaction, payment | — |

```
 payment pod                                         account pod
 ┌──────────────────────────────┐   mTLS :8443       ┌──────────────────────────────┐
 │ app ──http──▶ 127.0.0.1:9002 │══════════════════▶│ sidecar: verify cert (mesh CA)│
 │               sidecar egress │  client cert:      │  + caller ∈ {transaction,     │
 │               (payment cert) │  CN=payment-...    │    payment} ──▶ 127.0.0.1:8080│
 └──────────────────────────────┘                    └──────────────────────────────┘
```

### 16.2 Security controls

1. **Authentication:** workload certificates are signed by the MeshBank mesh
   root CA (RSA 2048, generated locally by `generate_mesh_certs.py`). Each
   carries the service's DNS names and a SPIFFE URI SAN
   (`spiffe://meshbank.internal/ns/meshbank/sa/<service>`). TLS 1.2 and 1.3
   are allowed.
2. **Authorization:** the receiving sidecar maps the certificate subject CN
   (the service's cluster DNS name) to an allow-list; anything else gets
   `403 IDENTITY_NOT_AUTHORIZED`. NGINX cannot read URI SANs without extra
   modules, so the check uses the CN; the CA signs both, so they cannot
   disagree.
3. **No bypass:** apps listen on loopback only. Internal endpoints are refused
   on the public ports: everything under `/internal/`, plus fraud `/evaluate`
   (fraud's public port serves only `/health` and `/ready`) and notification
   `POST /notifications`.
4. **NetworkPolicy** (`k8s/network-policy-mesh.yaml`): port 8443 only from
   meshed pods; PostgreSQL only from meshed pods. Requires kind ≥ 0.24.
5. **Certificate lifecycle:** certificates are reused across deploys and
   reissued only when missing, expiring within 14 days, or signed by another
   CA. `ROTATE_CERTS=1 make k8s-up` issues a new CA and restarts the pods.
   Rotation is manual: there is no control plane yet.
6. **Observability:** sidecar JSON access logs record the client DN, the
   verification result, the authorized caller (`mesh_caller`), TLS protocol
   and cipher, and upstream latency.

### 16.3 Running it

Prerequisites: Docker Desktop running, `brew install kind kubectl`, and
`make install` (Python venv).

```bash
make k8s-up          # build images, create the kind cluster, deploy everything
make k8s-status
make k8s-test        # banking smoke test; every service hop is mTLS
make k8s-mtls-test   # security tests (below)
make k8s-down        # delete the cluster
```

On Windows use `k8s\deploy.ps1` and `k8s\test-mesh-mtls.ps1`.

`make k8s-mtls-test` checks, for every service:
* internal endpoints on the public NodePort return `403 MTLS_REQUIRED`;
* the mTLS port rejects a client without a certificate and a certificate from
  an untrusted CA (the attacker certificate even claims `payment-service`'s
  name);
* a trusted certificate of a service that is *not* an authorized caller gets
  `403 IDENTITY_NOT_AUTHORIZED`;
* each authorized caller succeeds.

It then runs the smoke test and checks each receiving sidecar's logs for
verified, authorized mTLS traffic.

---

## 17. Phase 2: Traffic Management & Canary Deployment

### 17.1 Canary architecture

`payment-service` (NodePort 30004) points at **payment-router**, an NGINX
deployment that splits traffic between two deployments:

* **stable:** image `meshbank/payment-service:dev`, reports `v1`;
* **canary:** image `meshbank/payment-service:v2`, built with
  `--build-arg SERVICE_VERSION=v2`, reports `v2`.

The version comes from the running application (`/health` and the
`X-Service-Version` header), not from the proxy, so the split you measure is
the release that actually answered. Both tracks run the same code today; to
ship a real change, build the `v2` image from the changed code
(`k8s/deploy.sh` shows the command) and shift traffic.

```
            NodePort 30004
                  │
          ┌───────▼────────┐   X-Canary: always → canary
          │ payment-router │   X-Canary: never  → stable
          └───┬────────┬───┘   otherwise weighted (default 80/20)
              │        │
     payment-service-stable   payment-service-canary
        (v1 + sidecar)           (v2 + sidecar)
              └──── mTLS to account / transaction / fraud / notification
```

Response headers: `X-Service-Version` (from the app), `X-Mesh-Track` (from the
sidecar), `X-Mesh-Router` (from the router). CORS is answered by the app only.

### 17.2 Commands

```bash
make k8s-canary-split STABLE=90 CANARY=10    # applies the weights, restarts the router
make k8s-canary-demo                         # 90/10 → 50/50 → 0/100 → 100/0, measuring each
.venv/bin/python k8s/canary-traffic.py --verify 200      # measure the live split
.venv/bin/python k8s/canary-traffic.py --test-header     # X-Canary overrides
.venv/bin/python k8s/canary-traffic.py --demo-rollback   # canary failure → 100% stable
```

A weight of 0 marks that backend `down`. A new split is applied by restarting
the router (a rolling restart; NGINX reads its config only at start). The
committed `k8s/mesh-router-config.yaml` keeps the 80/20 default; splits are
applied to the cluster, not written back to the file. The dashboard's release
buttons call the same script through the Vite dev server, which accepts
integer weights only, from the dashboard's own origin.

---

## 18. Two-cluster mesh (AWS / GCP simulation)

`make k8s-multi-up` runs the same services across two local kind clusters.
They are named after two clouds, but both are Docker containers on your
machine.

| Cluster (kubectl context) | Simulates | Runs | Host ports |
|---|---|---|---|
| `kind-meshbank-aws` | AWS | auth, account, transaction, PostgreSQL, gateway | 30001-30003, gateway 31443 |
| `kind-meshbank-gcp` | GCP | payment (stable + canary + router), fraud, notification, PostgreSQL, gateway | 30004-30006, gateway 32443 |

Every customer flow crosses clusters: a transfer runs in "AWS" and its saga
calls fraud and notification in "GCP"; a payment runs in "GCP" and debits an
account in "AWS". Pod and service address ranges do not overlap
(`10.10/16`, `10.11/16` vs `10.20/16`, `10.21/16`).

### How a cross-cluster call works

```
 kind-meshbank-gcp                                       kind-meshbank-aws
 payment app ─http─▶ 127.0.0.1:9002 (own sidecar)
                     mTLS, client cert payment-service,
                     SNI account-service.meshbank.svc.cluster.local
                       │
                       ▼ aws-gateway alias Service ──▶ node :30443 ──▶ east-west gateway
                                                                     reads SNI only,
                                                                     forwards still-encrypted
                                                                       │
                                                                       ▼
                                                        account sidecar :8443
                                                        verifies cert + caller allow-list
```

* **mTLS stays end to end.** The gateway uses `ssl_preread`: it reads the
  server name from the TLS ClientHello and forwards the encrypted bytes. It
  holds no keys and cannot read or alter the traffic. The receiving sidecar
  authenticates and authorizes the caller exactly as it does in one cluster.
* **Gateways route only exported services:** the ones the other cluster is
  authorized to call (account and transaction from AWS; fraud and
  notification from GCP). Any other name is refused at the gateway.
* **Sidecar configs contain no addresses.** A sidecar calls the alias Service
  `<cluster>-gateway`. The deploy script points that alias, through an
  EndpointSlice, at the other cluster's node. On real clouds the alias would
  point at a load balancer instead.
* **One CA, separate keys.** Both clusters trust the same mesh CA, but each
  cluster receives only its own workloads' private keys.
* **No hardcoded DNS.** Proxies take their resolver from the pod's
  `/etc/resolv.conf` at start, so the same configs work on kind, EKS and GKE.

The placement (`cluster=` in [`scripts/mesh_topology.py`](scripts/mesh_topology.py))
drives everything: `make mesh-configs` regenerates the per-mode sidecar
configs and the gateways, and `scripts/cluster_manifests.py` selects each
cluster's objects at deploy time.

### Running it

The single cluster and the two clusters use the same host ports, so run one
at a time (the deploy scripts refuse to start otherwise).

```bash
make k8s-down                    # if the single cluster is running
make k8s-multi-up                # build, create both clusters, deploy
make k8s-multi-status            # nodes are labelled with the cloud they simulate
make k8s-multi-test              # smoke test; transfers and payments cross clusters
make k8s-multi-mtls-test         # adds gateway attacks and cross-cluster log checks
make k8s-multi-canary-demo       # canary runs in the gcp cluster
make frontend MESHBANK_TOPOLOGY=multi
make k8s-multi-down
```

Two kind clusters need roughly 6 GB of Docker memory and several GB of disk.
Linux and macOS only (the Windows `deploy.ps1` covers the single cluster).


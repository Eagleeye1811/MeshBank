# MeshBank

**MeshBank: A Secure Multi-Cloud Service Mesh Infrastructure for a Cloud-Native Banking Platform**
*Secure connections. Reliable banking. Across clouds.*

MeshBank is a **simulated** banking platform. In later phases it will show secure
service-to-service communication across two Kubernetes clusters that stand in for
AWS and GCP. That communication will use a custom sidecar data plane, mTLS,
iptables interception, adaptive load balancing and canary routing.

This repository currently contains **Phase 1: the banking microservices
foundation**. It has six independently runnable FastAPI services with real
persistence, validation, authentication, an idempotent transfer saga, and
automated tests.

> All money is simulated. Nothing connects to real banks, payment gateways,
> email/SMS providers, or production banking APIs.

---

## 1. Phase 1 scope and what is *not* implemented

| Implemented in Phase 1 | Not implemented (future phases) |
|---|---|
| Six FastAPI services, each with its own database | Service mesh, sidecar proxies (Go) |
| JWT auth (RS256 with key IDs, JWKS, rotation), login throttling, Argon2id password hashing | mTLS, workload identity, certificate management |
| Ownership checks on every customer resource | iptables traffic interception |
| Decimal-safe money, INR only | Cross-cluster / multi-cloud networking |
| Durable, idempotent transfer saga with compensation and recovery | Adaptive load balancing, canary routing |
| Deterministic fraud rules | Prometheus metrics, Grafana dashboards |
| Simulated payment provider and simulated notification delivery | Kubernetes manifests |
| Structured JSON logs with request-ID propagation | Rate limiting (`429`), refresh tokens, token revocation |
| Docker images and Docker Compose | Alembic migrations (see [Limitations](#14-known-limitations-and-security-boundaries)) |

Internal traffic between services is **plain HTTP**. A shared internal token
authenticates it. **It is not encrypted.** JWTs authenticate customers. They do
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

* **No transport security.** Internal and external HTTP traffic is plaintext.
  The Docker bridge network is **not** a service mesh and gives no
  multi-cloud isolation.
* **Public keys are distributed as files.** Only the auth service can mint
  tokens. Other services verify locally with configured public keys; they do
  not fetch JWKS at runtime, so rotation means updating the key files. `/auth/verify`
  additionally checks that the user still exists and is active.
* **The shared internal token** authenticates "some internal service", not a
  specific service identity. mTLS SPIFFE-style identities replace it later.
* **No distributed ACID.** The saga guarantees at-most-once balance effects
  and eventual completion or compensation. In-flight money is temporarily
  visible in neither account.
* **Schema management:** tables are created idempotently at startup
  (`create_all`); nothing is ever dropped. **Alembic migrations are not yet
  set up.** This is a deliberate Phase 1 simplification, since every schema
  is new. Migrations must be added before the first schema change or the
  PostgreSQL deployment.
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

1. **Phase 1 (this repository):** banking microservices foundation.
2. **Phase 2:** container hardening and local deployment validation (image
   scanning, Alembic, PostgreSQL), then Kubernetes manifests.
3. **Service mesh:** two local Kubernetes clusters simulating
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

### 16.1 Architecture Overview

MeshBank implements a lightweight, cloud-native sidecar service mesh data plane for secure inter-service communication between critical banking services (`transaction-service` and `payment-service`):

```
+---------------------------------------------------------------------------------------------------+
| Kubernetes Namespace: meshbank                                                                    |
|                                                                                                   |
|  +---------------------------------------+       mTLS (Port 8443)      +------------------------+ |
|  | Pod: payment-service                  |   TLSv1.3 / AES_256_GCM    | Pod: transaction-svc   | |
|  |                                       |============================>|                        | |
|  | [payment-service app]                 |  Client: payment-service    | [mesh-sidecar proxy]   | |
|  |   Listens on: 127.0.0.1:8080          |  Server: transaction-service|   Port 8443: mTLS      | |
|  |                                       |                             |   Port 8003: Public In | |
|  | [mesh-sidecar proxy]                  |                             |   Port 9102: Metrics   | |
|  |   Port 8004: Public Ingress           |                             |            │           | |
|  |   Port 9003: Egress Proxy ────────────┘                             |            ▼ localhost | |
|  |   Port 9102: Metrics                                                | [transaction-svc app]  | |
|  +---------------------------------------+                             |   Listens: 127.0.0.1   | |
|                                                                        +------------------------+ |
+---------------------------------------------------------------------------------------------------+
```

### 16.2 Core Security Controls

1. **Mutual TLS (mTLS) Authentication:**
   * Both sides of the communication verify cryptographic certificates issued by an internal **MeshBank Mesh Root CA**.
   * Workload identities are encoded using SPIFFE Subject Alternative Names (SANs):
     * `spiffe://meshbank.internal/ns/meshbank/sa/transaction-service`
     * `spiffe://meshbank.internal/ns/meshbank/sa/payment-service`
   * Communication strictly requires TLS 1.3/1.2 with forward secrecy (`TLS_AES_256_GCM_SHA384`).
2. **Loopback Application Isolation & NetworkPolicy:**
   * Core microservice application containers bind strictly to loopback (`127.0.0.1:8080`). They cannot be directly contacted from outside their Pod.
   * All ingress and egress traffic is mediated through the sidecar proxy container.
   * Kubernetes `NetworkPolicy` (`k8s/network-policy-mesh.yaml`) enforces that only authorized mesh traffic can reach the pods.
3. **Strict Rejection of Bypass Attempts:**
   * Sending plaintext HTTP requests directly to internal endpoints (`/internal/...`) on public ports 8003/8004 is rejected with `403 Forbidden` (`MTLS_REQUIRED`).
   * Requests lacking client certificates are rejected with `400 Bad Request` (`No required SSL certificate was sent`).
   * Requests presenting untrusted certificates (e.g., signed by an unauthorized/rogue CA) are rejected with `400 Bad Request` (`The SSL certificate error`).
4. **Observability & Metrics:**
   * Structured JSON logs capture client Subject DN (`ssl_client_s_dn`), verification status (`ssl_client_verify`), protocol, cipher, upstream latency, and response status.
   * Dedicated `/metrics` endpoint on port `9102` provides active connection counts and traffic statistics.

### 16.3 Setup & Verification Commands

Generate certificates and deploy to the cluster:
```powershell
# 1. Generate Mesh Root CA and Workload Certificates
.\.venv\Scripts\python.exe scripts\generate_mesh_certs.py

# 2. Deploy to Kubernetes (or use deploy.ps1)
.\k8s\deploy.ps1

# 3. Run the automated Service Mesh & mTLS Compliance Test Suite
.\k8s\test-mesh-mtls.ps1
```

Or run directly with Python:
```bash
python k8s/test-mesh-mtls.py
```

### 16.4 Actual Test Results

```text
======================================================================
  MESHBANK SERVICE MESH & mTLS COMPLIANCE TEST SUITE
======================================================================

--- Phase 1.1: Certificate & Workload Identity Artifacts ---
  [PASS] Artifact Mesh Root CA exists
  [PASS] Artifact payment-service client certificate exists
  [PASS] Artifact payment-service private key exists
  [PASS] Artifact transaction-service certificate exists
  [PASS] Artifact transaction-service private key exists
  [PASS] Artifact Attacker rogue certificate exists
  [PASS] Artifact Attacker rogue key exists

--- Phase 1.2: Plaintext Bypass Rejection (Network Restriction) ---
  [PASS] Plaintext request to transaction-service /internal/ is blocked (HTTP 403 MTLS_REQUIRED)
  [PASS] Plaintext request to payment-service /internal/ is blocked (HTTP 403 MTLS_REQUIRED)

--- Phase 1.3: Direct mTLS Handshake & Identity Verification ---
  [PASS] Request without client certificate is rejected by mTLS sidecar (HTTP 400 No Cert)
  [PASS] Request with untrusted attacker certificate is rejected by mTLS sidecar (HTTP 400 SSL Error)
  [PASS] Request with valid payment-service workload certificate succeeds (HTTP 200 OK)

--- Phase 1.4: In-Cluster Mesh Traffic Mediation ---
  [PASS] End-to-End banking transaction & bill payment through mesh proxies (All 9 steps pass)

--- Phase 1.5: Sidecar Proxy Structured Logs & Metrics ---
  [PASS] Transaction sidecar captured valid mTLS handshake with payment-service
         Protocol=TLSv1.3, Cipher=TLS_AES_256_GCM_SHA384, Client-DN=OU=Mesh Workload,O=MeshBank,CN=payment-service.meshbank.svc.cluster.local
  [PASS] Sidecar metrics endpoint (port 9102) active and recording stats

======================================================================
  TEST SUMMARY: 15 PASSED, 0 FAILED
======================================================================
```

---

## 17. Phase 2: Traffic Management & Canary Deployment

### 17.1 Canary Architecture

MeshBank implements proxy-based dynamic traffic splitting between **Stable (`v1`)** and **Canary (`v2`)** versions of `payment-service`:

```
                           Incoming Requests (NodePort 30004 or Mesh)
                                              │
                                              ▼
                             ┌─────────────────────────────────┐
                             │         payment-router          │
                             │   (NGINX Traffic Controller)    │
                             └────────────────┬────────────────┘
                                              │
                    ┌─────────────────────────┴────────────────────────┐
   Header Override  │                                                  │  Header Override
   X-Canary: never  │ Default Split (e.g. 80% / 20%)                   │  X-Canary: always
                    ▼                                                  ▼
      ┌───────────────────────────┐                      ┌───────────────────────────┐
      │  payment-service-stable   │                      │  payment-service-canary   │
      │       (Track: stable)     │                      │       (Track: canary)     │
      │         Version: v1       │                      │         Version: v2       │
      │                           │                      │                           │
      │  [mesh-sidecar] Port 8443 │                      │  [mesh-sidecar] Port 8443 │
      │  [payment-app]  Port 8080 │                      │  [payment-app]  Port 8080 │
      └─────────────┬─────────────┘                      └─────────────┬─────────────┘
                    │                                                  │
                    └───────────────── mTLS (Port 8443) ───────────────┘
                                              │
                                              ▼
                                 ┌───────────────────────────┐
                                 │    transaction-service    │
                                 └───────────────────────────┘
```

### 17.2 Capabilities & Controls

1. **Dynamic Weighted Traffic Splitting:**
   * Split ratio can be updated on-the-fly without restarting pods or losing connections (e.g., 90/10, 50/50, 0/100, 100/0).
   * Responses carry track and version headers for transparency:
     * `X-Mesh-Track: stable` vs `canary`
     * `X-Service-Version: v1` vs `v2`
     * `X-Mesh-Router: payment-traffic-manager`
2. **Header-based Routing (A/B Testing Overrides):**
   * `X-Canary: always` routes 100% of requests to the Canary deployment regardless of weights.
   * `X-Canary: never` routes 100% of requests to the Stable deployment.
3. **Health-based Rollout & Automatic Rollback:**
   * Circuit-breaking and fail-over with `max_fails=2 fail_timeout=5s`.
   * Automated rollback restores 100% stable traffic instantly if the canary deployment becomes degraded or unavailable.

### 17.3 Traffic Management Commands

```powershell
# Run the complete automated canary rollout lifecycle demo
.\k8s\canary-traffic.ps1 -Command demo-rollout

# Run the health failure & automatic rollback demonstration
.\k8s\canary-traffic.ps1 -Command demo-rollback

# Test header-based routing overrides (A/B testing)
.\k8s\canary-traffic.ps1 -Command header

# Dynamically set a specific split ratio (e.g. 70% stable, 30% canary)
.\k8s\canary-traffic.ps1 -Command set-split -Stable 70 -Canary 30

# Verify the actual live request distribution over 100 requests
.\k8s\canary-traffic.ps1 -Command verify -Count 100
```

### 17.4 Actual Live Test Results

```text
======================================================================
  STAGE 1: CANARY ACTIVATION (90% STABLE / 10% CANARY)
======================================================================
  Total Requests:  100
  Successful:      100 | Failed: 0
  Stable (v1):      90 hits ( 90.0%)
  Canary (v2):      10 hits ( 10.0%)

======================================================================
  STAGE 2: TRAFFIC SPLIT RAMP-UP (50% STABLE / 50% CANARY)
======================================================================
  Total Requests:  100
  Successful:      100 | Failed: 0
  Stable (v1):      50 hits ( 50.0%)
  Canary (v2):      50 hits ( 50.0%)

======================================================================
  STAGE 3: FULL PROMOTION (0% STABLE / 100% CANARY)
======================================================================
  Total Requests:  100
  Successful:      100 | Failed: 0
  Stable (v1):       0 hits (  0.0%)
  Canary (v2):     100 hits (100.0%)

======================================================================
  STAGE 4: AUTOMATED ROLLBACK TO STABLE (100% STABLE / 0% CANARY)
======================================================================
  Total Requests:  100
  Successful:      100 | Failed: 0
  Stable (v1):     100 hits (100.0%)
  Canary (v2):       0 hits (  0.0%)

[Header-based Routing Overrides]
  [PASS] X-Canary: always -> canary (v2)
  [PASS] X-Canary: never  -> stable (v1)
```



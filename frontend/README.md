# MeshBank dashboard

A small React app for demonstrating MeshBank. All money is simulated.

```bash
make frontend                          # http://localhost:5173
make frontend MESHBANK_TOPOLOGY=multi  # when running the two-cluster setup
```

It finds the running services by itself (Kubernetes first, then `make run` /
Docker on this computer); “Running on” in the header switches between them.

## Two pages

**Banking** is what a customer sees. Sign in as Alice or Bob; the first sign-in
creates both customers with a savings account each, and gives Alice ₹25,000.

* *Send money*: pick the recipient from a list (no account IDs to copy). Amounts
  above ₹50,000 are held by the fraud check. “Send the exact same request
  again” shows that a repeated request is not charged twice.
* *Pay a bill*: the “always declines” biller shows an automatic refund.
* *Activity* and *Messages* show what happened.
* *Add test money* tops up an account (development only).

**Behind the scenes** is for explaining the system: whether each service is
running, two security checks (an outsider calling an internal endpoint; a
customer opening someone else's account), and the canary release of the
payment service (Kubernetes only).

## Code

| File | Contents |
|---|---|
| `src/App.jsx` | Header, page switch, environment, sign-in |
| `src/Banking.jsx` | Accounts, send money, pay a bill, activity, messages |
| `src/System.jsx` | Service status, security checks, release rollout |
| `src/api.js` | Service addresses, `call()` and readable error messages |
| `src/demo.js` | Demo customers, one-time setup, known-recipients list |
| `src/ui.jsx`, `src/styles.js` | Shared components, status wording |
| `vite.config.js` | Dev-server endpoints for the canary and test money; keeps the internal token out of the browser |

// Where the six services are reachable, and small helpers for calling them.

export const ENVIRONMENTS = {
  k8s: {
    label: 'Kubernetes',
    hint: 'make k8s-up or make k8s-multi-up',
    services: {
      auth: 'http://localhost:30001',
      account: 'http://localhost:30002',
      transaction: 'http://localhost:30003',
      payment: 'http://localhost:30004',
      fraud: 'http://localhost:30005',
      notification: 'http://localhost:30006',
    },
  },
  docker: {
    label: 'On this computer',
    hint: 'make run or make up',
    services: {
      auth: 'http://localhost:8001',
      account: 'http://localhost:8002',
      transaction: 'http://localhost:8003',
      payment: 'http://localhost:8004',
      fraud: 'http://localhost:8005',
      notification: 'http://localhost:8006',
    },
  },
};

export const SERVICES = [
  { id: 'auth', name: 'Sign-in', detail: 'Customer accounts and login tokens' },
  { id: 'account', name: 'Accounts', detail: 'Balances and account records' },
  { id: 'transaction', name: 'Transfers', detail: 'Moves money between accounts' },
  { id: 'payment', name: 'Payments', detail: 'Pays bills to outside companies' },
  { id: 'fraud', name: 'Fraud checks', detail: 'Holds or blocks risky amounts' },
  { id: 'notification', name: 'Messages', detail: 'Sends confirmations to customers' },
];

// Which simulated cloud runs each service in the two-cluster setup.
export const CLOUD_OF = {
  auth: 'AWS', account: 'AWS', transaction: 'AWS',
  payment: 'GCP', fraud: 'GCP', notification: 'GCP',
};

export const serviceUrl = (envKey, svc) => ENVIRONMENTS[envKey].services[svc];

// fetch + JSON, never throws on HTTP errors: returns { ok, status, data, headers }.
// A network failure returns status 0 with a readable message.
export async function call(url, { method = 'GET', token, body, headers = {}, timeout = 10000 } = {}) {
  try {
    const res = await fetch(url, {
      method,
      headers: {
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(timeout),
    });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data, headers: res.headers };
  } catch (err) {
    const message = err.name === 'TimeoutError'
      ? 'The service did not answer in time.'
      : 'Cannot reach the service. Is MeshBank running?';
    return { ok: false, status: 0, data: { error: { message } }, headers: new Headers() };
  }
}

// The services answer errors as {"error": {"code", "message", "details"}}.
export function errorText(result) {
  const err = result?.data?.error;
  if (typeof err === 'string') return err;
  if (err?.code === 'VALIDATION_ERROR' && err.details?.length) {
    const d = err.details[0];
    const field = (d.loc || []).filter(x => x !== 'body').join(' ');
    return `${field ? field + ': ' : ''}${d.msg}`;
  }
  return err?.message || result?.data?.detail || `Something went wrong (HTTP ${result?.status}).`;
}

export const inr = (value) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Number(value || 0));

export const lastFour = (accountNumber = '') => `•••• ${String(accountNumber).slice(-4)}`;

export const customerIdFrom = (token) => {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(part)).sub;
  } catch {
    return null;
  }
};

export const newRequestKey = (prefix) => `${prefix}-${crypto.randomUUID()}`;

export const timeAgo = (iso) => {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString('en-IN');
};

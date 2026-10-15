// Demo customers and the browser-side helpers that make the demo self-explanatory.
import { call, customerIdFrom, serviceUrl } from './api.js';

export const DEMO_CUSTOMERS = {
  alice: { key: 'alice', name: 'Alice Vance', email: 'alice@example.com', password: 'MeshBank#ALICE123!' },
  bob: { key: 'bob', name: 'Bob Martin', email: 'bob@example.com', password: 'MeshBank#BOB123!' },
};

const STARTING_BALANCE = '25000.00';

const read = (key, fallback) => {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
};
const write = (key, value) => {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage unavailable (private window): the demo still works, just forgets.
  }
};
export const store = { read, write };

// Sign in a demo customer, registering them the first time.
export async function signIn(envKey, customerKey) {
  const c = DEMO_CUSTOMERS[customerKey];
  const auth = serviceUrl(envKey, 'auth');
  const credentials = { email: c.email, password: c.password };
  let res = await call(`${auth}/auth/login`, { method: 'POST', body: credentials });
  if (res.status === 401 || res.status === 400) {
    await call(`${auth}/auth/register`, { method: 'POST', body: { ...credentials, full_name: c.name } });
    res = await call(`${auth}/auth/login`, { method: 'POST', body: credentials });
  }
  return res.ok ? { ok: true, token: res.data.access_token } : { ok: false, result: res };
}

export async function listAccounts(envKey, token) {
  const url = `${serviceUrl(envKey, 'account')}/customers/${customerIdFrom(token)}/accounts`;
  const res = await call(url, { token });
  return { ...res, accounts: res.data.items || [] };
}

export async function openAccount(envKey, token, accountType) {
  return call(`${serviceUrl(envKey, 'account')}/accounts`, {
    method: 'POST', token, body: { account_type: accountType, currency: 'INR' },
  });
}

// Development-only funding, through the Vite dev server (it holds the internal
// credentials; the browser never sees them).
export async function addTestMoney(envKey, accountId, amount) {
  return call('/api/dev/seed', { method: 'POST', body: { env: envKey, accountId, amount } });
}

// Accounts of the demo customers seen in this browser, so "Send money" can offer
// "Bob Martin - Savings" instead of asking for an account ID. Per environment,
// because each environment has its own databases.
const directoryKey = (envKey) => `meshbank_directory_${envKey}`;

export const readDirectory = (envKey) => read(directoryKey(envKey), {});

export function rememberAccounts(envKey, customerKey, accounts) {
  const directory = readDirectory(envKey);
  for (const a of accounts) {
    directory[a.id] = {
      owner: DEMO_CUSTOMERS[customerKey].name,
      ownerKey: customerKey,
      type: a.account_type,
      number: a.account_number,
    };
  }
  write(directoryKey(envKey), directory);
}

// Make sure both demo customers exist with one account each, and give a newly
// opened account of Alice's some money to start with. Safe to repeat.
export async function setUpDemo(envKey) {
  for (const key of Object.keys(DEMO_CUSTOMERS)) {
    const session = await signIn(envKey, key);
    if (!session.ok) return session;
    const listed = await listAccounts(envKey, session.token);
    if (!listed.ok) return { ok: false, result: listed };
    let accounts = listed.accounts;
    if (accounts.length === 0) {
      const opened = await openAccount(envKey, session.token, 'SAVINGS');
      if (!opened.ok) return { ok: false, result: opened };
      accounts = [opened.data];
      if (key === 'alice') await addTestMoney(envKey, opened.data.id, STARTING_BALANCE);
    }
    rememberAccounts(envKey, key, accounts);
  }
  return { ok: true };
}

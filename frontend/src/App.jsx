import { useCallback, useEffect, useState } from 'react';
import { Landmark, LogOut, UserCircle2 } from 'lucide-react';
import { ENVIRONMENTS, call, errorText, serviceUrl } from './api.js';
import { DEMO_CUSTOMERS, setUpDemo, signIn, store } from './demo.js';
import Banking from './Banking.jsx';
import System from './System.jsx';
import { Button, Card, Notice, Toasts } from './ui.jsx';

// Pick whichever deployment is answering: Kubernetes first, then this computer.
async function detectEnvironment() {
  for (const key of ['k8s', 'docker']) {
    const res = await call(`${serviceUrl(key, 'auth')}/health`, { timeout: 1500 });
    if (res.ok) return key;
  }
  return 'k8s';
}

export default function App() {
  const [envKey, setEnvKey] = useState(() => store.read('meshbank_env', null));
  const [page, setPage] = useState(() => store.read('meshbank_page', 'banking'));
  const [session, setSession] = useState(null); // { customerKey, token }
  const [toasts, setToasts] = useState([]);

  useEffect(() => {
    if (!envKey) detectEnvironment().then(setEnvKey);
  }, [envKey]);

  // Each environment has its own databases, so it has its own signed-in customer.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (envKey) setSession(store.read(`meshbank_session_${envKey}`, null));
  }, [envKey]);

  const toast = useCallback((message, tone = 'info') => {
    const id = crypto.randomUUID();
    setToasts(prev => [...prev, { id, message, tone }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 5000);
  }, []);

  const saveSession = useCallback((next) => {
    setSession(next);
    store.write(`meshbank_session_${envKey}`, next);
  }, [envKey]);

  const choosePage = (next) => {
    setPage(next);
    store.write('meshbank_page', next);
  };

  const chooseEnv = (next) => {
    store.write('meshbank_env', next);
    setEnvKey(next);
  };

  // Calls a service as the signed-in customer. Logins last 15 minutes; when one
  // expires, sign the demo customer in again and retry once.
  const request = useCallback(async (svc, path, options = {}) => {
    const url = `${serviceUrl(envKey, svc)}${path}`;
    let res = await call(url, { ...options, token: session?.token });
    if (res.status === 401 && session) {
      const again = await signIn(envKey, session.customerKey);
      if (again.ok) {
        saveSession({ ...session, token: again.token });
        res = await call(url, { ...options, token: again.token });
      }
    }
    return res;
  }, [envKey, session, saveSession]);

  if (!envKey) {
    return <div className="min-h-screen flex items-center justify-center text-slate-500">Looking for MeshBank…</div>;
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3 flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex items-center gap-2.5">
            <span className="w-9 h-9 rounded-xl bg-blue-600 text-white flex items-center justify-center">
              <Landmark className="w-5 h-5" />
            </span>
            <div className="leading-tight">
              <p className="font-semibold">MeshBank</p>
              <p className="text-xs text-slate-500">Demo bank · simulated money</p>
            </div>
          </div>

          <nav className="flex gap-1 rounded-xl bg-slate-100 p-1" aria-label="Main">
            {[['banking', 'Banking'], ['system', 'Behind the scenes']].map(([key, label]) => (
              <button
                key={key}
                onClick={() => choosePage(key)}
                aria-current={page === key ? 'page' : undefined}
                className={`rounded-lg px-4 py-2 text-sm font-semibold transition
                  ${page === key ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600 hover:text-slate-900'}`}
              >
                {label}
              </button>
            ))}
          </nav>

          <div className="flex items-center gap-3 ml-auto">
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <span className="hidden sm:inline">Running on</span>
              <select
                value={envKey}
                onChange={e => chooseEnv(e.target.value)}
                className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900"
              >
                {Object.entries(ENVIRONMENTS).map(([key, env]) => <option key={key} value={key}>{env.label}</option>)}
              </select>
            </label>
            {session && (
              <div className="flex items-center gap-2 text-sm">
                <UserCircle2 className="w-5 h-5 text-slate-400" />
                <span className="font-medium whitespace-nowrap">{DEMO_CUSTOMERS[session.customerKey].name.split(' ')[0]}</span>
                <button
                  onClick={() => saveSession(null)}
                  className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                  title="Switch customer"
                >
                  <LogOut className="w-4 h-4" /> <span className="hidden sm:inline">Switch</span>
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        {page === 'system' ? (
          <System envKey={envKey} customerKey={session?.customerKey} token={session?.token} />
        ) : session ? (
          <Banking
            key={`${envKey}-${session.customerKey}`}
            envKey={envKey}
            customerKey={session.customerKey}
            token={session.token}
            request={request}
            toast={toast}
          />
        ) : (
          <ChooseCustomer envKey={envKey} onSignedIn={saveSession} />
        )}
      </main>

      <Toasts toasts={toasts} />
    </div>
  );
}

function ChooseCustomer({ envKey, onSignedIn }) {
  const [busy, setBusy] = useState(null);
  const [problem, setProblem] = useState(null);

  const start = async (customerKey) => {
    setBusy(customerKey);
    setProblem(null);
    // Creates both demo customers with an account each (first time only), so
    // there is always someone to send money to.
    const setup = await setUpDemo(envKey);
    const session = setup.ok ? await signIn(envKey, customerKey) : setup;
    setBusy(null);
    if (session.ok) onSignedIn({ customerKey, token: session.token });
    else setProblem(errorText(session.result));
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="text-center space-y-2">
        <h1 className="text-2xl font-semibold">Welcome to MeshBank</h1>
        <p className="text-slate-600">
          Pick a demo customer to sign in. Both have an account ready, and Alice starts with {' '}
          ₹25,000 so you can send money to Bob straight away.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        {Object.values(DEMO_CUSTOMERS).map(c => (
          <Card key={c.key}>
            <div className="flex flex-col items-center text-center gap-3">
              <span className="w-14 h-14 rounded-full bg-blue-50 text-blue-700 flex items-center justify-center text-xl font-semibold">
                {c.name[0]}
              </span>
              <div>
                <p className="font-semibold">{c.name}</p>
                <p className="text-sm text-slate-500">{c.email}</p>
              </div>
              <Button className="w-full" busy={busy === c.key} disabled={busy !== null} onClick={() => start(c.key)}>
                Sign in as {c.name.split(' ')[0]}
              </Button>
            </div>
          </Card>
        ))}
      </div>
      {busy && <p className="text-center text-sm text-slate-500">Getting the demo ready…</p>}
      {problem && (
        <Notice tone="bad" title="Could not sign in">
          {problem}. Check that MeshBank is running ({ENVIRONMENTS[envKey].hint}) and that “Running on” matches.
        </Notice>
      )}
    </div>
  );
}

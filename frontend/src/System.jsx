// "Behind the scenes": is everything running, is it secure, and the release rollout.
import { useCallback, useEffect, useState } from 'react';
import { GitBranch, RefreshCw, Server, ShieldCheck } from 'lucide-react';
import { CLOUD_OF, ENVIRONMENTS, SERVICES, call, errorText, serviceUrl } from './api.js';
import { readDirectory } from './demo.js';
import { Button, Card, Notice } from './ui.jsx';

export default function System({ envKey, customerKey, token }) {
  const [topology, setTopology] = useState('single');

  useEffect(() => {
    call('/api/mesh/info').then(res => {
      if (res.ok) setTopology(res.data.topology);
    });
  }, []);

  const onKubernetes = envKey === 'k8s';
  return (
    <div className="space-y-6">
      <Notice tone="info" title="What this page is for">
        The customer screens above run on six small services. This page shows that they are
        running, that outsiders cannot misuse them, and how a new version is released safely.
      </Notice>
      <Services envKey={envKey} showClouds={onKubernetes && topology === 'multi'} />
      <SecurityChecks envKey={envKey} onKubernetes={onKubernetes} customerKey={customerKey} token={token} />
      <Rollout envKey={envKey} onKubernetes={onKubernetes} />
    </div>
  );
}

function Services({ envKey, showClouds }) {
  const [status, setStatus] = useState({});
  const [checking, setChecking] = useState(false);

  const check = useCallback(async () => {
    setChecking(true);
    const results = await Promise.all(
      SERVICES.map(s => call(`${serviceUrl(envKey, s.id)}/ready`, { timeout: 4000 })),
    );
    setStatus(Object.fromEntries(SERVICES.map((s, i) => [s.id, results[i].ok])));
    setChecking(false);
  }, [envKey]);

  useEffect(() => {
    // Polling the services is the external system this effect synchronizes with.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    check();
  }, [check]);

  const online = Object.values(status).filter(Boolean).length;
  return (
    <Card
      title="Services"
      subtitle={
        checking ? 'Checking…'
          : `${online} of ${SERVICES.length} running (${ENVIRONMENTS[envKey].label.toLowerCase()})`
      }
      action={<Button variant="secondary" busy={checking} onClick={check}><RefreshCw className="w-4 h-4" /> Check again</Button>}
    >
      {showClouds && (
        <p className="text-sm text-slate-600 mb-4">
          Split across two clusters that stand in for two clouds (both run on this computer).
          Calls between them are encrypted end to end.
        </p>
      )}
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {SERVICES.map(s => {
          const up = status[s.id];
          return (
            <li key={s.id} className="rounded-xl border border-slate-200 p-4 flex gap-3">
              <Server className="w-5 h-5 text-slate-400 mt-0.5 shrink-0" />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="font-medium text-slate-900">{s.name}</p>
                  {showClouds && (
                    <span className="text-[11px] font-semibold rounded-full bg-slate-100 text-slate-600 px-2 py-0.5">
                      {CLOUD_OF[s.id]}
                    </span>
                  )}
                </div>
                <p className="text-xs text-slate-500">{s.detail}</p>
                <p className={`text-xs font-semibold mt-1.5 ${up === undefined ? 'text-slate-400' : up ? 'text-emerald-700' : 'text-rose-700'}`}>
                  {up === undefined ? 'Checking…' : up ? '● Running' : '● Not responding'}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
      {online === 0 && !checking && (
        <div className="mt-4">
          <Notice tone="warn" title="Nothing is answering">
            Start MeshBank first ({ENVIRONMENTS[envKey].hint}), or switch “Running on” at the top.
          </Notice>
        </div>
      )}
    </Card>
  );
}

function SecurityChecks({ envKey, onKubernetes, customerKey, token }) {
  const [outsider, setOutsider] = useState(null);
  const [snoop, setSnoop] = useState(null);
  const [busy, setBusy] = useState(null);

  const otherAccount = Object.entries(readDirectory(envKey)).find(([, e]) => e.ownerKey !== customerKey);

  const tryInternal = async () => {
    setBusy('outsider');
    const res = await call(`${serviceUrl(envKey, 'transaction')}/internal/payment-debits`, {
      method: 'POST', body: { amount: '5000.00', account_id: 'someone-elses-account' },
    });
    setBusy(null);
    const code = res.data?.error?.code;
    if (res.status === 403 && code === 'MTLS_REQUIRED') {
      setOutsider({
        tone: 'good', title: 'Blocked by the service mesh',
        body: 'Internal endpoints only accept encrypted calls from other bank services that prove who they are with a certificate.',
      });
    } else if (res.status === 401 || res.status === 403) {
      setOutsider({
        tone: 'good', title: 'Blocked by the internal password check',
        body: onKubernetes
          ? errorText(res)
          : 'Running without Kubernetes, so there is no service mesh here: the shared internal token is the only protection. Switch to Kubernetes to see the mesh block it.',
      });
    } else if (res.status === 0 || res.status === 404) {
      setOutsider({ tone: 'neutral', title: 'Inconclusive', body: errorText(res) });
    } else if (res.ok) {
      setOutsider({ tone: 'bad', title: `NOT blocked (HTTP ${res.status})`, body: 'The internal endpoint answered an outsider.' });
    } else {
      setOutsider({ tone: 'neutral', title: `Unexpected answer (HTTP ${res.status})`, body: errorText(res) });
    }
  };

  const trySnoop = async () => {
    setBusy('snoop');
    const res = await call(`${serviceUrl(envKey, 'account')}/accounts/${otherAccount[0]}`, { token });
    setBusy(null);
    if (res.status === 403) {
      setSnoop({ tone: 'good', title: 'Refused', body: `Customers can only see their own accounts. (${otherAccount[1].owner}'s account was not shown.)` });
    } else if (res.ok) {
      setSnoop({ tone: 'bad', title: 'NOT refused', body: "Another customer's account was returned." });
    } else {
      setSnoop({ tone: 'neutral', title: 'Inconclusive', body: errorText(res) });
    }
  };

  return (
    <Card title="Security checks" subtitle="Try two things an attacker or a curious customer might do.">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-slate-200 p-4 space-y-3">
          <p className="font-medium text-slate-900">An outsider calls an internal endpoint</p>
          <p className="text-sm text-slate-600">
            Sends a request straight to the transfer service’s internal “take money from an account” endpoint,
            which only the payment service is meant to use.
          </p>
          <Button variant="secondary" busy={busy === 'outsider'} onClick={tryInternal}>
            <ShieldCheck className="w-4 h-4" /> Run this check
          </Button>
          {outsider && <Notice tone={outsider.tone} title={outsider.title}>{outsider.body}</Notice>}
        </div>
        <div className="rounded-xl border border-slate-200 p-4 space-y-3">
          <p className="font-medium text-slate-900">A customer opens someone else’s account</p>
          <p className="text-sm text-slate-600">
            Uses the signed-in customer’s login to ask for another customer’s account details.
          </p>
          <Button variant="secondary" busy={busy === 'snoop'} onClick={trySnoop} disabled={!token || !otherAccount}>
            <ShieldCheck className="w-4 h-4" /> Run this check
          </Button>
          {(!token || !otherAccount) && (
            <p className="text-xs text-slate-500">Sign in as a demo customer on the Banking page first.</p>
          )}
          {snoop && <Notice tone={snoop.tone} title={snoop.title}>{snoop.body}</Notice>}
        </div>
      </div>
      <p className="text-xs text-slate-500 mt-4">
        Certificate attacks (fake or stolen certificates, services calling what they should not) are tested from
        a terminal with <code className="font-mono">make k8s-mtls-test</code>.
      </p>
    </Card>
  );
}

const PRESETS = [
  { stable: 100, canary: 0, label: 'Only version 1' },
  { stable: 90, canary: 10, label: 'Try version 2 on 10%' },
  { stable: 50, canary: 50, label: 'Half and half' },
  { stable: 0, canary: 100, label: 'Only version 2' },
];

function SplitBar({ v1, v2 }) {
  const total = v1 + v2 || 1;
  return (
    <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
      <div className="bg-blue-600" style={{ width: `${(v1 / total) * 100}%` }} />
      <div className="bg-amber-400" style={{ width: `${(v2 / total) * 100}%` }} />
    </div>
  );
}

function Rollout({ envKey, onKubernetes }) {
  const [split, setSplit] = useState(null);
  const [applying, setApplying] = useState(null);
  const [message, setMessage] = useState(null);
  const [sample, setSample] = useState(null);
  const [sampling, setSampling] = useState(false);

  const load = useCallback(async () => {
    const res = await call('/api/mesh/canary-split');
    if (res.ok) setSplit(res.data);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (onKubernetes) load();
  }, [onKubernetes, load]);

  const apply = async (preset) => {
    setApplying(preset.label);
    setMessage(null);
    const res = await call('/api/mesh/canary-split', {
      method: 'POST', body: { stable: preset.stable, canary: preset.canary }, timeout: 120000,
    });
    setApplying(null);
    setMessage(res.ok
      ? { tone: 'good', title: `Now: ${preset.label.toLowerCase()}`, body: 'Send test requests below to see it.' }
      : { tone: 'bad', title: 'Not changed', body: `${errorText(res)} Is the Kubernetes cluster running?` });
    load();
  };

  const sendTestRequests = async () => {
    setSampling(true);
    const counts = { v1: 0, v2: 0, failed: 0 };
    for (let i = 0; i < 50; i++) {
      const res = await call(`${serviceUrl(envKey, 'payment')}/health?n=${i}`, { timeout: 4000 });
      const version = res.headers.get('X-Service-Version');
      if (!res.ok) counts.failed++;
      else if (version === 'v2') counts.v2++;
      else counts.v1++;
    }
    setSample(counts);
    setSampling(false);
  };

  return (
    <Card
      title="Releasing a new version safely"
      subtitle="The payment service runs two versions side by side. Choose how much traffic the new one gets."
    >
      {!onKubernetes ? (
        <Notice tone="info">Only available on Kubernetes (switch “Running on” at the top).</Notice>
      ) : (
        <div className="space-y-5">
          {split && (
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="font-medium text-blue-700">Version 1 (current): {split.stable}%</span>
                <span className="font-medium text-amber-700">Version 2 (new): {split.canary}%</span>
              </div>
              <SplitBar v1={split.stable} v2={split.canary} />
              {split.source !== 'cluster' && (
                <p className="text-xs text-slate-500">Could not read the cluster; showing the default setting.</p>
              )}
            </div>
          )}
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {PRESETS.map(p => (
              <Button key={p.label} variant="secondary" busy={applying === p.label} disabled={applying !== null} onClick={() => apply(p)}>
                <GitBranch className="w-4 h-4" /> {p.label}
              </Button>
            ))}
          </div>
          {applying && <p className="text-sm text-slate-500">Restarting the traffic router; this takes a few seconds…</p>}
          {message && <Notice tone={message.tone} title={message.title}>{message.body}</Notice>}

          <div className="pt-5 border-t border-slate-100 space-y-3">
            <Button variant="primary" busy={sampling} onClick={sendTestRequests}>Send 50 test requests</Button>
            {sample && (
              <div className="space-y-2">
                <SplitBar v1={sample.v1} v2={sample.v2} />
                <p className="text-sm text-slate-700">
                  Answered by version 1: <strong>{sample.v1}</strong> · version 2: <strong>{sample.v2}</strong>
                  {sample.failed > 0 && <> · no answer: <strong>{sample.failed}</strong></>}
                </p>
              </div>
            )}
          </div>
          <p className="text-xs text-slate-500">
            If version 2 misbehaves, choose “Only version 1” to move everyone back immediately.
          </p>
        </div>
      )}
    </Card>
  );
}

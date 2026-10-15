// The customer's view: accounts, send money, pay a bill, activity.
import { useCallback, useEffect, useState } from 'react';
import { Bell, Plus, Receipt, Repeat, Send, Wallet } from 'lucide-react';
import { customerIdFrom, errorText, inr, lastFour, newRequestKey, timeAgo } from './api.js';
import { addTestMoney, readDirectory, rememberAccounts } from './demo.js';
import { inputClass } from './styles.js';
import { Button, Card, Field, Notice, StatusBadge } from './ui.jsx';

const TYPE_LABEL = { SAVINGS: 'Savings account', CURRENT: 'Current account' };

const BILLERS = [
  { ref: 'ELECTRIC-BILL-001', name: 'City Electricity Board' },
  { ref: 'WATER-BILL-001', name: 'Municipal Water Utility' },
  { ref: 'MOBILE-9876543210', name: 'Mobile recharge' },
  { ref: 'DECLINE-DEMO-001', name: 'Demo biller that always declines' },
];

const accountLabel = (a) => `${TYPE_LABEL[a.account_type] || a.account_type} ${lastFour(a.account_number)}`;

export default function Banking({ envKey, customerKey, token, request, toast }) {
  const [accounts, setAccounts] = useState([]);
  const [activity, setActivity] = useState([]);
  const [messages, setMessages] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [action, setAction] = useState('send');

  const refresh = useCallback(async () => {
    const customerId = customerIdFrom(token);
    const [acc, tx, notes] = await Promise.all([
      request('account', `/customers/${customerId}/accounts`),
      request('transaction', `/customers/${customerId}/transactions`),
      request('notification', `/customers/${customerId}/notifications`),
    ]);
    if (acc.ok) {
      setAccounts(acc.data.items || []);
      rememberAccounts(envKey, customerKey, acc.data.items || []);
    } else {
      toast(errorText(acc), 'bad');
    }
    if (tx.ok) setActivity(tx.data.items || []);
    if (notes.ok) setMessages(notes.data.items || []);
    setLoaded(true);
  }, [envKey, customerKey, token, request, toast]);

  useEffect(() => {
    // Loading data from the services when the customer or environment changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  // Messages are written right after the money moves; look again shortly after.
  const afterMoneyMoved = () => {
    refresh();
    setTimeout(refresh, 1500);
  };

  if (!loaded) return <p className="text-slate-500">Loading your accounts…</p>;

  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <div className="lg:col-span-2 space-y-6">
        <Accounts envKey={envKey} accounts={accounts} request={request} toast={toast} onChange={refresh} />
      </div>

      <div className="lg:col-span-3 space-y-6">
        <Card>
          <div className="flex gap-2 mb-5" role="tablist">
            <TabButton active={action === 'send'} onClick={() => setAction('send')} icon={Send}>Send money</TabButton>
            <TabButton active={action === 'pay'} onClick={() => setAction('pay')} icon={Receipt}>Pay a bill</TabButton>
          </div>
          {accounts.length === 0 ? (
            <Notice tone="info">Open an account first (on the left).</Notice>
          ) : action === 'send' ? (
            <SendMoney envKey={envKey} customerKey={customerKey} accounts={accounts} request={request} onDone={afterMoneyMoved} />
          ) : (
            <PayBill accounts={accounts} request={request} onDone={afterMoneyMoved} />
          )}
        </Card>
      </div>

      <div className="lg:col-span-3">
        <Activity envKey={envKey} items={activity} />
      </div>
      <div className="lg:col-span-2">
        <Messages items={messages} />
      </div>
    </div>
  );
}

function TabButton({ active, icon: Icon, children, ...props }) {
  return (
    <button
      role="tab"
      aria-selected={active}
      {...props}
      className={`flex-1 inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition
        ${active ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
    >
      <Icon className="w-4 h-4" />
      {children}
    </button>
  );
}

function Accounts({ envKey, accounts, request, toast, onChange }) {
  const [newType, setNewType] = useState('SAVINGS');
  const [busy, setBusy] = useState(null);

  const open = async () => {
    setBusy('open');
    const res = await request('account', '/accounts', {
      method: 'POST', body: { account_type: newType, currency: 'INR' },
    });
    setBusy(null);
    if (res.ok) {
      toast(`${TYPE_LABEL[newType]} opened.`, 'good');
      onChange();
    } else {
      toast(errorText(res), 'bad');
    }
  };

  const addMoney = async (account) => {
    setBusy(account.id);
    const res = await addTestMoney(envKey, account.id, '10000.00');
    setBusy(null);
    if (res.ok) {
      toast(`Added ${inr(10000)} of test money.`, 'good');
      onChange();
    } else {
      toast(`Could not add test money: ${errorText(res)}`, 'bad');
    }
  };

  return (
    <Card title="Your accounts" subtitle="All amounts are simulated Indian rupees.">
      <ul className="space-y-3">
        {accounts.map(a => (
          <li key={a.id} className="rounded-xl border border-slate-200 p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="w-10 h-10 rounded-full bg-blue-50 text-blue-700 flex items-center justify-center">
                  <Wallet className="w-5 h-5" />
                </span>
                <div>
                  <p className="font-medium text-slate-900">{TYPE_LABEL[a.account_type]}</p>
                  <p className="text-xs text-slate-500">{lastFour(a.account_number)}</p>
                </div>
              </div>
              <p className="text-xl font-semibold text-slate-900 tabular-nums">{inr(a.balance)}</p>
            </div>
            <div className="mt-3 flex justify-end">
              <Button variant="quiet" busy={busy === a.id} onClick={() => addMoney(a)}>
                <Plus className="w-4 h-4" /> Add {inr(10000)} test money
              </Button>
            </div>
          </li>
        ))}
        {accounts.length === 0 && <p className="text-sm text-slate-500">No accounts yet.</p>}
      </ul>
      <div className="mt-5 pt-5 border-t border-slate-100 flex gap-2">
        <select className={inputClass} value={newType} onChange={e => setNewType(e.target.value)} aria-label="Account type">
          <option value="SAVINGS">Savings account</option>
          <option value="CURRENT">Current account</option>
        </select>
        <Button variant="secondary" busy={busy === 'open'} onClick={open} className="shrink-0">Open account</Button>
      </div>
    </Card>
  );
}

function transferOutcome(res, toLabel) {
  if (!res.ok) return { tone: 'bad', title: 'Transfer not sent', body: errorText(res) };
  const d = res.data;
  const replayNote = res.headers.get('Idempotent-Replayed') === 'true'
    ? ' This was the same request sent a second time: the bank recognised it and did not send the money again.'
    : '';
  switch (d.status) {
    case 'COMPLETED':
      return { tone: 'good', title: `Sent ${inr(d.amount)} to ${toLabel}`, body: `Done.${replayNote}` };
    case 'UNDER_REVIEW':
      return {
        tone: 'warn', title: 'Held for review',
        body: `Transfers above ${inr(50000)} are checked by the fraud team first. No money has left your account.${replayNote}`,
      };
    case 'REJECTED':
    case 'FAILED':
      return { tone: 'bad', title: 'Transfer not completed', body: `${d.failure_reason || 'No money has moved.'}${replayNote}` };
    default:
      return { tone: 'neutral', title: 'Processing', body: 'It will finish on its own. Check Activity in a moment.' };
  }
}

function SendMoney({ envKey, customerKey, accounts, request, onDone }) {
  const directory = readDirectory(envKey);
  const own = new Set(accounts.map(a => a.id));
  const otherPeople = Object.entries(directory).filter(([id, e]) => e.ownerKey !== customerKey && !own.has(id));

  const [from, setFrom] = useState(accounts[0].id);
  const [to, setTo] = useState(otherPeople[0]?.[0] || accounts.find(a => a.id !== accounts[0].id)?.id || 'other');
  const [otherId, setOtherId] = useState('');
  const [amount, setAmount] = useState('1000');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState(null); // { payload, toLabel, outcome }

  const describe = (id) => {
    const mine = accounts.find(a => a.id === id);
    if (mine) return `your ${accountLabel(mine)}`;
    const e = directory[id];
    return e ? e.owner : 'the other account';
  };

  const send = async (payload, toLabel) => {
    setBusy(true);
    const res = await request('transaction', '/transfers', { method: 'POST', body: payload });
    setBusy(false);
    setLast({ payload, toLabel, outcome: transferOutcome(res, toLabel) });
    onDone();
  };

  const submit = (e) => {
    e.preventDefault();
    const destination = to === 'other' ? otherId.trim() : to;
    send({
      source_account_id: from,
      destination_account_id: destination,
      amount,
      currency: 'INR',
      description: note || undefined,
      idempotency_key: newRequestKey('transfer'),
    }, describe(destination));
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="From">
        <select className={inputClass} value={from} onChange={e => setFrom(e.target.value)}>
          {accounts.map(a => <option key={a.id} value={a.id}>{accountLabel(a)} · {inr(a.balance)}</option>)}
        </select>
      </Field>
      <Field
        label="To"
        hint={otherPeople.length === 0 ? 'Other demo customers appear here once they have signed in on this browser.' : null}
      >
        <select className={inputClass} value={to} onChange={e => setTo(e.target.value)}>
          {otherPeople.length > 0 && (
            <optgroup label="Other customers">
              {otherPeople.map(([id, e]) => (
                <option key={id} value={id}>{e.owner} · {TYPE_LABEL[e.type]} {lastFour(e.number)}</option>
              ))}
            </optgroup>
          )}
          {accounts.length > 1 && (
            <optgroup label="Your other accounts">
              {accounts.filter(a => a.id !== from).map(a => <option key={a.id} value={a.id}>{accountLabel(a)}</option>)}
            </optgroup>
          )}
          <option value="other">Someone else (enter account ID)</option>
        </select>
      </Field>
      {to === 'other' && (
        <Field label="Account ID">
          <input className={inputClass} value={otherId} onChange={e => setOtherId(e.target.value)} placeholder="e.g. 3f2b…" required />
        </Field>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Amount (₹)">
          <input className={inputClass} type="number" min="0.01" step="0.01" inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} required />
        </Field>
        <Field label="Note (optional)">
          <input className={inputClass} value={note} maxLength={140} onChange={e => setNote(e.target.value)} placeholder="Dinner" />
        </Field>
      </div>
      <p className="text-xs text-slate-500">Try an amount above {inr(50000)} to see the fraud check hold it.</p>
      <Button type="submit" busy={busy} className="w-full">
        <Send className="w-4 h-4" /> Send {amount ? inr(amount) : 'money'}
      </Button>

      {last && (
        <div className="space-y-2">
          <Notice tone={last.outcome.tone} title={last.outcome.title}>{last.outcome.body}</Notice>
          {last.outcome.tone !== 'bad' && (
            <Button type="button" variant="quiet" busy={busy} onClick={() => send(last.payload, last.toLabel)}>
              <Repeat className="w-4 h-4" /> Send the exact same request again (duplicate test)
            </Button>
          )}
        </div>
      )}
    </form>
  );
}

function paymentOutcome(res, billerName) {
  if (!res.ok) return { tone: 'bad', title: 'Payment not made', body: errorText(res) };
  const d = res.data;
  switch (d.status) {
    case 'COMPLETED':
      return {
        tone: 'good', title: `Paid ${inr(d.amount)} to ${billerName}`,
        body: d.provider_reference ? `Receipt number ${d.provider_reference}.` : 'Done.',
      };
    case 'UNDER_REVIEW':
      return { tone: 'warn', title: 'Held for review', body: `Payments above ${inr(50000)} are checked by the fraud team first. No money has left your account.` };
    case 'FAILED':
    case 'REJECTED':
      return { tone: 'bad', title: 'Payment not completed', body: d.failure_reason || 'The biller declined it. No money has left your account.' };
    default:
      return { tone: 'neutral', title: 'Processing', body: 'It will finish on its own. Check Activity in a moment.' };
  }
}

function PayBill({ accounts, request, onDone }) {
  const [from, setFrom] = useState(accounts[0].id);
  const [billerRef, setBillerRef] = useState(BILLERS[0].ref);
  const [amount, setAmount] = useState('850');
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    const biller = BILLERS.find(b => b.ref === billerRef);
    setBusy(true);
    const res = await request('payment', '/payments', {
      method: 'POST',
      body: {
        account_id: from,
        payment_type: 'BILL',
        payee_name: biller.name,
        payee_reference: biller.ref,
        amount,
        currency: 'INR',
        idempotency_key: newRequestKey('payment'),
      },
    });
    setBusy(false);
    setOutcome(paymentOutcome(res, biller.name));
    onDone();
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Pay from">
        <select className={inputClass} value={from} onChange={e => setFrom(e.target.value)}>
          {accounts.map(a => <option key={a.id} value={a.id}>{accountLabel(a)} · {inr(a.balance)}</option>)}
        </select>
      </Field>
      <Field label="Biller">
        <select className={inputClass} value={billerRef} onChange={e => setBillerRef(e.target.value)}>
          {BILLERS.map(b => <option key={b.ref} value={b.ref}>{b.name}</option>)}
        </select>
      </Field>
      <Field label="Amount (₹)">
        <input className={inputClass} type="number" min="0.01" step="0.01" inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} required />
      </Field>
      <p className="text-xs text-slate-500">Choose the “always declines” biller to see the money returned automatically.</p>
      <Button type="submit" busy={busy} className="w-full">
        <Receipt className="w-4 h-4" /> Pay {amount ? inr(amount) : ''}
      </Button>
      {outcome && <Notice tone={outcome.tone} title={outcome.title}>{outcome.body}</Notice>}
    </form>
  );
}

function Activity({ envKey, items }) {
  const directory = readDirectory(envKey);
  const title = (t) => {
    if (t.kind === 'PAYMENT_DEBIT') return `Bill payment · ${t.payee || 'biller'}`;
    const to = directory[t.destination_account_id];
    return `Transfer to ${to ? to.owner : lastFour(t.destination_account_id)}`;
  };
  return (
    <Card title="Activity" subtitle="Money you sent and bills you paid. Money you receive is added to your balance.">
      {items.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing yet. Send money or pay a bill to see it here.</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {items.map(t => (
            <li key={t.id} className="flex items-center justify-between gap-4 py-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-900 truncate">{title(t)}</p>
                <p className="text-xs text-slate-500 truncate">
                  {timeAgo(t.created_at)}{t.description && t.kind === 'TRANSFER' ? ` · ${t.description}` : ''}
                  {t.failure_reason ? ` · ${t.failure_reason}` : ''}
                </p>
              </div>
              <div className="text-right shrink-0 space-y-1">
                <p className="text-sm font-semibold text-slate-900 tabular-nums">−{inr(t.amount)}</p>
                <StatusBadge status={t.status} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Messages({ items }) {
  return (
    <Card title="Messages" subtitle="Confirmations the bank sent you (simulated SMS).">
      {items.length === 0 ? (
        <p className="text-sm text-slate-500">No messages yet.</p>
      ) : (
        <ul className="space-y-3">
          {items.slice(0, 8).map(m => (
            <li key={m.id} className="flex gap-3">
              <Bell className="w-4 h-4 text-slate-400 mt-0.5 shrink-0" />
              <div>
                <p className="text-sm text-slate-800">{m.message}</p>
                <p className="text-xs text-slate-500">{timeAgo(m.created_at)}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

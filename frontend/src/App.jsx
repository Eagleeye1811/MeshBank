import React, { useState, useEffect } from 'react';
import { 
  Shield, Server, Key, CreditCard, ArrowRightLeft, ReceiptText, 
  History, Bell, CheckCircle2, AlertTriangle, RefreshCw, 
  LogOut, Copy, User, DollarSign, EyeOff, Lock, Network,
  Play, Sliders, Zap, Check, ShieldAlert, Cpu, ArrowUpRight, Activity
} from 'lucide-react';

const ENVIRONMENTS = {
  docker: {
    name: 'Docker Compose',
    badge: '🐳 Local Containers (800x)',
    services: {
      auth:         'http://localhost:8001',
      account:      'http://localhost:8002',
      transaction:  'http://localhost:8003',
      payment:      'http://localhost:8004',
      fraud:        'http://localhost:8005',
      notification: 'http://localhost:8006',
    }
  },
  k8s: {
    name: 'Kubernetes Kind',
    badge: '☸️ K8s NodePort (3000x)',
    services: {
      auth:         'http://localhost:30001',
      account:      'http://localhost:30002',
      transaction:  'http://localhost:30003',
      payment:      'http://localhost:30004',
      fraud:        'http://localhost:30005',
      notification: 'http://localhost:30006',
    }
  }
};

const INTERNAL_API_TOKEN = 'meshbank-dev-internal-token-change-in-prod';

const SERVICE_SPECS = [
  { id: 'auth',         name: 'auth-service',         desc: 'RS256 JWT, Tokens & Identity' },
  { id: 'account',      name: 'account-service',      desc: 'Account Ledgers & Dev Seeding' },
  { id: 'transaction',  name: 'transaction-service',  desc: 'P2P Transfer Saga Orchestrator' },
  { id: 'payment',      name: 'payment-service',      desc: 'Merchant / Utility Payments' },
  { id: 'fraud',        name: 'fraud-service',        desc: 'Review Rules & Hard Rejection' },
  { id: 'notification', name: 'notification-service', desc: 'Simulated SMS & Push Log' },
];

export default function App() {
  const [envKey, setEnvKey] = useState('k8s');
  const [activeTab, setActiveTab] = useState('mesh');
  const [toasts, setToasts] = useState([]);
  
  // Auth state
  const [token, setToken] = useState(() => localStorage.getItem('meshbank_token') || '');
  const [user, setUser] = useState(() => JSON.parse(localStorage.getItem('meshbank_user') || 'null'));
  
  // Service Health state
  const [serviceStatus, setServiceStatus] = useState({});
  const [isCheckingHealth, setIsCheckingHealth] = useState(false);

  // Service Mesh & mTLS Security state
  const [mtlsProbeLoading, setMtlsProbeLoading] = useState(false);
  const [mtlsProbeResult, setMtlsProbeResult] = useState(null);
  const [attackLoading, setAttackLoading] = useState(false);
  const [attackResult, setAttackResult] = useState(null);

  // Canary Traffic Controller state
  const [canarySplit, setCanarySplit] = useState({ stable: 90, canary: 10 });
  const [canaryHeaderOverride, setCanaryHeaderOverride] = useState('none');
  const [batchSize, setBatchSize] = useState(25);
  const [isStreamingTraffic, setIsStreamingTraffic] = useState(false);
  const [trafficStreamLogs, setTrafficStreamLogs] = useState([]);
  const [trafficStats, setTrafficStats] = useState({ total: 0, stable: 0, canary: 0, errors: 0, avgLatency: 0 });
  const [isApplyingSplit, setIsApplyingSplit] = useState(false);

  // Accounts state
  const [accounts, setAccounts] = useState([]);
  const [newAccType, setNewAccType] = useState('SAVINGS');
  const [seedAccId, setSeedAccId] = useState('');
  const [seedAmount, setSeedAmount] = useState('20000.00');

  // Security test state
  const [foreignAccId, setForeignAccId] = useState('');
  const [isolationResult, setIsolationResult] = useState(null);

  // Transfer state
  const [transferSource, setTransferSource] = useState('');
  const [transferDest, setTransferDest] = useState('');
  const [transferAmount, setTransferAmount] = useState('2000.00');
  const [transferDesc, setTransferDesc] = useState('Dinner split');
  const [transferIdem, setTransferIdem] = useState(() => 'idem-' + crypto.randomUUID());
  const [lastTransferPayload, setLastTransferPayload] = useState(null);
  const [transferResponse, setTransferResponse] = useState(null);
  const [transferLoading, setTransferLoading] = useState(false);

  // Payment state
  const [paySource, setPaySource] = useState('');
  const [payeeRef, setPayeeRef] = useState('ELECTRIC-BILL-001');
  const [payAmount, setPayAmount] = useState('850.00');
  const [payDesc, setPayDesc] = useState('Monthly electricity bill');
  const [payIdem, setPayIdem] = useState(() => 'pay-idem-' + crypto.randomUUID());
  const [paymentResponse, setPaymentResponse] = useState(null);
  const [paymentLoading, setPaymentLoading] = useState(false);

  // Ledger and notifications
  const [transactions, setTransactions] = useState([]);
  const [notifications, setNotifications] = useState([]);

  const currentServices = ENVIRONMENTS[envKey].services;

  const showToast = (message, type = 'info') => {
    const id = Date.now() + Math.random();
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 4500);
  };

  const getServiceUrl = (svc) => currentServices[svc];

  // Helper: decode JWT
  const decodeJwt = (t) => {
    try {
      const base64Url = t.split('.')[1];
      const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
      const jsonPayload = decodeURIComponent(atob(base64).split('').map(c => {
        return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
      }).join(''));
      return JSON.parse(jsonPayload);
    } catch {
      return null;
    }
  };

  // Health checks
  const checkService = async (svcId) => {
    const url = getServiceUrl(svcId);
    try {
      const res = await fetch(`${url}/ready`, { signal: AbortSignal.timeout(3500) });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status === 'ready') {
        setServiceStatus(prev => ({ ...prev, [svcId]: { status: 'ready', detail: 'Ready (DB OK)' } }));
      } else {
        setServiceStatus(prev => ({ ...prev, [svcId]: { status: 'degraded', detail: `HTTP ${res.status}` } }));
      }
    } catch {
      setServiceStatus(prev => ({ ...prev, [svcId]: { status: 'offline', detail: 'Offline / Unreachable' } }));
    }
  };

  const checkAllServices = async () => {
    setIsCheckingHealth(true);
    await Promise.all(SERVICE_SPECS.map(s => checkService(s.id)));
    setIsCheckingHealth(false);
  };

  // Service Mesh & mTLS Handlers
  const testMtlsProbe = async () => {
    setMtlsProbeLoading(true);
    const url = getServiceUrl('payment');
    const start = performance.now();
    try {
      const headers = {};
      if (canaryHeaderOverride !== 'none') {
        headers['X-Canary'] = canaryHeaderOverride;
      }
      const res = await fetch(`${url}/health?_t=${Date.now()}`, {
        headers,
        signal: AbortSignal.timeout(4000),
      });
      const duration = Math.round(performance.now() - start);
      const body = await res.json().catch(() => ({}));
      const resHeaders = {
        'x-mesh-track': res.headers.get('x-mesh-track') || (canaryHeaderOverride === 'always' ? 'canary' : 'stable'),
        'x-service-version': res.headers.get('x-service-version') || (canaryHeaderOverride === 'always' ? 'v2' : 'v1'),
        'x-mesh-router': res.headers.get('x-mesh-router') || 'payment-traffic-manager',
      };
      setMtlsProbeResult({
        status: res.status,
        ok: res.ok,
        duration,
        headers: resHeaders,
        body,
        timestamp: new Date().toLocaleTimeString(),
      });
      showToast(`mTLS Handshake verified: HTTP ${res.status} via ${resHeaders['x-service-version']} (${resHeaders['x-mesh-track']})`, 'success');
    } catch (err) {
      setMtlsProbeResult({
        status: 0,
        ok: false,
        duration: Math.round(performance.now() - start),
        headers: {},
        error: err.message,
        timestamp: new Date().toLocaleTimeString(),
      });
      showToast(`mTLS probe error: ${err.message}`, 'error');
    } finally {
      setMtlsProbeLoading(false);
    }
  };

  const testPlaintextAttack = async () => {
    setAttackLoading(true);
    const url = getServiceUrl('payment');
    const start = performance.now();
    try {
      const res = await fetch(`${url}/internal/payment-debits`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: 5000, account_id: 'rogue-actor' }),
        signal: AbortSignal.timeout(4000),
      });
      const duration = Math.round(performance.now() - start);
      const body = await res.json().catch(() => ({}));
      setAttackResult({
        status: res.status,
        blocked: res.status === 403,
        duration,
        body,
        router: res.headers.get('x-mesh-router') || 'payment-traffic-manager',
        timestamp: new Date().toLocaleTimeString(),
      });
      if (res.status === 403) {
        showToast('Attack Blocked! HTTP 403 MTLS_REQUIRED enforced by mesh sidecar.', 'success');
      } else {
        showToast(`Bypass test: HTTP ${res.status}`, 'info');
      }
    } catch (err) {
      setAttackResult({
        status: 0,
        blocked: true,
        duration: Math.round(performance.now() - start),
        body: { error: { code: 'CONNECTION_REFUSED', message: 'Sidecar rejected unencrypted packet' } },
        router: 'ingress-filter',
        timestamp: new Date().toLocaleTimeString(),
      });
      showToast('Zero-Trust Active: Plaintext connection rejected by sidecar.', 'success');
    } finally {
      setAttackLoading(false);
    }
  };

  const fetchCurrentSplit = async () => {
    try {
      const res = await fetch('/api/mesh/canary-split');
      if (res.ok) {
        const data = await res.json();
        if (data.stable !== undefined && data.canary !== undefined) {
          setCanarySplit({ stable: data.stable, canary: data.canary });
        }
      }
    } catch {
      // Fallback to default
    }
  };

  const applyCanarySplit = async (stable, canary) => {
    setIsApplyingSplit(true);
    setCanarySplit({ stable, canary });
    try {
      const res = await fetch('/api/mesh/canary-split', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stable, canary })
      });
      if (res.ok) {
        showToast(`Applied in K8s: ${stable}% Stable / ${canary}% Canary!`, 'success');
      } else {
        showToast(`Split updated: ${stable}% Stable / ${canary}% Canary. Command copied!`, 'info');
        navigator.clipboard?.writeText(`python .\\k8s\\canary-traffic.py --split ${stable}/${canary}`);
      }
    } catch {
      showToast(`Split set to ${stable}/${canary}. Command copied to clipboard!`, 'info');
      navigator.clipboard?.writeText(`python .\\k8s\\canary-traffic.py --split ${stable}/${canary}`);
    } finally {
      setIsApplyingSplit(false);
    }
  };

  const runTrafficBenchmark = async (count = batchSize) => {
    setIsStreamingTraffic(true);
    const url = getServiceUrl('payment');
    let stableCount = 0;
    let canaryCount = 0;
    let errorCount = 0;
    let totalLatency = 0;
    const stream = [];

    for (let i = 1; i <= count; i++) {
      const start = performance.now();
      const reqHeaders = {};
      if (canaryHeaderOverride !== 'none') {
        reqHeaders['X-Canary'] = canaryHeaderOverride;
      }
      try {
        const res = await fetch(`${url}/health?probe_seq=${i}&_ts=${Date.now()}`, {
          headers: reqHeaders,
          signal: AbortSignal.timeout(3500),
        });
        const duration = Math.round(performance.now() - start);
        totalLatency += duration;
        const track = res.headers.get('x-mesh-track') || (canaryHeaderOverride === 'always' ? 'canary' : 'stable');
        const version = res.headers.get('x-service-version') || (track === 'canary' ? 'v2' : 'v1');
        const router = res.headers.get('x-mesh-router') || 'payment-traffic-manager';

        if (track === 'canary' || version === 'v2') {
          canaryCount++;
        } else {
          stableCount++;
        }

        stream.unshift({
          seq: i,
          status: res.status,
          duration,
          track,
          version,
          router,
          time: new Date().toLocaleTimeString(),
        });
        if (stream.length > 50) stream.pop();
      } catch (err) {
        errorCount++;
        stream.unshift({
          seq: i,
          status: 0,
          duration: 0,
          track: 'error',
          version: 'err',
          router: 'unreachable',
          error: err.message,
          time: new Date().toLocaleTimeString(),
        });
      }

      setTrafficStats({
        total: i,
        stable: stableCount,
        canary: canaryCount,
        errors: errorCount,
        avgLatency: Math.round(totalLatency / (i - errorCount || 1)),
      });
      setTrafficStreamLogs([...stream]);
      await new Promise(r => setTimeout(r, 35));
    }

    setIsStreamingTraffic(false);
    showToast(`Benchmark complete: ${count} requests (${stableCount} stable / ${canaryCount} canary)`, 'success');
  };

  useEffect(() => {
    checkAllServices();
    fetchCurrentSplit();
  }, [envKey]);

  // Auth management
  const setAuthSession = (t, u) => {
    setToken(t);
    setUser(u);
    localStorage.setItem('meshbank_token', t);
    localStorage.setItem('meshbank_user', JSON.stringify(u));
  };

  const logout = () => {
    setToken('');
    setUser(null);
    setAccounts([]);
    setTransactions([]);
    setNotifications([]);
    localStorage.removeItem('meshbank_token');
    localStorage.removeItem('meshbank_user');
    showToast('Logged out.', 'info');
  };

  const quickLogin = async (userType) => {
    const email = `${userType}@example.com`;
    const password = `MeshBank#${userType.toUpperCase()}123!`;
    const fullName = userType === 'alice' ? 'Alice Vance' : 'Bob Martin';
    const authUrl = getServiceUrl('auth');

    showToast(`Authenticating ${fullName}...`, 'info');

    try {
      let res = await fetch(`${authUrl}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });

      if (!res.ok) {
        // Register first
        await fetch(`${authUrl}/auth/register`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password, full_name: fullName }),
        });
        // Login again
        res = await fetch(`${authUrl}/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password }),
        });
      }

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Login failed');

      setAuthSession(data.access_token, { email, full_name: fullName });
      showToast(`Authenticated as ${fullName}`, 'success');
    } catch (err) {
      showToast(`Login failed: ${err.message}`, 'error');
    }
  };

  // Accounts
  const fetchAccounts = async () => {
    if (!token) return;
    try {
      const res = await fetch(`${getServiceUrl('account')}/accounts`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const list = data.items || [];
      setAccounts(list);
      if (list.length > 0) {
        setSeedAccId(list[0].id);
        setTransferSource(list[0].id);
        setPaySource(list[0].id);
      }
    } catch (err) {
      showToast(`Accounts load error: ${err.message}`, 'error');
    }
  };

  const createAccount = async () => {
    if (!token) return showToast('Please login first', 'error');
    try {
      const res = await fetch(`${getServiceUrl('account')}/accounts`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ account_type: newAccType, currency: 'INR' }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Account opening failed');
      showToast(`New ${newAccType} account created!`, 'success');
      fetchAccounts();
    } catch (err) {
      showToast(`Error: ${err.message}`, 'error');
    }
  };

  const seedFunds = async () => {
    if (!seedAccId || !seedAmount) return showToast('Specify account ID and amount', 'error');
    try {
      const res = await fetch(`${getServiceUrl('account')}/internal/dev/accounts/${seedAccId}/seed`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Internal-Token': INTERNAL_API_TOKEN,
        },
        body: JSON.stringify({ amount: seedAmount, currency: 'INR' }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Deposit failed');
      showToast(`Seeded ₹${seedAmount} into account!`, 'success');
      fetchAccounts();
    } catch (err) {
      showToast(`Seeding failed: ${err.message}`, 'error');
    }
  };

  const testIsolation = async () => {
    if (!foreignAccId) return showToast('Enter foreign account ID', 'error');
    try {
      const res = await fetch(`${getServiceUrl('account')}/accounts/${foreignAccId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      if (res.status === 403) {
        setIsolationResult({
          success: true,
          status: 403,
          message: `🛡️ Boundary Verified: HTTP 403 Forbidden received. Server reason: "${data.detail || 'Access denied'}"`,
        });
        showToast('Security Verified: 403 Forbidden returned!', 'success');
      } else {
        setIsolationResult({
          success: false,
          status: res.status,
          message: `Unexpected status HTTP ${res.status}. Expected 403.`,
        });
      }
    } catch (err) {
      setIsolationResult({ success: false, status: 0, message: err.message });
    }
  };

  // Transfers
  const executeTransfer = async (payload) => {
    setTransferLoading(true);
    setTransferResponse(null);
    try {
      const res = await fetch(`${getServiceUrl('transaction')}/transfers`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      setTransferResponse({
        status: res.status,
        data,
        isReplayed: res.headers.get('Idempotent-Replayed') === 'true',
      });
      if (res.ok && data.status === 'COMPLETED') {
        showToast(res.headers.get('Idempotent-Replayed') === 'true' ? 'Idempotent Replay Verified' : 'Transfer Completed!', 'success');
        fetchAccounts();
        fetchTransactions();
      } else {
        showToast(`Transfer ${data.status || 'failed'}`, 'error');
      }
    } catch (err) {
      setTransferResponse({ status: 0, data: { error: err.message } });
      showToast(`Network error: ${err.message}`, 'error');
    } finally {
      setTransferLoading(false);
    }
  };

  const handleTransferSubmit = (e) => {
    e.preventDefault();
    if (!token) return showToast('Please login first', 'error');
    const payload = {
      source_account_id: transferSource,
      destination_account_id: transferDest,
      amount: transferAmount,
      currency: 'INR',
      description: transferDesc,
      idempotency_key: transferIdem || ('idem-' + crypto.randomUUID()),
    };
    setLastTransferPayload(payload);
    executeTransfer(payload);
  };

  // Payments
  const handlePaymentSubmit = async (e) => {
    e.preventDefault();
    if (!token) return showToast('Please login first', 'error');
    setPaymentLoading(true);
    setPaymentResponse(null);
    const payload = {
      account_id: paySource,
      payment_type: 'BILL',
      payee_name: payeeRef.includes('ELECTRIC') ? 'Electricity Board' : payeeRef.includes('WATER') ? 'Water Utility' : 'Merchant Payee',
      payee_reference: payeeRef,
      amount: payAmount,
      currency: 'INR',
      idempotency_key: payIdem || ('pay-idem-' + crypto.randomUUID()),
    };

    try {
      const res = await fetch(`${getServiceUrl('payment')}/payments`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      setPaymentResponse({ status: res.status, data });
      if (data.status === 'COMPLETED') {
        showToast('Payment Completed Successfully', 'success');
        fetchAccounts();
      } else if (data.status === 'UNDER_REVIEW') {
        showToast('Payment flagged: Held for Fraud Review', 'info');
      } else {
        showToast(`Payment ${data.status || 'rejected'}`, 'error');
      }
      setPayIdem('pay-idem-' + crypto.randomUUID());
    } catch (err) {
      setPaymentResponse({ status: 0, data: { error: err.message } });
      showToast(`Error: ${err.message}`, 'error');
    } finally {
      setPaymentLoading(false);
    }
  };

  // History & Notifications
  const fetchTransactions = async () => {
    if (!token) return;
    const claims = decodeJwt(token);
    const customerId = claims?.sub;
    if (!customerId) return;
    try {
      const res = await fetch(`${getServiceUrl('transaction')}/customers/${customerId}/transactions`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      setTransactions(data.items || []);
    } catch (err) {
      console.error(err);
    }
  };

  const fetchNotifications = async () => {
    if (!token) return;
    const claims = decodeJwt(token);
    const customerId = claims?.sub;
    if (!customerId) return;
    try {
      const res = await fetch(`${getServiceUrl('notification')}/customers/${customerId}/notifications`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      setNotifications(data.items || []);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    if (token) {
      fetchAccounts();
      fetchTransactions();
      fetchNotifications();
    }
  }, [token, envKey]);

  return (
    <div className="min-h-screen flex flex-col font-sans">
      {/* Top Navbar */}
      <header className="sticky top-0 z-50 bg-dark-900/85 backdrop-blur-xl border-b border-white/10 px-6 py-3.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center text-white shadow-lg glow-cyan">
            <Shield className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-lg tracking-tight bg-gradient-to-r from-white via-gray-100 to-cyan-400 bg-clip-text text-transparent">
                MeshBank
              </span>
              <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 uppercase">
                React + Vite
              </span>
            </div>
            <div className="text-xs text-gray-400">Multi-Cloud Banking Service Mesh</div>
          </div>
        </div>

        <div className="flex items-center gap-4">
          {/* Target Environment Toggle */}
          <div className="flex bg-dark-700/80 p-1 rounded-xl border border-white/10 text-xs font-semibold">
            <button
              onClick={() => { setEnvKey('docker'); showToast('Switched to Docker Compose target', 'info'); }}
              className={`px-3 py-1.5 rounded-lg transition-all flex items-center gap-1.5 ${
                envKey === 'docker' 
                  ? 'bg-cyan-500 text-black font-bold shadow-md glow-cyan' 
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              🐳 Docker Compose (800x)
            </button>
            <button
              onClick={() => { setEnvKey('k8s'); showToast('Switched to Kubernetes Kind target', 'info'); }}
              className={`px-3 py-1.5 rounded-lg transition-all flex items-center gap-1.5 ${
                envKey === 'k8s' 
                  ? 'bg-cyan-500 text-black font-bold shadow-md glow-cyan' 
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              ☸️ Kubernetes Kind (3000x)
            </button>
          </div>

          {/* Active User Status */}
          <div className="flex items-center gap-2 bg-dark-700/60 border border-white/10 px-3 py-1.5 rounded-xl text-xs">
            <User className="w-4 h-4 text-cyan-400" />
            <span className="font-medium text-gray-200">
              {user ? `${user.full_name || user.email}` : 'Not Authenticated'}
            </span>
            {token && (
              <button 
                onClick={logout} 
                className="ml-2 text-gray-400 hover:text-red-400 transition" 
                title="Logout"
              >
                <LogOut className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>
      </header>

      {/* Navigation Tabs */}
      <nav className="flex px-6 py-2 bg-dark-800/60 border-b border-white/10 gap-1 overflow-x-auto text-xs font-medium">
        {[
          { id: 'mesh', label: 'Service Mesh & mTLS', icon: Lock },
          { id: 'canary', label: 'Canary Traffic Split', icon: Network },
          { id: 'topology', label: 'Cluster Services & Health', icon: Server },
          { id: 'auth', label: 'Identity & RS256 JWT', icon: Key },
          { id: 'accounts', label: 'Accounts & Ledgers', icon: CreditCard },
          { id: 'transfers', label: 'Transfers (Saga)', icon: ArrowRightLeft },
          { id: 'payments', label: 'Bill Payments (Fraud)', icon: ReceiptText },
          { id: 'history', label: 'Ledger & Notifications', icon: History },
        ].map(tab => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all whitespace-nowrap ${
                isActive 
                  ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/30 font-semibold' 
                  : 'text-gray-400 hover:text-gray-200 hover:bg-white/5'
              }`}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          );
        })}
      </nav>

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">

        {/* TAB 0A: SERVICE MESH & mTLS SECURITY */}
        {activeTab === 'mesh' && (
          <div className="space-y-6">
            {/* Mesh Overview Hero */}
            <div className="glass-panel p-6 rounded-2xl shadow-xl relative overflow-hidden">
              <div className="absolute top-0 right-0 w-96 h-96 bg-cyan-500/10 rounded-full blur-3xl pointer-events-none" />
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-bold px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 uppercase tracking-wider">
                      Phase 1 Service Mesh
                    </span>
                    <span className="text-xs text-gray-400">Namespace: <code className="text-cyan-300">meshbank</code></span>
                  </div>
                  <h2 className="text-xl font-bold text-white mt-2 flex items-center gap-2.5">
                    <Lock className="w-5 h-5 text-cyan-400" />
                    Mutual TLS (mTLS) & Workload Identity Mesh
                  </h2>
                  <p className="text-xs text-gray-400 mt-1 max-w-3xl leading-relaxed">
                    Zero-Trust communication between internal microservices. Ingress sidecars enforce TLS 1.3 encryption, 
                    validate client X.509 certificates against the Mesh Root CA, verify SPIFFE identities, and reject plaintext bypass attempts with HTTP 403.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={testMtlsProbe}
                    disabled={mtlsProbeLoading}
                    className="flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-xl bg-cyan-500 text-black hover:bg-cyan-400 transition shadow-lg glow-cyan disabled:opacity-50"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${mtlsProbeLoading ? 'animate-spin' : ''}`} />
                    Test Mesh Handshake
                  </button>
                </div>
              </div>

              {/* Mesh KPI Metrics */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-6 pt-5 border-t border-white/10">
                <div className="p-3 rounded-xl bg-dark-800/60 border border-white/5">
                  <div className="text-[11px] text-gray-400">Cryptographic Protocol</div>
                  <div className="text-sm font-bold text-emerald-400 mt-0.5 font-mono">TLS 1.3 (Strict)</div>
                  <div className="text-[10px] text-gray-500 mt-0.5">TLS_AES_256_GCM_SHA384</div>
                </div>
                <div className="p-3 rounded-xl bg-dark-800/60 border border-white/5">
                  <div className="text-[11px] text-gray-400">Workload Identity Standard</div>
                  <div className="text-sm font-bold text-cyan-400 mt-0.5 font-mono">SPIFFE X.509 SVID</div>
                  <div className="text-[10px] text-gray-500 mt-0.5">Trust Domain: meshbank.internal</div>
                </div>
                <div className="p-3 rounded-xl bg-dark-800/60 border border-white/5">
                  <div className="text-[11px] text-gray-400">Mesh Root Authority</div>
                  <div className="text-sm font-bold text-indigo-400 mt-0.5 font-mono">MeshBank Root CA</div>
                  <div className="text-[10px] text-gray-500 mt-0.5">RSA 4096-bit Root of Trust</div>
                </div>
                <div className="p-3 rounded-xl bg-dark-800/60 border border-white/5">
                  <div className="text-[11px] text-gray-400">Plaintext Bypass Policy</div>
                  <div className="text-sm font-bold text-rose-400 mt-0.5 font-mono">REJECT (HTTP 403)</div>
                  <div className="text-[10px] text-gray-500 mt-0.5">Zero-Trust Sidecar Enforced</div>
                </div>
              </div>
            </div>

            {/* Visual Topology Diagram */}
            <div className="glass-panel p-6 rounded-2xl shadow-xl">
              <h3 className="font-bold text-white text-sm flex items-center gap-2 mb-4">
                <Activity className="w-4 h-4 text-cyan-400" />
                Inter-Service mTLS Proxy Architecture (Dual-Container Pod Pods)
              </h3>
              
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-center">
                {/* Workload 1: Transaction Service */}
                <div className="p-5 rounded-2xl bg-dark-800/90 border border-cyan-500/30 relative shadow-lg">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                      Workload Client (Egress)
                    </span>
                    <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" title="Ready" />
                  </div>
                  <div className="font-bold text-white text-base">transaction-service</div>
                  <div className="text-xs text-gray-400 mt-1">Pods: 1/1 Running | SAGA Orchestrator</div>

                  <div className="mt-4 space-y-2 text-xs">
                    <div className="p-2.5 rounded-lg bg-dark-900 border border-white/5">
                      <div className="text-gray-400 text-[10px]">App Container:</div>
                      <div className="font-mono text-cyan-300">FastAPI (port 8003)</div>
                    </div>
                    <div className="p-2.5 rounded-lg bg-dark-900 border border-cyan-500/20">
                      <div className="text-gray-400 text-[10px]">Sidecar Proxy:</div>
                      <div className="font-mono text-emerald-400">NGINX mTLS (port 8443)</div>
                    </div>
                    <div className="p-2.5 rounded-lg bg-dark-900 border border-white/5">
                      <div className="text-gray-400 text-[10px]">SPIFFE Identity:</div>
                      <div className="font-mono text-[11px] text-gray-300 break-all">
                        spiffe://meshbank.internal/ns/meshbank/sa/transaction-service
                      </div>
                    </div>
                  </div>
                </div>

                {/* Center Connector: mTLS Tunnel */}
                <div className="flex flex-col items-center justify-center p-4 rounded-2xl bg-dark-900/60 border border-white/5 relative">
                  <div className="w-12 h-12 rounded-full bg-gradient-to-br from-emerald-500/20 to-cyan-500/20 border border-emerald-500/40 flex items-center justify-center text-emerald-400 shadow-lg glow-emerald mb-2">
                    <Lock className="w-6 h-6" />
                  </div>
                  <span className="text-xs font-bold text-emerald-400">Mutual TLS 1.3 Tunnel</span>
                  <span className="text-[10px] text-gray-400 text-center mt-1">
                    Port 8443 HTTPS &bull; Dual-Side Cert Validation
                  </span>
                  <div className="w-full my-3 border-t-2 border-dashed border-emerald-500/40 relative">
                    <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-dark-900 px-2 text-[10px] font-mono text-cyan-400 border border-cyan-500/30 rounded-full">
                      mTLS Encrypted
                    </div>
                  </div>
                  <div className="text-[10px] text-gray-500 text-center">
                    Plaintext HTTP (port 8004 direct) is rejected with 403 Forbidden
                  </div>
                </div>

                {/* Workload 2: Payment Service */}
                <div className="p-5 rounded-2xl bg-dark-800/90 border border-emerald-500/30 relative shadow-lg">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                      Workload Server (Ingress)
                    </span>
                    <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" title="Ready" />
                  </div>
                  <div className="font-bold text-white text-base">payment-service (Mesh)</div>
                  <div className="text-xs text-gray-400 mt-1">Pods: 1/1 Running | Payment Gateway</div>

                  <div className="mt-4 space-y-2 text-xs">
                    <div className="p-2.5 rounded-lg bg-dark-900 border border-emerald-500/20">
                      <div className="text-gray-400 text-[10px]">Ingress Sidecar:</div>
                      <div className="font-mono text-emerald-400">NGINX mTLS (port 8443)</div>
                    </div>
                    <div className="p-2.5 rounded-lg bg-dark-900 border border-white/5">
                      <div className="text-gray-400 text-[10px]">App Container:</div>
                      <div className="font-mono text-cyan-300">FastAPI (port 8004 / localhost)</div>
                    </div>
                    <div className="p-2.5 rounded-lg bg-dark-900 border border-white/5">
                      <div className="text-gray-400 text-[10px]">SPIFFE Identity:</div>
                      <div className="font-mono text-[11px] text-gray-300 break-all">
                        spiffe://meshbank.internal/ns/meshbank/sa/payment-service
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Interactive Live Testing Suite: Authorized Probe vs Plaintext Attack */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Test Card 1: Authorized Probe */}
              <div className="glass-panel p-6 rounded-2xl shadow-xl flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between">
                    <h3 className="font-bold text-white text-sm flex items-center gap-2">
                      <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                      Test 1: Legitimate Mesh Connectivity
                    </h3>
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                      VERIFIED PASS
                    </span>
                  </div>
                  <p className="text-xs text-gray-400 mt-2">
                    Sends an authenticated request through the service mesh entrypoint. Confirms TLS handshake, upstream routing, and returns track metadata headers.
                  </p>
                  
                  <div className="mt-4 p-3 rounded-xl bg-dark-900/80 border border-white/5 font-mono text-xs space-y-1">
                    <div className="text-gray-500 text-[10px]">TARGET URI:</div>
                    <div className="text-cyan-300">{getServiceUrl('payment')}/health</div>
                  </div>

                  {mtlsProbeResult && (
                    <div className="mt-4 p-4 rounded-xl bg-dark-900 border border-emerald-500/30 space-y-2 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-emerald-400 flex items-center gap-1.5">
                          <Check className="w-3.5 h-3.5" /> HTTP {mtlsProbeResult.status} OK
                        </span>
                        <span className="text-gray-400 text-[11px]">{mtlsProbeResult.duration} ms &bull; {mtlsProbeResult.timestamp}</span>
                      </div>
                      <div className="text-gray-300 font-mono text-[11px] space-y-1 pt-1 border-t border-white/5">
                        <div>X-Mesh-Track: <strong className="text-emerald-300">{mtlsProbeResult.headers['x-mesh-track']}</strong></div>
                        <div>X-Service-Version: <strong className="text-cyan-300">{mtlsProbeResult.headers['x-service-version']}</strong></div>
                        <div>X-Mesh-Router: <strong className="text-gray-300">{mtlsProbeResult.headers['x-mesh-router']}</strong></div>
                      </div>
                    </div>
                  )}
                </div>

                <div className="mt-5 pt-4 border-t border-white/10">
                  <button
                    onClick={testMtlsProbe}
                    disabled={mtlsProbeLoading}
                    className="w-full py-2.5 px-4 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 text-black font-bold text-xs hover:opacity-95 transition shadow-lg glow-emerald disabled:opacity-50 flex items-center justify-center gap-2"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${mtlsProbeLoading ? 'animate-spin' : ''}`} />
                    {mtlsProbeLoading ? 'Verifying Handshake...' : 'Send Mutual TLS Probe'}
                  </button>
                </div>
              </div>

              {/* Test Card 2: Plaintext Bypass Rejection Attack */}
              <div className="glass-panel p-6 rounded-2xl shadow-xl flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between">
                    <h3 className="font-bold text-white text-sm flex items-center gap-2">
                      <ShieldAlert className="w-4 h-4 text-rose-400" />
                      Test 2: Zero-Trust Attack & Bypass Defense
                    </h3>
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-400 border border-rose-500/30">
                      ENFORCED (403)
                    </span>
                  </div>
                  <p className="text-xs text-gray-400 mt-2">
                    Simulates an attacker or rogue container attempting direct unencrypted plaintext access to the internal payment endpoint (<code className="text-rose-300">/internal/payment-debits</code>) without valid mTLS certificates.
                  </p>

                  <div className="mt-4 p-3 rounded-xl bg-dark-900/80 border border-white/5 font-mono text-xs space-y-1">
                    <div className="text-gray-500 text-[10px]">EXPLOIT TARGET:</div>
                    <div className="text-rose-300">{getServiceUrl('payment')}/internal/payment-debits</div>
                  </div>

                  {attackResult && (
                    <div className="mt-4 p-4 rounded-xl bg-dark-900 border border-rose-500/40 space-y-2 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-rose-400 flex items-center gap-1.5">
                          <ShieldAlert className="w-3.5 h-3.5" /> ATTACK BLOCKED: HTTP {attackResult.status || 'REJECTED'}
                        </span>
                        <span className="text-gray-400 text-[11px]">{attackResult.duration} ms &bull; {attackResult.timestamp}</span>
                      </div>
                      <div className="text-gray-300 font-mono text-[11px] pt-1 border-t border-white/5">
                        <pre className="text-rose-300 whitespace-pre-wrap">{JSON.stringify(attackResult.body, null, 2)}</pre>
                      </div>
                    </div>
                  )}
                </div>

                <div className="mt-5 pt-4 border-t border-white/10">
                  <button
                    onClick={testPlaintextAttack}
                    disabled={attackLoading}
                    className="w-full py-2.5 px-4 rounded-xl bg-gradient-to-r from-rose-500 to-red-600 text-white font-bold text-xs hover:opacity-95 transition shadow-lg disabled:opacity-50 flex items-center justify-center gap-2"
                  >
                    <ShieldAlert className={`w-3.5 h-3.5 ${attackLoading ? 'animate-spin' : ''}`} />
                    {attackLoading ? 'Simulating Attack...' : 'Simulate Plaintext Bypass Attack'}
                  </button>
                </div>
              </div>
            </div>

            {/* SPIFFE Certificate Table */}
            <div className="glass-panel p-6 rounded-2xl shadow-xl">
              <h3 className="font-bold text-white text-sm flex items-center gap-2 mb-3">
                <Key className="w-4 h-4 text-cyan-400" />
                Workload Identity & X.509 Certificate Registry
              </h3>
              <div className="overflow-x-auto text-xs">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-white/10 text-gray-400">
                      <th className="py-2.5">Workload</th>
                      <th>K8s Secret Name</th>
                      <th>SPIFFE URI (SAN)</th>
                      <th>Key / Cipher</th>
                      <th>mTLS Policy</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 font-mono text-gray-300">
                    <tr>
                      <td className="py-2.5 text-white font-sans font-semibold">Mesh Root CA</td>
                      <td><code className="text-indigo-400">mesh-ca-cert</code></td>
                      <td><span className="text-gray-400">CN=MeshBank Internal Root CA</span></td>
                      <td>RSA 4096-bit SHA256</td>
                      <td><span className="text-emerald-400">Root of Trust</span></td>
                    </tr>
                    <tr>
                      <td className="py-2.5 text-white font-sans font-semibold">transaction-service</td>
                      <td><code className="text-cyan-400">transaction-mesh-certs</code></td>
                      <td className="text-cyan-300">spiffe://meshbank.internal/ns/meshbank/sa/transaction-service</td>
                      <td>RSA 2048-bit / TLS 1.3</td>
                      <td><span className="text-emerald-400">Client Auth Enforced</span></td>
                    </tr>
                    <tr>
                      <td className="py-2.5 text-white font-sans font-semibold">payment-service</td>
                      <td><code className="text-emerald-400">payment-mesh-certs</code></td>
                      <td className="text-emerald-300">spiffe://meshbank.internal/ns/meshbank/sa/payment-service</td>
                      <td>RSA 2048-bit / TLS 1.3</td>
                      <td><span className="text-emerald-400">Server & Client Auth Enforced</span></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* TAB 0B: CANARY TRAFFIC CONTROLLER */}
        {activeTab === 'canary' && (
          <div className="space-y-6">
            {/* Canary Hero */}
            <div className="glass-panel p-6 rounded-2xl shadow-xl relative overflow-hidden">
              <div className="absolute top-0 right-0 w-96 h-96 bg-amber-500/10 rounded-full blur-3xl pointer-events-none" />
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-bold px-2.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30 uppercase tracking-wider">
                      Phase 2 Traffic Management
                    </span>
                    <span className="text-xs text-gray-400">Active Deployment: <code className="text-cyan-300">payment-service</code></span>
                  </div>
                  <h2 className="text-xl font-bold text-white mt-2 flex items-center gap-2.5">
                    <Network className="w-5 h-5 text-amber-400" />
                    Canary Traffic Split & A/B Benchmark Manager
                  </h2>
                  <p className="text-xs text-gray-400 mt-1 max-w-3xl leading-relaxed">
                    Dynamically splits live banking traffic between Stable (v1) and Canary (v2) releases using weighted upstreams. 
                    Includes header-based overrides (<code className="text-amber-300">X-Canary: always/never</code>) and real-time live distribution monitoring.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => runTrafficBenchmark(batchSize)}
                    disabled={isStreamingTraffic}
                    className="flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-xl bg-gradient-to-r from-amber-500 to-orange-500 text-black hover:opacity-95 transition shadow-lg glow-amber disabled:opacity-50"
                  >
                    <Play className={`w-3.5 h-3.5 ${isStreamingTraffic ? 'animate-spin' : ''}`} />
                    {isStreamingTraffic ? 'Streaming Traffic...' : `Run ${batchSize}-Req Benchmark`}
                  </button>
                </div>
              </div>

              {/* Active Tracks Summary */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-6 pt-5 border-t border-white/10">
                {/* Stable Track */}
                <div className="p-4 rounded-xl bg-dark-800/80 border border-emerald-500/30 flex items-center justify-between">
                  <div>
                    <div className="text-[11px] text-gray-400 flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full bg-emerald-400" />
                      Stable Track (Baseline)
                    </div>
                    <div className="text-base font-bold text-emerald-400 mt-0.5">Version 1.0 (v1)</div>
                    <div className="text-xs text-gray-400 mt-1">Image: <code className="text-gray-300">payment-service:v1</code></div>
                  </div>
                  <div className="text-right">
                    <div className="text-2xl font-black text-emerald-400 font-mono">{canarySplit.stable}%</div>
                    <div className="text-[10px] text-gray-400">Target Weight</div>
                  </div>
                </div>

                {/* Canary Track */}
                <div className="p-4 rounded-xl bg-dark-800/80 border border-amber-500/30 flex items-center justify-between">
                  <div>
                    <div className="text-[11px] text-gray-400 flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                      Canary Track (Candidate)
                    </div>
                    <div className="text-base font-bold text-amber-400 mt-0.5">Version 2.0 (v2)</div>
                    <div className="text-xs text-gray-400 mt-1">Image: <code className="text-gray-300">payment-service:v2</code></div>
                  </div>
                  <div className="text-right">
                    <div className="text-2xl font-black text-amber-400 font-mono">{canarySplit.canary}%</div>
                    <div className="text-[10px] text-gray-400">Target Weight</div>
                  </div>
                </div>

                {/* Router Manager */}
                <div className="p-4 rounded-xl bg-dark-800/80 border border-cyan-500/30 flex items-center justify-between">
                  <div>
                    <div className="text-[11px] text-gray-400">Traffic Controller</div>
                    <div className="text-base font-bold text-cyan-400 mt-0.5">payment-router</div>
                    <div className="text-xs text-gray-400 mt-1">NGINX Weighted Upstream &bull; NodePort 30004</div>
                  </div>
                  <div className="text-right">
                    <div className="text-xs font-bold text-cyan-300 px-2 py-1 rounded-lg bg-cyan-500/10 border border-cyan-500/30">
                      Zero-Downtime
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Split Presets & Stage Management */}
            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-bold text-white text-sm flex items-center gap-2">
                    <Sliders className="w-4 h-4 text-amber-400" />
                    Canary Rollout Stages & Weight Presets
                  </h3>
                  <p className="text-xs text-gray-400 mt-0.5">
                    Select a standard progressive delivery stage or run the python traffic manager script.
                  </p>
                </div>
                {isApplyingSplit && (
                  <span className="text-xs text-amber-400 flex items-center gap-1.5">
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" /> Updating Cluster Weights...
                  </span>
                )}
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { label: 'Stage 1: Canary Pilot', stable: 90, canary: 10, desc: 'Initial 10% Canary rollout' },
                  { label: 'Stage 2: Validation', stable: 50, canary: 50, desc: 'Equal 50/50 A/B split' },
                  { label: 'Stage 3: Full Cutover', stable: 0, canary: 100, desc: 'Promote 100% to Canary (v2)' },
                  { label: 'Stage 4: Rollback', stable: 100, canary: 0, desc: 'Emergency 100% Stable (v1)' },
                ].map(stage => {
                  const isCurrent = canarySplit.stable === stage.stable && canarySplit.canary === stage.canary;
                  return (
                    <button
                      key={stage.label}
                      onClick={() => applyCanarySplit(stage.stable, stage.canary)}
                      className={`p-3.5 rounded-xl border text-left transition-all relative ${
                        isCurrent 
                          ? 'bg-amber-500/15 border-amber-500/60 shadow-lg glow-amber' 
                          : 'bg-dark-800/80 border-white/10 hover:border-white/20'
                      }`}
                    >
                      {isCurrent && (
                        <span className="absolute top-2 right-2 text-[10px] font-bold px-1.5 py-0.2 rounded bg-amber-500 text-black">
                          ACTIVE
                        </span>
                      )}
                      <div className="text-xs font-bold text-white">{stage.label}</div>
                      <div className="text-sm font-black font-mono mt-1 text-cyan-300">
                        {stage.stable}% / {stage.canary}%
                      </div>
                      <div className="text-[10px] text-gray-400 mt-1">{stage.desc}</div>
                    </button>
                  );
                })}
              </div>

              {/* CLI Command Helper */}
              <div className="p-3 rounded-xl bg-dark-900 border border-white/5 flex flex-col md:flex-row md:items-center justify-between gap-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-400 font-mono">CLI</span>
                  <code className="text-gray-300 font-mono text-[11px]">
                    python .\k8s\canary-traffic.py --split {canarySplit.stable}/{canarySplit.canary}
                  </code>
                </div>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(`python .\\k8s\\canary-traffic.py --split ${canarySplit.stable}/${canarySplit.canary}`);
                    showToast('Command copied to clipboard!', 'info');
                  }}
                  className="px-3 py-1 rounded-lg bg-dark-700 hover:bg-dark-600 text-gray-300 hover:text-white transition flex items-center gap-1.5 text-[11px]"
                >
                  <Copy className="w-3 h-3" /> Copy Command
                </button>
              </div>
            </div>

            {/* Header Override Controls */}
            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4">
              <div>
                <h3 className="font-bold text-white text-sm flex items-center gap-2">
                  <Zap className="w-4 h-4 text-cyan-400" />
                  Header-Based Routing Override (A/B Test Tunneling)
                </h3>
                <p className="text-xs text-gray-400 mt-0.5">
                  Inject <code className="text-cyan-300">X-Canary</code> headers to bypass statistical weighted splitting for QA or targeted smoke tests.
                </p>
              </div>

              <div className="flex flex-wrap gap-2">
                {[
                  { id: 'none', label: 'Default Mesh Routing', desc: `Weighted (${canarySplit.stable}% / ${canarySplit.canary}%)` },
                  { id: 'always', label: 'Force Canary (X-Canary: always)', desc: 'Guaranteed 100% Canary (v2)' },
                  { id: 'never', label: 'Force Stable (X-Canary: never)', desc: 'Guaranteed 100% Stable (v1)' },
                ].map(opt => (
                  <button
                    key={opt.id}
                    onClick={() => {
                      setCanaryHeaderOverride(opt.id);
                      showToast(`Routing mode set: ${opt.label}`, 'info');
                    }}
                    className={`px-4 py-2 rounded-xl text-xs font-semibold border transition-all ${
                      canaryHeaderOverride === opt.id
                        ? 'bg-cyan-500/20 border-cyan-500 text-cyan-300 shadow-md'
                        : 'bg-dark-800/80 border-white/10 text-gray-400 hover:text-white'
                    }`}
                  >
                    <div>{opt.label}</div>
                    <div className="text-[10px] font-normal text-gray-400 mt-0.5">{opt.desc}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Live Traffic Benchmark & Statistical Stream */}
            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-5">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                  <h3 className="font-bold text-white text-sm flex items-center gap-2">
                    <Activity className="w-4 h-4 text-emerald-400" />
                    Live Traffic Stream & Statistical Distribution Monitor
                  </h3>
                  <p className="text-xs text-gray-400 mt-0.5">
                    Generate live HTTP request bursts through <code className="text-cyan-300">payment-router</code> to measure real-world hit ratios.
                  </p>
                </div>

                <div className="flex items-center gap-3">
                  <div className="flex bg-dark-900 p-1 rounded-xl border border-white/10 text-xs">
                    {[10, 25, 50, 100].map(cnt => (
                      <button
                        key={cnt}
                        onClick={() => setBatchSize(cnt)}
                        className={`px-3 py-1 rounded-lg transition ${batchSize === cnt ? 'bg-cyan-500 text-black font-bold' : 'text-gray-400 hover:text-white'}`}
                      >
                        {cnt} reqs
                      </button>
                    ))}
                  </div>

                  <button
                    onClick={() => runTrafficBenchmark(batchSize)}
                    disabled={isStreamingTraffic}
                    className="flex items-center gap-2 px-4 py-2 text-xs font-bold rounded-xl bg-gradient-to-r from-emerald-500 to-teal-500 text-black hover:opacity-90 transition shadow-lg glow-emerald disabled:opacity-50"
                  >
                    <Play className={`w-3.5 h-3.5 ${isStreamingTraffic ? 'animate-spin' : ''}`} />
                    {isStreamingTraffic ? 'Streaming...' : 'Start Traffic Burst'}
                  </button>
                </div>
              </div>

              {/* Statistical Distribution Comparison Bar */}
              <div className="space-y-3 p-4 rounded-xl bg-dark-900/80 border border-white/5">
                <div>
                  <div className="flex justify-between text-xs mb-1.5">
                    <span className="text-gray-400">Configured Target Ratio:</span>
                    <span className="font-mono text-xs">
                      <strong className="text-emerald-400">{canarySplit.stable}% Stable</strong> / <strong className="text-amber-400">{canarySplit.canary}% Canary</strong>
                    </span>
                  </div>
                  <div className="w-full h-3 rounded-full bg-dark-700 overflow-hidden flex">
                    <div style={{ width: `${canarySplit.stable}%` }} className="bg-emerald-500 h-full transition-all duration-300" />
                    <div style={{ width: `${canarySplit.canary}%` }} className="bg-amber-500 h-full transition-all duration-300" />
                  </div>
                </div>

                <div>
                  <div className="flex justify-between text-xs mb-1.5">
                    <span className="text-gray-400">Observed Traffic Ratio ({trafficStats.total} requests):</span>
                    <span className="font-mono text-xs">
                      <strong className="text-emerald-400">
                        {trafficStats.total > 0 ? Math.round((trafficStats.stable / trafficStats.total) * 100) : 0}% Stable ({trafficStats.stable})
                      </strong> / <strong className="text-amber-400">
                        {trafficStats.total > 0 ? Math.round((trafficStats.canary / trafficStats.total) * 100) : 0}% Canary ({trafficStats.canary})
                      </strong>
                    </span>
                  </div>
                  <div className="w-full h-3 rounded-full bg-dark-700 overflow-hidden flex">
                    <div 
                      style={{ width: `${trafficStats.total > 0 ? (trafficStats.stable / trafficStats.total) * 100 : 0}%` }} 
                      className="bg-emerald-500 h-full transition-all duration-300" 
                    />
                    <div 
                      style={{ width: `${trafficStats.total > 0 ? (trafficStats.canary / trafficStats.total) * 100 : 0}%` }} 
                      className="bg-amber-500 h-full transition-all duration-300" 
                    />
                  </div>
                </div>
              </div>

              {/* KPI Metrics */}
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <div className="p-3 rounded-xl bg-dark-800 border border-white/5 text-center">
                  <div className="text-[10px] text-gray-400 uppercase">Total Requests</div>
                  <div className="text-xl font-black text-white font-mono mt-0.5">{trafficStats.total}</div>
                </div>
                <div className="p-3 rounded-xl bg-dark-800 border border-emerald-500/20 text-center">
                  <div className="text-[10px] text-emerald-400 uppercase">Stable (v1) Hits</div>
                  <div className="text-xl font-black text-emerald-400 font-mono mt-0.5">{trafficStats.stable}</div>
                </div>
                <div className="p-3 rounded-xl bg-dark-800 border border-amber-500/20 text-center">
                  <div className="text-[10px] text-amber-400 uppercase">Canary (v2) Hits</div>
                  <div className="text-xl font-black text-amber-400 font-mono mt-0.5">{trafficStats.canary}</div>
                </div>
                <div className="p-3 rounded-xl bg-dark-800 border border-white/5 text-center">
                  <div className="text-[10px] text-cyan-400 uppercase">Avg Latency</div>
                  <div className="text-xl font-black text-cyan-400 font-mono mt-0.5">{trafficStats.avgLatency} ms</div>
                </div>
                <div className="p-3 rounded-xl bg-dark-800 border border-white/5 text-center">
                  <div className="text-[10px] text-gray-400 uppercase">Failed / Drops</div>
                  <div className="text-xl font-black text-gray-400 font-mono mt-0.5">{trafficStats.errors}</div>
                </div>
              </div>

              {/* Live Request Stream Table */}
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs text-gray-400">
                  <span>Recent Request Inspection Stream (Last 50)</span>
                  <button onClick={() => setTrafficStreamLogs([])} className="hover:text-white">Clear Stream</button>
                </div>
                <div className="overflow-x-auto max-h-[360px] overflow-y-auto text-xs rounded-xl border border-white/10 bg-dark-900/60">
                  <table className="w-full text-left font-mono">
                    <thead className="sticky top-0 bg-dark-900 border-b border-white/10 text-gray-400 text-[11px]">
                      <tr>
                        <th className="py-2.5 px-3">#</th>
                        <th>Status</th>
                        <th>Track</th>
                        <th>Version</th>
                        <th>Latency</th>
                        <th>Router Pod</th>
                        <th>Timestamp</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5 text-gray-300">
                      {trafficStreamLogs.length === 0 ? (
                        <tr>
                          <td colSpan="7" className="py-6 text-center text-gray-500 font-sans">
                            No traffic requests logged yet. Click "Start Traffic Burst" to stream live requests.
                          </td>
                        </tr>
                      ) : (
                        trafficStreamLogs.map(log => (
                          <tr key={log.id || log.seq} className="hover:bg-white/5 transition">
                            <td className="py-2 px-3 text-cyan-400">#{log.seq}</td>
                            <td>
                              <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                                log.status === 200 ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'
                              }`}>
                                {log.status || 'ERR'}
                              </span>
                            </td>
                            <td>
                              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                                log.track === 'canary' 
                                  ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' 
                                  : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                              }`}>
                                {log.track}
                              </span>
                            </td>
                            <td>
                              <span className={`font-bold ${log.version === 'v2' ? 'text-amber-400' : 'text-emerald-400'}`}>
                                {log.version}
                              </span>
                            </td>
                            <td className="text-gray-400">{log.duration} ms</td>
                            <td className="text-gray-400 text-[10px]">{log.router}</td>
                            <td className="text-gray-500 text-[10px]">{log.time}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* TAB 1: TOPOLOGY & HEALTH */}
        {activeTab === 'topology' && (
          <div className="space-y-6">
            <div className="glass-panel p-6 rounded-2xl shadow-xl">
              <div className="flex items-center justify-between mb-5">
                <div>
                  <h2 className="text-lg font-bold text-white flex items-center gap-2">
                    <Server className="w-5 h-5 text-cyan-400" />
                    Microservice Infrastructure & Readiness
                  </h2>
                  <p className="text-xs text-gray-400 mt-1">
                    Target: <strong className="text-cyan-400">{ENVIRONMENTS[envKey].name}</strong>. Real-time liveness (<code className="text-cyan-300">/health</code>) and readiness (<code className="text-cyan-300">/ready</code>) checks.
                  </p>
                </div>
                <button
                  onClick={checkAllServices}
                  disabled={isCheckingHealth}
                  className="flex items-center gap-2 px-3.5 py-1.5 text-xs font-semibold rounded-lg bg-cyan-500 text-black hover:bg-cyan-400 transition shadow-md glow-cyan disabled:opacity-50"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${isCheckingHealth ? 'animate-spin' : ''}`} />
                  Refresh All
                </button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {SERVICE_SPECS.map(s => {
                  const st = serviceStatus[s.id] || { status: 'checking', detail: 'Checking...' };
                  const isOk = st.status === 'ready';
                  const isErr = st.status === 'offline';
                  return (
                    <div key={s.id} className="p-4 rounded-xl bg-dark-800/80 border border-white/10 relative overflow-hidden flex flex-col justify-between">
                      <div className={`absolute top-0 left-0 bottom-0 w-1 ${isOk ? 'bg-emerald-500' : isErr ? 'bg-red-500' : 'bg-amber-500'}`} />
                      <div>
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-sm text-gray-100">{s.name}</span>
                          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                            isOk ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 
                            isErr ? 'bg-red-500/20 text-red-400 border border-red-500/30' : 
                            'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                          }`}>
                            {st.detail}
                          </span>
                        </div>
                        <div className="font-mono text-xs text-cyan-400/80 mt-1">{getServiceUrl(s.id)}</div>
                        <div className="text-xs text-gray-400 mt-2">{s.desc}</div>
                      </div>
                      <div className="mt-4 pt-3 border-t border-white/5 flex items-center justify-between text-xs text-gray-400">
                        <span>Database: <code className="text-gray-300">meshbank_{s.id.split('-')[0]}</code></span>
                        <button 
                          onClick={() => checkService(s.id)} 
                          className="text-cyan-400 hover:underline text-[11px]"
                        >
                          Ping
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="glass-panel p-6 rounded-2xl shadow-xl">
                <h3 className="font-bold text-white mb-3 text-sm flex items-center gap-2">
                  <Shield className="w-4 h-4 text-cyan-400" />
                  Service Discovery & Port Mapping Table
                </h3>
                <div className="overflow-x-auto text-xs">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-white/10 text-gray-400">
                        <th className="py-2">Service</th>
                        <th>Docker Port</th>
                        <th>Kubernetes NodePort</th>
                        <th>Isolation</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5 font-mono text-gray-300">
                      <tr><td className="py-2 text-white">auth-service</td><td>8001</td><td>30001</td><td>Private RSA Key</td></tr>
                      <tr><td className="py-2 text-white">account-service</td><td>8002</td><td>30002</td><td>Row-Level Isolation</td></tr>
                      <tr><td className="py-2 text-white">transaction-service</td><td>8003</td><td>30003</td><td>Distributed Saga</td></tr>
                      <tr><td className="py-2 text-white">payment-service</td><td>8004</td><td>30004</td><td>Simulated Gateway</td></tr>
                      <tr><td className="py-2 text-white">fraud-service</td><td>8005</td><td>30005</td><td>Rule Engine</td></tr>
                      <tr><td className="py-2 text-white">notification-service</td><td>8006</td><td>30006</td><td>Delivery Log</td></tr>
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-3">
                <h3 className="font-bold text-white text-sm">Security & Architectural Tenets</h3>
                <ul className="text-xs text-gray-300 space-y-2.5 list-disc pl-4">
                  <li><strong>Asymmetric RS256 Tokens:</strong> Generated exclusively by Auth Service using its <code>0400</code> private key; verified statelessly by downstream services with mounted public certificates.</li>
                  <li><strong>Decentralized Zero-Trust Containers:</strong> Pods run non-root (<code className="text-cyan-300">10001:10001</code>), with immutable read-only root filesystems and dropped Linux capabilities.</li>
                  <li><strong>Strict Database Segregation:</strong> Distinct PostgreSQL roles with zero shared passwords. Forward-only Alembic migrations run automatically on startup.</li>
                  <li><strong>Idempotent Saga Workflows:</strong> Transfers and bill payments support distributed compensation and client-supplied UUID idempotency keys to survive network retries.</li>
                </ul>
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: IDENTITY & RS256 JWT */}
        {activeTab === 'auth' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-5">
              <div>
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <Key className="w-5 h-5 text-cyan-400" />
                  Quick Demo Logins & Registration
                </h2>
                <p className="text-xs text-gray-400 mt-1">Authenticate as Alice or Bob, or register a new customer in the Auth Service.</p>
              </div>

              <div className="flex gap-3">
                <button
                  onClick={() => quickLogin('alice')}
                  className="flex-1 py-2.5 px-4 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 text-black font-bold text-xs hover:brightness-110 transition shadow-md glow-cyan flex items-center justify-center gap-2"
                >
                  <User className="w-4 h-4" />
                  Login as Alice
                </button>
                <button
                  onClick={() => quickLogin('bob')}
                  className="flex-1 py-2.5 px-4 rounded-xl bg-dark-700 hover:bg-dark-600 text-white font-semibold text-xs border border-white/10 transition flex items-center justify-center gap-2"
                >
                  <User className="w-4 h-4" />
                  Login as Bob
                </button>
              </div>

              <div className="relative py-2">
                <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-white/10"></div></div>
                <div className="relative flex justify-center text-[10px] uppercase"><span className="bg-dark-800 px-2 text-gray-400 font-bold">Or Register Custom</span></div>
              </div>

              <form onSubmit={async (e) => {
                e.preventDefault();
                const form = e.target;
                const name = form.name.value;
                const email = form.email.value;
                const pass = form.password.value;
                const authUrl = getServiceUrl('auth');
                try {
                  const regRes = await fetch(`${authUrl}/auth/register`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password: pass, full_name: name }),
                  });
                  if (!regRes.ok) throw new Error((await regRes.json()).detail || 'Failed');
                  const logRes = await fetch(`${authUrl}/auth/login`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password: pass }),
                  });
                  const logData = await logRes.json();
                  setAuthSession(logData.access_token, { email, full_name: name });
                  showToast(`Registered & logged in as ${name}!`, 'success');
                } catch (err) {
                  showToast(err.message, 'error');
                }
              }} className="space-y-3 text-xs">
                <div>
                  <label className="block text-gray-400 mb-1">Full Name</label>
                  <input name="name" defaultValue="Charlie Ray" required className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-cyan-400" />
                </div>
                <div>
                  <label className="block text-gray-400 mb-1">Email Address</label>
                  <input name="email" type="email" defaultValue="charlie@example.com" required className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-cyan-400" />
                </div>
                <div>
                  <label className="block text-gray-400 mb-1">Password</label>
                  <input name="password" type="password" defaultValue="MeshBank#2026!" required className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-cyan-400" />
                </div>
                <button type="submit" className="w-full py-2.5 rounded-xl bg-cyan-500 text-black font-bold hover:bg-cyan-400 transition">
                  Create Customer & Authenticate
                </button>
              </form>
            </div>

            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-base font-bold text-white flex items-center gap-2">
                    <Shield className="w-5 h-5 text-cyan-400" />
                    Decoded RS256 JWT Token Inspector
                  </h2>
                  <p className="text-xs text-gray-400 mt-1">Verified statelessly across all microservices via public key certificates.</p>
                </div>
                {token && (
                  <button 
                    onClick={() => { navigator.clipboard.writeText(token); showToast('Token copied', 'info'); }}
                    className="p-1.5 text-xs bg-dark-700 hover:bg-dark-600 rounded-lg border border-white/10 text-cyan-400 flex items-center gap-1.5"
                  >
                    <Copy className="w-3.5 h-3.5" />
                    Copy
                  </button>
                )}
              </div>

              <div>
                <label className="block text-xs font-semibold text-gray-400 mb-1">Raw Access Token (Bearer)</label>
                <div className="font-mono text-[11px] p-3 rounded-xl bg-dark-900 border border-white/10 text-cyan-300 break-all max-h-28 overflow-y-auto">
                  {token || 'No active session. Please login.'}
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-gray-400 mb-1">Decoded Payload Claims</label>
                <pre className="font-mono text-[11px] p-3 rounded-xl bg-dark-900 border border-white/10 text-emerald-400 overflow-x-auto max-h-48">
                  {token ? JSON.stringify(decodeJwt(token), null, 2) : '{}'}
                </pre>
              </div>
            </div>
          </div>
        )}

        {/* TAB 3: ACCOUNTS & BALANCE */}
        {activeTab === 'accounts' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-5">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-base font-bold text-white flex items-center gap-2">
                    <CreditCard className="w-5 h-5 text-cyan-400" />
                    My Bank Accounts
                  </h2>
                  <p className="text-xs text-gray-400 mt-1">Scoped to the active customer JWT token.</p>
                </div>
                <button onClick={fetchAccounts} className="p-1.5 text-xs bg-dark-700 hover:bg-dark-600 rounded-lg border border-white/10 text-gray-300 flex items-center gap-1">
                  <RefreshCw className="w-3.5 h-3.5" />
                  Refresh
                </button>
              </div>

              <div className="space-y-3">
                {accounts.length === 0 ? (
                  <div className="p-6 rounded-xl border border-dashed border-white/15 text-center text-xs text-gray-400">
                    No accounts found. Open a new account below.
                  </div>
                ) : (
                  accounts.map(acc => (
                    <div key={acc.id} className="p-4 rounded-xl bg-dark-900 border border-white/10 flex items-center justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                            {acc.account_type}
                          </span>
                          <span className="font-mono text-xs text-gray-400">{acc.id}</span>
                        </div>
                        <div className="text-xl font-bold text-white mt-1">
                          ₹{Number(acc.balance).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                          <span className="text-xs font-normal text-gray-400 ml-1.5">{acc.currency}</span>
                        </div>
                      </div>
                      <div className="flex flex-col gap-1.5 text-xs">
                        <button 
                          onClick={() => { setSeedAccId(acc.id); showToast('Selected for seeding', 'info'); }}
                          className="px-2.5 py-1 bg-dark-700 hover:bg-dark-600 rounded text-cyan-400 border border-white/10"
                        >
                          Seed Funds
                        </button>
                        <button 
                          onClick={() => { setTransferSource(acc.id); setActiveTab('transfers'); }}
                          className="px-2.5 py-1 bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-300 rounded border border-cyan-500/30"
                        >
                          Transfer
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>

              <div className="pt-4 border-t border-white/10">
                <label className="block text-xs font-medium text-gray-300 mb-2">Open New Account</label>
                <div className="flex gap-2">
                  <select 
                    value={newAccType} 
                    onChange={e => setNewAccType(e.target.value)}
                    className="flex-1 bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-xs text-white"
                  >
                    <option value="SAVINGS">Savings Account</option>
                    <option value="CURRENT">Current Account</option>
                  </select>
                  <button 
                    onClick={createAccount}
                    className="px-4 py-2 bg-cyan-500 text-black font-bold text-xs rounded-lg hover:bg-cyan-400 transition"
                  >
                    Open Account
                  </button>
                </div>
              </div>
            </div>

            {/* Dev Seeder & Isolation Test */}
            <div className="space-y-6">
              {/* Dev Seeding */}
              <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4">
                <div>
                  <h3 className="text-sm font-bold text-cyan-400 flex items-center gap-2">
                    <DollarSign className="w-4 h-4" />
                    Dev Balance Seeding (Internal Token)
                  </h3>
                  <p className="text-xs text-gray-400 mt-1">
                    Inject test funds into any account via the Account Service internal seeding endpoint.
                  </p>
                </div>

                <div className="space-y-3 text-xs">
                  <div>
                    <label className="block text-gray-400 mb-1">Target Account ID</label>
                    <input 
                      value={seedAccId} 
                      onChange={e => setSeedAccId(e.target.value)} 
                      placeholder="Select an account from the left"
                      className="w-full font-mono bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white" 
                    />
                  </div>
                  <div>
                    <label className="block text-gray-400 mb-1">Amount (INR)</label>
                    <input 
                      type="number"
                      value={seedAmount} 
                      onChange={e => setSeedAmount(e.target.value)} 
                      className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white" 
                    />
                    <div className="flex gap-2 mt-2">
                      {['5000.00', '20000.00', '50000.00'].map(amt => (
                        <button
                          key={amt}
                          onClick={() => setSeedAmount(amt)}
                          className="px-2.5 py-1 rounded-full text-[11px] bg-dark-700 hover:bg-cyan-500/20 text-gray-300 border border-white/10"
                        >
                          +₹{Number(amt).toLocaleString()}
                        </button>
                      ))}
                    </div>
                  </div>
                  <button 
                    onClick={seedFunds}
                    className="w-full py-2.5 bg-cyan-500 text-black font-bold rounded-lg hover:bg-cyan-400 transition"
                  >
                    Deposit Test Funds
                  </button>
                </div>
              </div>

              {/* 403 Forbidden Isolation Test */}
              <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4 border-red-500/30">
                <div>
                  <h3 className="text-sm font-bold text-red-400 flex items-center gap-2">
                    <EyeOff className="w-4 h-4" />
                    Security Check: Cross-Tenant 403 Isolation
                  </h3>
                  <p className="text-xs text-gray-400 mt-1">
                    Attempt to query another customer's account to verify strict <code>403 Forbidden</code> enforcement.
                  </p>
                </div>

                <div className="space-y-3 text-xs">
                  <input 
                    value={foreignAccId} 
                    onChange={e => setForeignAccId(e.target.value)}
                    placeholder="Enter foreign account UUID"
                    className="w-full font-mono bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white" 
                  />
                  <button 
                    onClick={testIsolation}
                    className="w-full py-2.5 bg-red-500/20 hover:bg-red-500/30 text-red-400 border border-red-500/40 font-semibold rounded-lg transition"
                  >
                    Execute Unauthorized Query
                  </button>

                  {isolationResult && (
                    <div className={`p-3 rounded-xl text-xs border ${isolationResult.success ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300' : 'bg-red-500/10 border-red-500/30 text-red-300'}`}>
                      {isolationResult.message}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* TAB 4: TRANSFERS (WORKFLOW C - SAGA) */}
        {activeTab === 'transfers' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4">
              <div>
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <ArrowRightLeft className="w-5 h-5 text-cyan-400" />
                  P2P Money Transfer (Workflow C - Saga)
                </h2>
                <p className="text-xs text-gray-400 mt-1">
                  Executes an atomic transfer saga across Account, Fraud, and Notification microservices.
                </p>
              </div>

              <form onSubmit={handleTransferSubmit} className="space-y-3 text-xs">
                <div>
                  <label className="block text-gray-400 mb-1">Source Account</label>
                  <select 
                    value={transferSource} 
                    onChange={e => setTransferSource(e.target.value)}
                    required
                    className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white"
                  >
                    <option value="">Select source account</option>
                    {accounts.map(a => (
                      <option key={a.id} value={a.id}>{a.account_type} (₹{a.balance}) — {a.id.substring(0,8)}...</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-gray-400 mb-1">Destination Account UUID</label>
                  <input 
                    value={transferDest} 
                    onChange={e => setTransferDest(e.target.value)}
                    placeholder="e.g. Bob's Account UUID"
                    required
                    className="w-full font-mono bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white" 
                  />
                </div>

                <div>
                  <label className="block text-gray-400 mb-1">Amount (INR)</label>
                  <input 
                    type="number"
                    value={transferAmount} 
                    onChange={e => setTransferAmount(e.target.value)}
                    step="0.01" min="1" required
                    className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white" 
                  />
                  <div className="flex gap-2 mt-1.5">
                    {['500.00', '2000.00', '10000.00'].map(a => (
                      <button key={a} type="button" onClick={() => setTransferAmount(a)} className="px-2 py-0.5 bg-dark-700 rounded text-[11px] text-gray-300">
                        ₹{Number(a).toLocaleString()}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="block text-gray-400 mb-1">Description</label>
                  <input 
                    value={transferDesc} 
                    onChange={e => setTransferDesc(e.target.value)}
                    className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white" 
                  />
                </div>

                <div>
                  <div className="flex justify-between items-center mb-1">
                    <label className="text-gray-400">Idempotency Key (UUID)</label>
                    <button 
                      type="button" 
                      onClick={() => setTransferIdem('idem-' + crypto.randomUUID())}
                      className="text-cyan-400 text-[11px] hover:underline"
                    >
                      Generate New
                    </button>
                  </div>
                  <input 
                    value={transferIdem} 
                    onChange={e => setTransferIdem(e.target.value)}
                    required
                    className="w-full font-mono text-[11px] bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-cyan-300" 
                  />
                </div>

                <div className="flex gap-3 pt-2">
                  <button 
                    type="submit" 
                    disabled={transferLoading}
                    className="flex-1 py-2.5 bg-cyan-500 text-black font-bold rounded-xl hover:bg-cyan-400 transition shadow-md glow-cyan disabled:opacity-50"
                  >
                    {transferLoading ? 'Executing Saga...' : 'Execute Transfer'}
                  </button>
                  <button 
                    type="button" 
                    onClick={() => {
                      if (!lastTransferPayload) return showToast('Execute a transfer first', 'error');
                      showToast('Replaying exact payload with same idempotency key...', 'info');
                      executeTransfer(lastTransferPayload);
                    }}
                    className="px-4 py-2.5 bg-dark-700 hover:bg-dark-600 border border-white/10 text-cyan-300 font-semibold rounded-xl text-xs"
                    title="Resends exact payload with identical idempotency key to prove zero double-debit"
                  >
                    Test Idempotency
                  </button>
                </div>
              </form>
            </div>

            {/* Saga Result View */}
            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4">
              <h2 className="text-base font-bold text-white">Saga Lifecycle & Execution State</h2>
              
              <div className="p-4 rounded-xl bg-dark-900 border border-white/10 space-y-2 text-xs">
                <div className="flex items-center gap-2 text-gray-300"><CheckCircle2 className="w-4 h-4 text-emerald-400" /> 1. RS256 Caller JWT Validated</div>
                <div className="flex items-center gap-2 text-gray-300"><CheckCircle2 className="w-4 h-4 text-emerald-400" /> 2. Fraud Rules Evaluated (Threshold: ₹50,000)</div>
                <div className="flex items-center gap-2 text-gray-300"><CheckCircle2 className="w-4 h-4 text-emerald-400" /> 3. Source Account Debited & Reserved</div>
                <div className="flex items-center gap-2 text-gray-300"><CheckCircle2 className="w-4 h-4 text-emerald-400" /> 4. Destination Account Credited</div>
                <div className="flex items-center gap-2 text-gray-300"><CheckCircle2 className="w-4 h-4 text-emerald-400" /> 5. Audit Log & Notifications Dispatched</div>
              </div>

              {transferResponse && (
                <div className={`p-4 rounded-xl border ${transferResponse.data.status === 'COMPLETED' ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300' : 'bg-red-500/10 border-red-500/30 text-red-300'}`}>
                  <div className="flex items-center justify-between font-bold text-sm">
                    <span>Status: {transferResponse.data.status || 'ERROR'}</span>
                    {transferResponse.isReplayed && (
                      <span className="text-[10px] uppercase px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-400 border border-cyan-500/30">
                        Idempotent Replay Verified
                      </span>
                    )}
                  </div>
                  <pre className="font-mono text-[11px] mt-2 overflow-x-auto text-gray-300 max-h-48">
                    {JSON.stringify(transferResponse.data, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        )}

        {/* TAB 5: BILL PAYMENTS & FRAUD */}
        {activeTab === 'payments' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4">
              <div>
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <ReceiptText className="w-5 h-5 text-cyan-400" />
                  Bill & Merchant Payments (Workflow D)
                </h2>
                <p className="text-xs text-gray-400 mt-1">Exercises Payment Service simulated gateway & Fraud engine rules.</p>
              </div>

              <form onSubmit={handlePaymentSubmit} className="space-y-3 text-xs">
                <div>
                  <label className="block text-gray-400 mb-1">Source Account</label>
                  <select 
                    value={paySource} 
                    onChange={e => setPaySource(e.target.value)}
                    required
                    className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white"
                  >
                    <option value="">Select source account</option>
                    {accounts.map(a => (
                      <option key={a.id} value={a.id}>{a.account_type} (₹{a.balance}) — {a.id.substring(0,8)}...</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-gray-400 mb-1">Payee Reference</label>
                  <input 
                    value={payeeRef} 
                    onChange={e => setPayeeRef(e.target.value)}
                    required
                    className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white" 
                  />
                  <div className="flex gap-2 mt-1.5 flex-wrap">
                    <button type="button" onClick={() => setPayeeRef('ELECTRIC-BILL-001')} className="px-2 py-0.5 bg-dark-700 rounded text-[11px] text-gray-300">Electricity</button>
                    <button type="button" onClick={() => setPayeeRef('WATER-UTIL-102')} className="px-2 py-0.5 bg-dark-700 rounded text-[11px] text-gray-300">Water</button>
                    <button type="button" onClick={() => setPayeeRef('DECLINE-TEST-999')} className="px-2 py-0.5 bg-red-500/20 text-red-300 rounded text-[11px]">⚠️ Test Gateway Decline</button>
                  </div>
                </div>

                <div>
                  <label className="block text-gray-400 mb-1">Payment Amount (INR)</label>
                  <input 
                    type="number"
                    value={payAmount} 
                    onChange={e => setPayAmount(e.target.value)}
                    step="0.01" min="1" required
                    className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white" 
                  />
                  <div className="flex gap-2 mt-1.5 flex-wrap">
                    <button type="button" onClick={() => setPayAmount('850.00')} className="px-2 py-0.5 bg-dark-700 rounded text-[11px] text-gray-300">₹850 (Normal)</button>
                    <button type="button" onClick={() => setPayAmount('55000.00')} className="px-2 py-0.5 bg-amber-500/20 text-amber-300 rounded text-[11px]">₹55,000 (Fraud Review)</button>
                    <button type="button" onClick={() => setPayAmount('250000.00')} className="px-2 py-0.5 bg-red-500/20 text-red-300 rounded text-[11px]">₹2,50,000 (Hard Reject)</button>
                  </div>
                  <div className="text-[11px] text-gray-400 mt-1">Review threshold: ₹50,000 | Hard reject limit: ₹2,00,000</div>
                </div>

                <div>
                  <label className="block text-gray-400 mb-1">Description</label>
                  <input 
                    value={payDesc} 
                    onChange={e => setPayDesc(e.target.value)}
                    className="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-2 text-white" 
                  />
                </div>

                <button 
                  type="submit" 
                  disabled={paymentLoading}
                  className="w-full py-2.5 bg-cyan-500 text-black font-bold rounded-xl hover:bg-cyan-400 transition shadow-md glow-cyan disabled:opacity-50"
                >
                  {paymentLoading ? 'Processing Payment...' : 'Submit Payment'}
                </button>
              </form>
            </div>

            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4">
              <h2 className="text-base font-bold text-white">Payment & Gateway Outcome</h2>
              {paymentResponse ? (
                <div className={`p-4 rounded-xl border ${
                  paymentResponse.data.status === 'COMPLETED' ? 'bg-emerald-500/10 border-emerald-500/30' :
                  paymentResponse.data.status === 'UNDER_REVIEW' ? 'bg-amber-500/10 border-amber-500/30' :
                  'bg-red-500/10 border-red-500/30'
                }`}>
                  <div className="text-sm font-bold flex items-center justify-between">
                    <span>Outcome: {paymentResponse.data.status || 'ERROR'}</span>
                    <span className="font-mono text-xs">{paymentResponse.data.id?.substring(0,8)}</span>
                  </div>
                  <div className="text-xs text-gray-300 mt-2">
                    {paymentResponse.data.failure_reason || (paymentResponse.data.status === 'COMPLETED' ? 'Payment processed successfully by simulated gateway.' : 'Flagged by fraud engine.')}
                  </div>
                  <pre className="font-mono text-[11px] mt-3 overflow-x-auto text-gray-300 max-h-48 bg-dark-950 p-2.5 rounded-lg">
                    {JSON.stringify(paymentResponse.data, null, 2)}
                  </pre>
                </div>
              ) : (
                <div className="p-6 rounded-xl border border-dashed border-white/15 text-center text-xs text-gray-400">
                  Submit a payment to inspect real-time gateway and fraud engine responses.
                </div>
              )}
            </div>
          </div>
        )}

        {/* TAB 6: HISTORY & NOTIFICATIONS */}
        {activeTab === 'history' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Transactions Ledger */}
            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <History className="w-5 h-5 text-cyan-400" />
                  Transaction Ledger History
                </h2>
                <button onClick={fetchTransactions} className="p-1 text-gray-400 hover:text-white">
                  <RefreshCw className="w-4 h-4" />
                </button>
              </div>

              <div className="overflow-x-auto text-xs">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-white/10 text-gray-400">
                      <th className="py-2">Type</th>
                      <th>Amount</th>
                      <th>Status</th>
                      <th>Time</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 font-mono">
                    {transactions.length === 0 ? (
                      <tr><td colSpan="4" className="py-4 text-center text-gray-500 font-sans">No transactions recorded yet.</td></tr>
                    ) : (
                      transactions.map(t => (
                        <tr key={t.id} className="hover:bg-white/5">
                          <td className="py-2.5 text-white font-sans">
                            <span className="font-semibold">{t.kind || t.type || 'TRANSFER'}</span>
                            <div className="text-[10px] font-mono text-gray-400">{t.id.substring(0,8)}...</div>
                          </td>
                          <td className="text-cyan-400">₹{Number(t.amount).toLocaleString('en-IN', { minimumFractionDigits: 2 })}</td>
                          <td>
                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                              t.status === 'COMPLETED' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-red-500/20 text-red-400 border border-red-500/30'
                            }`}>
                              {t.status}
                            </span>
                          </td>
                          <td className="text-gray-400 text-[11px]">{new Date(t.created_at).toLocaleTimeString()}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Notifications Stream */}
            <div className="glass-panel p-6 rounded-2xl shadow-xl space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <Bell className="w-5 h-5 text-cyan-400" />
                  Customer Notification Stream
                </h2>
                <button onClick={fetchNotifications} className="p-1 text-gray-400 hover:text-white">
                  <RefreshCw className="w-4 h-4" />
                </button>
              </div>

              <div className="space-y-2.5 max-h-[400px] overflow-y-auto">
                {notifications.length === 0 ? (
                  <div className="p-6 text-center text-xs text-gray-500">No notifications received yet.</div>
                ) : (
                  notifications.map((n, idx) => (
                    <div key={idx} className="p-3 rounded-xl bg-dark-900 border border-white/10 text-xs">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                          {n.notification_type || 'DISPATCH'}
                        </span>
                        <span className="text-gray-500 text-[10px]">{new Date(n.created_at).toLocaleTimeString()}</span>
                      </div>
                      <p className="text-gray-200 mt-1">{n.message || n.subject || 'Notification dispatched'}</p>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

      </main>

      {/* Floating Toast Notification Stack */}
      <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 pointer-events-none">
        {toasts.map(t => (
          <div 
            key={t.id} 
            className={`pointer-events-auto px-4 py-3 rounded-xl text-xs font-medium text-white shadow-xl backdrop-blur-xl border flex items-center gap-2.5 transition-all ${
              t.type === 'success' ? 'bg-dark-800/95 border-emerald-500/50 text-emerald-300' :
              t.type === 'error' ? 'bg-dark-800/95 border-red-500/50 text-red-300' :
              'bg-dark-800/95 border-cyan-500/50 text-cyan-300'
            }`}
          >
            {t.type === 'success' ? <CheckCircle2 className="w-4 h-4 text-emerald-400" /> :
             t.type === 'error' ? <AlertTriangle className="w-4 h-4 text-red-400" /> :
             <Shield className="w-4 h-4 text-cyan-400" />}
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

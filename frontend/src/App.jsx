import React, { useState, useEffect } from 'react';
import { 
  Shield, Server, Key, CreditCard, ArrowRightLeft, ReceiptText, 
  History, Bell, CheckCircle2, AlertTriangle, RefreshCw, 
  LogOut, Copy, User, DollarSign, EyeOff
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
  const [envKey, setEnvKey] = useState('docker');
  const [activeTab, setActiveTab] = useState('topology');
  const [toasts, setToasts] = useState([]);
  
  // Auth state
  const [token, setToken] = useState(() => localStorage.getItem('meshbank_token') || '');
  const [user, setUser] = useState(() => JSON.parse(localStorage.getItem('meshbank_user') || 'null'));
  
  // Service Health state
  const [serviceStatus, setServiceStatus] = useState({});
  const [isCheckingHealth, setIsCheckingHealth] = useState(false);

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

  useEffect(() => {
    checkAllServices();
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
          { id: 'topology', label: 'Service Mesh & Health', icon: Server },
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

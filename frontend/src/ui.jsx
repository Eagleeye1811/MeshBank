// Small shared building blocks. One look, one vocabulary for statuses.
import { AlertTriangle, CheckCircle2, Clock, Info, Loader2, XCircle } from 'lucide-react';
import { STATUS } from './styles.js';

export function Card({ title, subtitle, action, children, className = '' }) {
  return (
    <section className={`bg-white rounded-2xl border border-slate-200 shadow-sm ${className}`}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 px-5 pt-5">
          <div>
            {title && <h2 className="text-base font-semibold text-slate-900">{title}</h2>}
            {subtitle && <p className="text-sm text-slate-500 mt-0.5">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

const BUTTON_STYLES = {
  primary: 'bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-300',
  secondary: 'bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 disabled:text-slate-400',
  quiet: 'text-blue-700 hover:bg-blue-50 disabled:text-slate-400',
};

export function Button({ variant = 'primary', busy = false, children, className = '', ...props }) {
  return (
    <button
      {...props}
      disabled={busy || props.disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold
        transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2
        disabled:cursor-not-allowed ${BUTTON_STYLES[variant]} ${className}`}
    >
      {busy && <Loader2 className="w-4 h-4 animate-spin" />}
      {children}
    </button>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-slate-700 mb-1.5">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500 mt-1.5">{hint}</span>}
    </label>
  );
}

const TONES = {
  good: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  warn: 'bg-amber-50 text-amber-800 ring-amber-200',
  bad: 'bg-rose-50 text-rose-700 ring-rose-200',
  neutral: 'bg-slate-100 text-slate-700 ring-slate-200',
};

export function StatusBadge({ status }) {
  const s = STATUS[status] || { label: status, tone: 'neutral' };
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ${TONES[s.tone]}`}>
      {s.label}
    </span>
  );
}

const NOTICE_ICONS = { good: CheckCircle2, warn: AlertTriangle, bad: XCircle, neutral: Clock, info: Info };
const NOTICE_STYLES = {
  good: 'bg-emerald-50 border-emerald-200 text-emerald-900',
  warn: 'bg-amber-50 border-amber-200 text-amber-900',
  bad: 'bg-rose-50 border-rose-200 text-rose-900',
  neutral: 'bg-slate-50 border-slate-200 text-slate-800',
  info: 'bg-blue-50 border-blue-200 text-blue-900',
};

export function Notice({ tone = 'info', title, children }) {
  const Icon = NOTICE_ICONS[tone];
  return (
    <div className={`flex gap-3 rounded-xl border p-4 text-sm ${NOTICE_STYLES[tone]}`} role="status">
      <Icon className="w-5 h-5 shrink-0 mt-0.5" />
      <div className="space-y-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className="leading-relaxed">{children}</div>}
      </div>
    </div>
  );
}

export function Toasts({ toasts }) {
  return (
    <div className="fixed bottom-4 right-4 left-4 sm:left-auto z-50 flex flex-col gap-2 sm:w-96" aria-live="polite">
      {toasts.map(t => (
        <Notice key={t.id} tone={t.tone}>{t.message}</Notice>
      ))}
    </div>
  );
}

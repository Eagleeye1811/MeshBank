// Shared class names and status vocabulary (kept out of ui.jsx: component files
// should export only components, for React fast refresh).

export const inputClass =
  'w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-900 ' +
  'focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500';

// Banking statuses in plain words.
export const STATUS = {
  COMPLETED: { label: 'Completed', tone: 'good' },
  UNDER_REVIEW: { label: 'Held for review', tone: 'warn' },
  PROCESSING: { label: 'Processing', tone: 'neutral' },
  PENDING: { label: 'Processing', tone: 'neutral' },
  REJECTED: { label: 'Declined', tone: 'bad' },
  FAILED: { label: 'Failed', tone: 'bad' },
  REVERSED: { label: 'Refunded', tone: 'neutral' },
};

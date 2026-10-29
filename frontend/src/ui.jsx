import { useEffect, useState } from 'react';

export const NAV_ITEMS = [
  { id: 'dashboard', label: 'Overview', icon: '⌂', section: 'Workspace' },
  { id: 'datasets', label: 'Dataset registry', icon: '▤', section: 'Workspace' },
  { id: 'rights', label: 'Rights checker', icon: '✓', section: 'Governance' },
  { id: 'review', label: 'Evidence review', icon: '⊙', section: 'Governance' },
  { id: 'ledger', label: 'Rights ledger', icon: '▧', section: 'Governance' },
  { id: 'costs', label: 'Cost allocation', icon: '＄', section: 'Planning' },
  { id: 'optimizer', label: 'Optimizer', icon: '⌘', section: 'Planning' },
  { id: 'quantum', label: 'Quantum experiment', icon: '◉', section: 'Research' },
  { id: 'manifests', label: 'Corpus manifests', icon: '▱', section: 'Research' },
  { id: 'audit', label: 'Audit log', icon: '≋', section: 'System' },
  { id: 'evaluation', label: 'Evaluation', icon: '◫', section: 'System' },
];

export function useLoad(loader, dependencies = []) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    loader()
      .then((value) => { if (active) setData(value); })
      .catch((reason) => { if (active) setError(reason.message || 'Unable to load data.'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // Loaders are stable within each page and dependencies describe their inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, reloadToken]);

  return { data, loading, error, reload: () => setReloadToken((value) => value + 1), setData };
}

export function PageHeader({ eyebrow, title, description, action }) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {action && <div className="page-action">{action}</div>}
    </div>
  );
}

export function Card({ children, className = '', title, subtitle, action, ...props }) {
  return (
    <section className={`card ${className}`} {...props}>
      {(title || action) && (
        <div className="card-heading">
          <div>{title && <h2>{title}</h2>}{subtitle && <p>{subtitle}</p>}</div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function StatCard({ label, value, detail, icon, tone = 'blue' }) {
  return (
    <div className={`stat-card tone-${tone}`}>
      <div className="stat-top"><span>{label}</span><span className="stat-icon" aria-hidden="true">{icon}</span></div>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

export function Badge({ children, tone = 'neutral', dot = false }) {
  return <span className={`badge badge-${tone}`}>{dot && <i aria-hidden="true" />}{children}</span>;
}

export function DecisionBadge({ decision }) {
  const normalized = String(decision || '').toUpperCase();
  const tone = normalized === 'APPROVED' ? 'success' : normalized === 'REJECTED' ? 'danger' : normalized === 'NEEDS_REVIEW' ? 'warning' : 'neutral';
  const label = normalized === 'NEEDS_REVIEW' ? 'Needs review' : normalized ? normalized[0] + normalized.slice(1).toLowerCase() : 'Not evaluated';
  return <Badge tone={tone} dot>{label}</Badge>;
}

export function LoadingState({ label = 'Loading data…' }) {
  return <div className="state-panel" role="status"><span className="spinner" />{label}</div>;
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="state-panel state-error" role="alert">
      <span className="state-symbol">!</span>
      <div><strong>We couldn’t load this data.</strong><p>{message}</p></div>
      {onRetry && <button className="button button-secondary button-small" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function EmptyState({ title = 'Nothing here yet', description, action }) {
  return (
    <div className="empty-state">
      <div className="empty-mark">○</div>
      <strong>{title}</strong>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}

export function InlineNotice({ tone = 'info', children }) {
  return <div className={`inline-notice notice-${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>{children}</div>;
}

export function Button({ children, variant = 'primary', className = '', ...props }) {
  return <button className={`button button-${variant} ${className}`} {...props}>{children}</button>;
}

export function Field({ label, hint, children, className = '' }) {
  return <label className={`field ${className}`}><span className="field-label">{label}</span>{children}{hint && <small className="field-hint">{hint}</small>}</label>;
}

export function formatMoney(value, currency = 'USD') {
  if (value === null || value === undefined || value === '') return '—';
  const amount = Number(value);
  if (!Number.isFinite(amount)) return String(value);
  return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 2 }).format(amount);
}

export function formatDate(value, options = {}) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat('en', { dateStyle: 'medium', ...options }).format(date);
}

export function shortReference(value) {
  if (!value) return '—';
  const text = String(value);
  return text.length > 16 ? `${text.slice(0, 8)}…${text.slice(-5)}` : text;
}

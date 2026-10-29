import { useEffect, useMemo, useState } from 'react';
import { API_BASE_URL, api } from './api.js';
import {
  Badge,
  NAV_ITEMS,
  PageHeader,
} from './ui.jsx';
import {
  AuditPage,
  CostsPage,
  DashboardPage,
  DatasetsPage,
  EvidenceReviewPage,
  EvaluationPage,
  LedgerPage,
  ManifestsPage,
  OptimizerPage,
  QuantumPage,
  RightsPage,
} from './pages.jsx';

const PAGE_META = {
  dashboard: ['Workspace', 'A clear view of dataset rights, evidence, and allocation activity.'],
  datasets: ['Governance', 'Register and inspect datasets in the local registry.'],
  rights: ['Governance', 'Evaluate a dataset request through the existing licensing engine.'],
  review: ['Governance', 'Review submitted evidence through a backend-authorized workflow.'],
  ledger: ['Governance', 'Inspect signed rights assertions stored by the backend.'],
  costs: ['Planning', 'Allocate synthetic demo licensing costs against a budget.'],
  optimizer: ['Planning', 'Compare eligible dataset selections using the configured solver.'],
  quantum: ['Research', 'Run a seeded local statevector experiment and compare it with the classical baseline.'],
  manifests: ['Research', 'Review decision manifests generated from current backend evaluations.'],
  audit: ['System', 'Inspect actual audit events and verify the local hash chain.'],
  evaluation: ['System', 'Review measured evaluation coverage without inferred metrics.'],
};

export default function App() {
  const [page, setPage] = useState('dashboard');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [backend, setBackend] = useState({ state: 'checking', label: 'Checking backend' });
  const currentNav = useMemo(() => NAV_ITEMS.find((item) => item.id === page) || NAV_ITEMS[0], [page]);
  const [eyebrow, description] = PAGE_META[page] || PAGE_META.dashboard;

  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        await api.health();
        if (active) setBackend({ state: 'connected', label: 'Backend connected' });
      } catch {
        if (active) setBackend({ state: 'disconnected', label: 'Backend unavailable' });
      }
    };
    check();
    const timer = window.setInterval(check, 30_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const navigate = (nextPage) => {
    setPage(nextPage);
    setSidebarOpen(false);
  };

  let content;
  switch (page) {
    case 'datasets': content = <DatasetsPage />; break;
    case 'rights': content = <RightsPage />; break;
    case 'review': content = <EvidenceReviewPage />; break;
    case 'ledger': content = <LedgerPage />; break;
    case 'costs': content = <CostsPage />; break;
    case 'optimizer': content = <OptimizerPage />; break;
    case 'quantum': content = <QuantumPage />; break;
    case 'manifests': content = <ManifestsPage />; break;
    case 'audit': content = <AuditPage />; break;
    case 'evaluation': content = <EvaluationPage />; break;
    default: content = <DashboardPage onNavigate={navigate} backend={backend} />;
  }

  const groups = [...new Set(NAV_ITEMS.map((item) => item.section))];

  return (
    <div className="app-shell">
      {sidebarOpen && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
      <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
        <div className="brand-lockup">
          <div className="brand-mark"><span>B</span><i /></div>
          <div><strong>BhashaTrust</strong><small>AI GOVERNANCE</small></div>
        </div>
        <div className="workspace-chip"><span className="workspace-dot" />Local workspace</div>
        <nav className="side-nav" aria-label="Main navigation">
          {groups.map((group) => (
            <div className="nav-group" key={group}>
              <div className="nav-section-label">{group}</div>
              {NAV_ITEMS.filter((item) => item.section === group).map((item) => (
                <button
                  key={item.id}
                  className={`nav-item ${page === item.id ? 'nav-item-active' : ''}`}
                  onClick={() => navigate(item.id)}
                  aria-current={page === item.id ? 'page' : undefined}
                >
                  <span className="nav-icon" aria-hidden="true">{item.icon}</span>
                  <span>{item.label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="footer-shield">✓</div>
          <div><strong>Governance first</strong><small>Rights are checked by the backend.</small></div>
        </div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div className="topbar-left">
            <button className="mobile-menu" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}>☰</button>
            <div className="breadcrumbs"><span>Workspace</span><b>/</b><strong>{currentNav.label}</strong></div>
          </div>
          <div className="topbar-right">
            <span className="api-url">API <code>{API_BASE_URL.replace(/^https?:\/\//, '')}</code></span>
            <Badge tone={backend.state === 'connected' ? 'success' : backend.state === 'disconnected' ? 'danger' : 'neutral'} dot>
              {backend.label}
            </Badge>
            <div className="avatar" aria-label="BhashaTrust workspace">BT</div>
          </div>
        </header>
        <main className="main-content">
          <PageHeader eyebrow={eyebrow} title={currentNav.label} description={description} />
          {content}
          <footer className="app-footer">
            <span>BhashaTrust AI <i>•</i> Local governance workspace</span>
            <span>Decision logic and evidence checks run in the backend.</span>
          </footer>
        </main>
      </div>
    </div>
  );
}

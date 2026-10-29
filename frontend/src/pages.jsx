import { useEffect, useMemo, useState } from 'react';
import { api } from './api.js';
import {
  Badge,
  Button,
  Card,
  DecisionBadge,
  EmptyState,
  ErrorState,
  Field,
  formatDate,
  formatMoney,
  InlineNotice,
  LoadingState,
  shortReference,
  StatCard,
  useLoad,
} from './ui.jsx';

const DEFAULT_PURPOSE = 'research_training';
const DEFAULT_CONTEXT = 'research';

function PageError({ error, reload }) {
  return error ? <ErrorState message={error} onRetry={reload} /> : null;
}

function LoadingOrEmpty({ loading, error, reload, empty, children, emptyTitle, emptyDescription }) {
  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (empty) return <EmptyState title={emptyTitle} description={emptyDescription} />;
  return children;
}

function EmptyTable({ cols, title, description }) {
  return <tr><td colSpan={cols}><EmptyState title={title} description={description} /></td></tr>;
}

function AuditRow({ event }) {
  const payload = event.payload || {};
  const decision = payload.decision || payload.result?.decision;
  return (
    <div className="activity-row">
      <div className={`activity-icon activity-${String(event.event_type || '').includes('reject') ? 'amber' : 'blue'}`}>{event.event_type?.startsWith('rights') ? '✓' : '↗'}</div>
      <div className="activity-copy">
        <strong>{(event.event_type || 'Event').replaceAll('_', ' ')}</strong>
        <span>{event.entity_type} · {event.entity_id}</span>
      </div>
      {decision && <DecisionBadge decision={decision} />}
      <time>{formatDate(event.created_at)}</time>
    </div>
  );
}

export function DashboardPage({ onNavigate, backend }) {
  const datasets = useLoad(() => api.datasets());
  const summary = useLoad(() => api.dashboardSummary(DEFAULT_PURPOSE, DEFAULT_CONTEXT));
  const stats = summary.data;
  const total = stats?.dataset_count || 0;
  const reviewed = (stats?.approved_count || 0) + (stats?.rejected_count || 0) + (stats?.needs_review_count || 0);
  const percent = (value) => reviewed ? `${(100 * value / reviewed).toFixed(1)}%` : '0%';

  return (
    <div className="page-stack">
      <div className="welcome-banner">
        <div className="welcome-orb orb-one" /><div className="welcome-orb orb-two" />
        <div className="welcome-copy">
          <span className="welcome-kicker"><span className="welcome-kicker-dot" /> TRUST &amp; RIGHTS CONTROL CENTER</span>
          <h2>Make every dataset decision<br /><em>traceable and responsible.</em></h2>
          <p>Review rights, manage evidence, and plan eligible training corpora from one workspace.</p>
          <Button onClick={() => onNavigate('rights')}>Check dataset rights <span aria-hidden="true">→</span></Button>
        </div>
        <div className="welcome-visual" aria-hidden="true">
          <div className="visual-ring ring-a" /><div className="visual-ring ring-b" />
          <div className="visual-core"><span>BT</span><i>✓</i></div>
          <span className="orbit-label label-top">RIGHTS</span><span className="orbit-label label-right">EVIDENCE</span><span className="orbit-label label-bottom">AUDIT</span>
        </div>
      </div>

      <div className="section-title-row">
        <div><h2>Governance snapshot</h2><p>Licensing decisions for <code>{DEFAULT_PURPOSE}</code> · {DEFAULT_CONTEXT}</p></div>
        <button className="text-button" onClick={() => { summary.reload(); datasets.reload(); }}>Refresh data <span>↻</span></button>
      </div>

      <PageError error={summary.error || datasets.error} reload={() => { summary.reload(); datasets.reload(); }} />
      <div className="stats-grid">
        <StatCard label="Registered datasets" value={summary.loading ? '—' : total} detail="In the backend registry" icon="▤" tone="blue" />
        <StatCard label="Approved" value={summary.loading ? '—' : stats?.approved_count ?? 0} detail={`${percent(stats?.approved_count || 0)} of evaluated`} icon="✓" tone="green" />
        <StatCard label="Needs review" value={summary.loading ? '—' : stats?.needs_review_count ?? 0} detail="Evidence or terms require attention" icon="!" tone="amber" />
        <StatCard label="Rejected" value={summary.loading ? '—' : stats?.rejected_count ?? 0} detail="Not permitted for this scope" icon="×" tone="rose" />
      </div>

      <div className="dashboard-grid">
        <Card className="decision-card" title="Decision distribution" subtitle={`Live licensing-engine results · ${DEFAULT_PURPOSE}`}>
          {summary.loading ? <LoadingState /> : summary.error ? <ErrorState message={summary.error} onRetry={summary.reload} /> : reviewed === 0 ? (
            <EmptyState title="No datasets evaluated" description="Register a dataset to see current decisions for this training scope." action={<Button variant="secondary" onClick={() => onNavigate('datasets')}>Open registry</Button>} />
          ) : (
            <>
              <div className="decision-bar" role="img" aria-label={`${stats.approved_count} approved, ${stats.needs_review_count} needing review, ${stats.rejected_count} rejected`}>
                <span className="bar-approved" style={{ width: percent(stats.approved_count) }} />
                <span className="bar-review" style={{ width: percent(stats.needs_review_count) }} />
                <span className="bar-rejected" style={{ width: percent(stats.rejected_count) }} />
              </div>
              <div className="legend-list">
                <div><span className="legend-dot dot-approved" /><span>Approved</span><strong>{stats.approved_count}</strong></div>
                <div><span className="legend-dot dot-review" /><span>Needs review</span><strong>{stats.needs_review_count}</strong></div>
                <div><span className="legend-dot dot-rejected" /><span>Rejected</span><strong>{stats.rejected_count}</strong></div>
              </div>
              <div className="scope-note">Scope: {stats.decision_scope.purpose} · {stats.decision_scope.context}</div>
            </>
          )}
        </Card>

        <Card className="cost-card" title="Licensing cost overview" subtitle="Synthetic demo costs from structured backend data">
          <div className="cost-overview-number">{summary.loading ? '—' : formatMoney(stats?.total_licensing_cost)}</div>
          <div className="cost-overview-note"><span className="cost-mini-icon">＄</span><span>Known synthetic demo costs across registered datasets</span></div>
          <div className="cost-divider" />
          <div className="cost-detail-row"><span>Priced datasets</span><strong>{summary.loading ? '—' : stats?.synthetic_cost_dataset_count ?? 0}</strong></div>
          <button className="text-button" onClick={() => onNavigate('costs')}>Open cost allocation <span>→</span></button>
        </Card>

        <Card className="audit-card" title="Recent audit activity" subtitle="Latest events recorded by the backend" action={<button className="text-button" onClick={() => onNavigate('audit')}>View all <span>→</span></button>}>
          {summary.loading ? <LoadingState /> : summary.error ? <ErrorState message={summary.error} onRetry={summary.reload} /> : (stats?.recent_audit_activity || []).length === 0 ? (
            <EmptyState title="No audit activity yet" description="Backend decisions and actions will appear here." />
          ) : <div className="activity-list">{stats.recent_audit_activity.slice(0, 5).map((event) => <AuditRow event={event} key={event.id} />)}</div>}
        </Card>

        <Card className="optimization-card" title="Latest optimization" subtitle="Result stored by the backend">
          {summary.loading ? <LoadingState /> : summary.error ? <ErrorState message={summary.error} onRetry={summary.reload} /> : !stats?.latest_optimization ? (
            <EmptyState title="No optimization runs" description="Run an optimization to compare eligible datasets under a budget." action={<Button variant="secondary" onClick={() => onNavigate('optimizer')}>Open optimizer</Button>} />
          ) : (
            <div className="optimization-summary">
              <div className="optimization-summary-top"><Badge tone={stats.latest_optimization.solver_used === 'qpiai_opt' ? 'violet' : 'blue'} dot>{stats.latest_optimization.solver_used === 'qpiai_opt' ? 'QpiAI Opt' : 'Local baseline'}</Badge><span>{formatDate(stats.latest_optimization.created_at)}</span></div>
              <div className="optimization-metrics"><div><strong>{stats.latest_optimization.total_utility}</strong><small>Utility</small></div><div><strong>{formatMoney(stats.latest_optimization.total_licensing_cost)}</strong><small>Selected cost</small></div></div>
              <p>{(stats.latest_optimization.selected_dataset_ids || []).length} dataset{(stats.latest_optimization.selected_dataset_ids || []).length === 1 ? '' : 's'} selected</p>
            </div>
          )}
        </Card>
      </div>

      <Card
        title="Before Voice AI Can Learn: Verify the Rights to Its Speech Data."
        subtitle="A governance layer for an organization's own speech-data ingestion and training pipeline."
      >
        <p>
          Speech recognition, transcription, conversational audio, translation, and speech generation
          depend on suitable data and complex model and infrastructure choices. BhashaTrust AI checks
          dataset rights before an integrating organization admits data to its own pipeline; it is not
          a speech model and cannot change another company's private training systems or voice quality.
        </p>
        <ol className="architecture-flow">
          <li>Speech dataset sources</li>
          <li>Metadata and provenance</li>
          <li>License and evidence assessment</li>
          <li>Purpose-specific rights decision</li>
          <li>Backend eligibility enforcement</li>
          <li>Approved training-data manifest</li>
          <li>Organization's training pipeline</li>
          <li>Audit and rights monitoring</li>
        </ol>
        <InlineNotice tone="info">
          The integrating organization must enforce this gate in its own ingestion controller. A
          standalone website cannot prevent data use outside that integration.
        </InlineNotice>
      </Card>

      <div className="integration-strip">
        <div className="integration-mark">i</div><div><strong>External solver status</strong><p>QpiAI Opt and QCloud live authentication have not been verified. Solver results are labeled by the backend's actual execution mode.</p></div>
        <Badge tone={backend.state === 'connected' ? 'success' : 'danger'}>{backend.state === 'connected' ? 'API online' : 'API offline'}</Badge>
      </div>
    </div>
  );
}

function DatasetDetails({ id, onClose }) {
  const detail = useLoad(() => api.dataset(id), [id]);
  const provenanceEvidence = useLoad(() => api.evidenceStatus(id), [id]);
  const eligibility = useLoad(
    () => api.eligibleDatasets({
      purpose: DEFAULT_PURPOSE,
      context: DEFAULT_CONTEXT,
      dataset_ids: [Number(id)],
    }),
    [id],
  );
  const ledger = useLoad(() => api.rightsRecords(id), [id]);
  const [rightsBusy, setRightsBusy] = useState(false);
  const [rightsError, setRightsError] = useState('');
  const [rightsNotice, setRightsNotice] = useState('');
  const [evidenceBusy, setEvidenceBusy] = useState(false);
  const [evidenceError, setEvidenceError] = useState('');
  const [evidenceNotice, setEvidenceNotice] = useState('');
  const submitEvidence = async (event) => {
    event.preventDefault();
    setEvidenceBusy(true);
    setEvidenceError('');
    setEvidenceNotice('');
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      const evidence = await api.submitEvidence(id, {
        evidence_type: form.get('evidence_type'),
        description: form.get('evidence_description'),
        reference: form.get('evidence_reference'),
        submitter_id: form.get('submitter_id') || null,
      });
      setEvidenceNotice(`Evidence #${evidence.id} was submitted for trusted review.`);
      formElement.reset();
    } catch (reason) {
      setEvidenceError(reason.message);
    } finally {
      setEvidenceBusy(false);
    }
  };
  const submitRights = async (event) => {
    event.preventDefault();
    setRightsBusy(true);
    setRightsError('');
    setRightsNotice('');
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      const payload = JSON.parse(String(form.get('payload') || ''));
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('Rights payload must be a JSON object.');
      }
      if (Number(payload.dataset_id) !== Number(id)) {
        throw new Error('The signed payload dataset_id must match this dataset.');
      }
      const result = await api.submitRightsRecord(id, {
        payload,
        signer_id: form.get('signer_id'),
        public_key: form.get('public_key'),
        signature: form.get('signature'),
      });
      setRightsNotice(result.signature_valid ? 'Signed claim stored; the signature is valid, but legal authority is not independently verified.' : 'Claim stored, but its signature is invalid.');
      formElement.reset();
      ledger.reload();
    } catch (reason) {
      setRightsError(reason instanceof SyntaxError ? 'Rights payload must be valid JSON.' : reason.message);
    } finally {
      setRightsBusy(false);
    }
  };
  return (
    <div className="detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="dataset-detail-title">
        <div className="drawer-heading"><div><span className="eyebrow">DATASET DETAILS</span><h2 id="dataset-detail-title">Dataset #{id}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close details">×</button></div>
        {detail.loading ? <LoadingState /> : detail.error ? <ErrorState message={detail.error} onRetry={detail.reload} /> : (
          <>
            <div className="detail-title-block"><h3>{detail.data.name}</h3><p>{detail.data.description || 'No description provided.'}</p></div>
            <dl className="detail-list">
              <div><dt>Language</dt><dd>{detail.data.language}</dd></div>
              <div><dt>Registrant rights-holder claim</dt><dd>{detail.data.owner || 'Not recorded'} — not independently verified</dd></div>
              <div><dt>License claim</dt><dd>{detail.data.license_name}{detail.data.license_version ? ` · ${detail.data.license_version}` : ''}</dd></div>
              <div><dt>Permitted purposes</dt><dd>{detail.data.permitted_purposes?.join(', ') || 'Not specified'}</dd></div>
              <div><dt>Commercial use</dt><dd>{detail.data.commercial_use === null ? 'Unspecified' : detail.data.commercial_use ? 'Allowed' : 'Not permitted'}</dd></div>
              <div><dt>Redistribution</dt><dd>{detail.data.redistribution_allowed === null ? 'Unspecified' : detail.data.redistribution_allowed ? 'Allowed' : 'Not permitted'}</dd></div>
              <div><dt>Derivative works</dt><dd>{detail.data.derivative_work_allowed === null ? 'Unspecified' : detail.data.derivative_work_allowed ? 'Allowed' : 'Not permitted'}</dd></div>
              <div><dt>Evidence status</dt><dd>{detail.data.evidence_independently_verified ? 'Independently verified' : detail.data.evidence_verified ? 'Registry claim — not independently verified' : 'Unverified'}</dd></div>
              <div><dt>Expires</dt><dd>{detail.data.expires_on || 'No expiry date recorded'}</dd></div>
            </dl>
            <div className="drawer-section">
              <div className="drawer-section-heading"><h3>Provenance metadata</h3><Badge tone={detail.data.synthetic_test_fixture ? 'warning' : 'neutral'}>{detail.data.synthetic_test_fixture ? 'Synthetic test fixture' : detail.data.provenance_status === 'REGISTRANT_SUPPLIED' ? 'Registrant supplied' : 'Not recorded'}</Badge></div>
              <p className="muted-copy">Source and collection metadata are registrant claims, not independently verified facts or evidence of training rights.</p>
              <dl className="detail-list">
                <div><dt>Source</dt><dd>{detail.data.source_name || 'Not recorded'}</dd></div>
                <div><dt>Source dataset / version</dt><dd>{[detail.data.source_dataset_id, detail.data.source_version].filter(Boolean).join(' · ') || 'Not recorded'}</dd></div>
                <div><dt>Source URL</dt><dd>{detail.data.source_url ? <a href={detail.data.source_url} target="_blank" rel="noreferrer">{detail.data.source_url}</a> : 'Not recorded'}</dd></div>
                <div><dt>Language variety</dt><dd>{detail.data.language_variety || 'Not recorded'}</dd></div>
                <div><dt>Modality</dt><dd>{detail.data.modalities?.join(', ') || 'Not recorded'}</dd></div>
                <div><dt>Collection method / period</dt><dd>{[detail.data.collection_method, detail.data.collection_period].filter(Boolean).join(' · ') || 'Not recorded'}</dd></div>
                <div><dt>Data contributor claim</dt><dd>{detail.data.data_contributor_claim || 'Not recorded'}</dd></div>
                <div><dt>Intended-use notes</dt><dd>{detail.data.intended_use_notes || 'Not recorded'}</dd></div>
                <div><dt>Known restrictions</dt><dd>{detail.data.known_restrictions || 'Not recorded'}</dd></div>
                <div><dt>Metadata origin / timestamp</dt><dd>{detail.data.metadata_origin || 'Not recorded'}{detail.data.metadata_claimed_at ? ` · ${formatDate(detail.data.metadata_claimed_at)}` : ''}</dd></div>
              </dl>
              {detail.data.provenance_notes && <p className="muted-copy">Registrant notes: {detail.data.provenance_notes}</p>}
            </div>
            <div className="drawer-section">
              <div className="drawer-section-heading"><h3>Evidence submitted for review</h3><span>{provenanceEvidence.data?.submissions?.length ?? 0}</span></div>
              <p className="muted-copy">Evidence review is distinct from registrant metadata and does not by itself establish legal ownership or authority.</p>
              {provenanceEvidence.loading ? <LoadingState label="Loading evidence status…" /> : provenanceEvidence.error ? <ErrorState message={provenanceEvidence.error} onRetry={provenanceEvidence.reload} /> : (provenanceEvidence.data?.submissions || []).length === 0 ? <p className="muted-copy">No evidence submissions recorded.</p> : (provenanceEvidence.data.submissions || []).map((item) => (
                <div className="record-card" key={item.id}>
                  <div><strong>Evidence #{item.id} · {item.evidence_type}</strong><Badge tone={item.review_status === 'APPROVED' ? 'success' : item.review_status === 'REJECTED' ? 'danger' : 'warning'}>{item.review_status.replaceAll('_', ' ')}</Badge></div>
                  <p>Submitted {formatDate(item.submitted_at)}{item.reviewed_at ? ` · reviewed ${formatDate(item.reviewed_at)}` : ''}</p>
                  {item.review_reason && <small>{item.review_reason}</small>}
                  <small>Evidence reference is hidden here; authorized reviewers use the review queue.</small>
                </div>
              ))}
            </div>
            <div className="drawer-section">
              <div className="drawer-section-heading"><h3>Current policy eligibility</h3><span>{DEFAULT_PURPOSE} · {DEFAULT_CONTEXT}</span></div>
              {eligibility.loading ? <LoadingState label="Checking backend eligibility…" /> : eligibility.error ? <ErrorState message={eligibility.error} onRetry={eligibility.reload} /> : (() => {
                const result = eligibility.data.eligible_datasets[0] || eligibility.data.excluded_datasets[0];
                return result ? <div className="record-card"><DecisionBadge decision={result.decision} /><p>{result.explanation}</p><small>{result.reason_codes.join(', ') || 'No reason codes'}</small></div> : <p className="muted-copy">No eligibility result was returned.</p>;
              })()}
            </div>
          </>
        )}
        <div className="drawer-section"><div className="drawer-section-heading"><h3>Rights ledger records</h3><span>{ledger.data?.length ?? 0}</span></div>
          {ledger.loading ? <LoadingState label="Loading ledger records…" /> : ledger.error ? <ErrorState message={ledger.error} onRetry={ledger.reload} /> : (ledger.data || []).length === 0 ? <p className="muted-copy">No signed records are stored for this dataset.</p> : ledger.data.map((record) => (
            <div className="record-card" key={record.id}><div><strong>Record #{record.id} · {record.signer_id}</strong><Badge tone={record.signature_valid ? 'success' : 'danger'}>{record.signature_valid ? 'Signature valid' : 'Signature invalid'}</Badge></div><p>{record.payload.license_name} · {formatDate(record.created_at)}</p><small>{record.ownership_proven ? 'Ownership proven' : 'A valid signature does not prove legal ownership.'}</small></div>
          ))}
        </div>
        <div className="drawer-section">
          <h3>Submit evidence for review</h3>
          <p className="muted-copy">References are stored as metadata and never fetched by the backend. Use a public HTTPS reference without credentials or query parameters; submission does not prove the claim.</p>
          {evidenceError && <InlineNotice tone="danger">{evidenceError}</InlineNotice>}
          {evidenceNotice && <InlineNotice tone="info">{evidenceNotice}</InlineNotice>}
          <form className="stacked-form" onSubmit={submitEvidence}>
            <Field label="Evidence type"><input name="evidence_type" required maxLength="80" placeholder="License document" /></Field>
            <Field label="Description"><textarea name="evidence_description" required maxLength="500" rows="2" /></Field>
            <Field label="HTTPS evidence reference" hint="No local paths, embedded credentials, query strings, or fragments."><input name="evidence_reference" type="url" required maxLength="2048" placeholder="https://example.org/license" /></Field>
            <Field label="Submitter identifier" hint="Optional and self-reported; it is not authentication."><input name="submitter_id" maxLength="120" /></Field>
            <Button type="submit" disabled={evidenceBusy}>{evidenceBusy ? 'Submitting…' : 'Submit evidence'}</Button>
          </form>
        </div>
        <div className="drawer-section">
          <h3>Submit a signed rights claim</h3>
          <p className="muted-copy">Paste an externally signed payload, public key, and signature. The private key is never requested or sent. A valid signature does not establish legal authority or independent verification.</p>
          {rightsError && <InlineNotice tone="danger">{rightsError}</InlineNotice>}
          {rightsNotice && <InlineNotice tone="info">{rightsNotice}</InlineNotice>}
          <form className="stacked-form" onSubmit={submitRights}>
            <Field label="Signer ID"><input name="signer_id" required maxLength="200" /></Field>
            <Field label="Rights payload JSON" hint="Use the canonical payload that was signed; dataset_id must match this record."><textarea name="payload" rows="7" required /></Field>
            <Field label="Public key (base64)"><input name="public_key" required /></Field>
            <Field label="Signature (base64)"><input name="signature" required /></Field>
            <Button type="submit" disabled={rightsBusy}>{rightsBusy ? 'Verifying and saving…' : 'Submit signed claim'}</Button>
          </form>
        </div>
      </section>
    </div>
  );
}

function DatasetRegistrationForm({ onRegistered, onCancel }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(null);
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError(''); setSaved(null);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const purposes = String(form.get('purposes') || '').split(',').map((item) => item.trim()).filter(Boolean);
    const geography = String(form.get('geography') || '').split(',').map((item) => item.trim()).filter(Boolean);
    const commercial = form.get('commercial_use');
    const modalities = String(form.get('modalities') || '').split(',').map((item) => item.trim()).filter(Boolean);
    try {
      const result = await api.registerDataset({
        name: form.get('name'), language: form.get('language'), owner: form.get('owner'), description: form.get('description') || '',
        license_name: form.get('license_name'), permitted_purposes: purposes,
        commercial_use: commercial === '' ? null : commercial === 'true',
        redistribution_allowed: form.get('redistribution_allowed') === '' ? null : form.get('redistribution_allowed') === 'true',
        derivative_work_allowed: form.get('derivative_work_allowed') === '' ? null : form.get('derivative_work_allowed') === 'true',
        geographic_restrictions: geography, expires_on: form.get('expires_on') || null,
        revoked: false, evidence_verified: false, synthetic: form.get('synthetic') === 'on',
        source_name: form.get('source_name') || null,
        source_url: form.get('source_url') || null,
        source_dataset_id: form.get('source_dataset_id') || null,
        source_version: form.get('source_version') || null,
        language_variety: form.get('language_variety') || null,
        modalities: modalities.length ? modalities : null,
        collection_method: form.get('collection_method') || null,
        data_contributor_claim: form.get('data_contributor_claim') || null,
        license_version: form.get('license_version') || null,
        intended_use_notes: form.get('intended_use_notes') || null,
        known_restrictions: form.get('known_restrictions') || null,
        collection_period: form.get('collection_period') || null,
        provenance_notes: form.get('provenance_notes') || null,
      });
      setSaved(result); formElement.reset(); onRegistered();
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  return (
    <Card title="Register a dataset" subtitle="New registry entries start with unverified evidence.">
      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
      {saved && <InlineNotice tone="success">Dataset #{saved.id} was registered. Check its rights before training.</InlineNotice>}
      <form className="form-grid" onSubmit={submit}>
        <Field label="Dataset name"><input name="name" required maxLength="200" /></Field>
        <Field label="Language"><input name="language" required maxLength="80" placeholder="e.g. Hindi" /></Field>
        <Field label="Registrant rights-holder claim" hint="A claim, not independently verified ownership."><input name="owner" required maxLength="200" /></Field>
        <Field label="License name"><input name="license_name" required maxLength="120" /></Field>
        <Field label="License version"><input name="license_version" maxLength="120" placeholder="If known" /></Field>
        <Field label="Source / provider name"><input name="source_name" maxLength="200" /></Field>
        <Field label="Original source URL" hint="Recorded only; the backend does not fetch this URL."><input name="source_url" type="url" maxLength="2048" placeholder="https://example.org/dataset-card" /></Field>
        <Field label="Source dataset identifier"><input name="source_dataset_id" maxLength="200" /></Field>
        <Field label="Source version / release"><input name="source_version" maxLength="120" /></Field>
        <Field label="Language variety / dialect"><input name="language_variety" maxLength="120" placeholder="If known" /></Field>
        <Field label="Modalities" hint="Comma-separated, e.g. speech_audio, transcription"><input name="modalities" maxLength="500" /></Field>
        <Field label="Collection method"><input name="collection_method" maxLength="200" placeholder="If known" /></Field>
        <Field label="Data contributor claim"><input name="data_contributor_claim" maxLength="200" /></Field>
        <Field label="Permitted purposes" hint="Comma-separated. Example: research_training"><input name="purposes" placeholder="research_training" /></Field>
        <Field label="Commercial use"><select name="commercial_use" defaultValue=""><option value="">Unspecified</option><option value="true">Allowed</option><option value="false">Not permitted</option></select></Field>
        <Field label="Redistribution"><select name="redistribution_allowed" defaultValue=""><option value="">Unspecified</option><option value="true">Allowed</option><option value="false">Not permitted</option></select></Field>
        <Field label="Derivative works"><select name="derivative_work_allowed" defaultValue=""><option value="">Unspecified</option><option value="true">Allowed</option><option value="false">Not permitted</option></select></Field>
        <Field label="Geographic restrictions" hint="Comma-separated, if any"><input name="geography" placeholder="India, EU" /></Field>
        <Field label="License expiry"><input name="expires_on" type="date" /></Field>
        <Field label="Collection date / period"><input name="collection_period" maxLength="120" placeholder="If known" /></Field>
        <Field label="Intended-use notes"><textarea name="intended_use_notes" rows="2" maxLength="1000" /></Field>
        <Field label="Known restrictions"><textarea name="known_restrictions" rows="2" maxLength="1000" /></Field>
        <Field label="Provenance verification notes"><textarea name="provenance_notes" rows="2" maxLength="1000" /></Field>
        <Field label="Description" className="field-wide"><textarea name="description" rows="3" maxLength="2000" /></Field>
        <label className="confirmation-check field-wide"><input type="checkbox" name="synthetic" /><span>Mark this as synthetic demo data</span></label>
        <div className="form-note field-wide"><span>i</span>Metadata is marked registrant-supplied with a server timestamp. Source claims and license labels do not establish rights; URLs are stored without fetching content.</div>
        <div className="form-actions field-wide"><Button type="submit" disabled={busy}>{busy ? 'Registering…' : 'Register dataset'}</Button>{onCancel && <Button type="button" variant="secondary" onClick={onCancel}>Close</Button>}</div>
      </form>
    </Card>
  );
}

export function DatasetsPage() {
  const registry = useLoad(() => api.datasets());
  const summary = useLoad(() => api.dashboardSummary(DEFAULT_PURPOSE, DEFAULT_CONTEXT));
  const [showForm, setShowForm] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const statusMap = Object.fromEntries((summary.data?.dataset_statuses || []).map((item) => [item.dataset_id, item]));
  const costMap = Object.fromEntries((summary.data?.dataset_costs || []).map((item) => [item.dataset_id, item]));
  const refresh = () => { registry.reload(); summary.reload(); };
  return (
    <div className="page-stack">
      <div className="toolbar-row"><div className="result-count">{registry.data?.length ?? 0} registered dataset{registry.data?.length === 1 ? '' : 's'}</div><Button onClick={() => setShowForm((value) => !value)}>{showForm ? 'Hide form' : '+ Register dataset'}</Button></div>
      <div className="scope-banner"><span className="scope-icon">◉</span><span>Status badges reflect a fresh backend check for <strong>{DEFAULT_PURPOSE}</strong> · <strong>{DEFAULT_CONTEXT}</strong>.</span></div>
      {registry.error && <ErrorState message={registry.error} onRetry={refresh} />}
      <Card className="table-card">
        {registry.loading ? <LoadingState /> : (
          <div className="table-wrap"><table><thead><tr><th>Dataset</th><th>Language / variety</th><th>Source / provenance</th><th>Permitted purpose</th><th>Current status</th><th>Evidence</th><th>Demo cost</th><th /></tr></thead>
            <tbody>{(registry.data || []).length === 0 ? <EmptyTable cols={8} title="No datasets registered" description="Register a dataset to start evaluating its rights." /> : registry.data.map((dataset) => (
              <tr key={dataset.id}>
                <td><div className="dataset-name-cell"><span className="dataset-mini">{dataset.language.slice(0, 2).toUpperCase()}</span><div><strong>{dataset.name}</strong><small>ID {dataset.id} · {dataset.license_name}{dataset.license_version ? ` · ${dataset.license_version}` : ''}</small></div></div></td>
                <td>{dataset.language}<small className="sub-label">{dataset.language_variety || 'Variety not recorded'}</small></td>
                <td>{dataset.source_name || 'Source not recorded'}<small className="sub-label">{dataset.synthetic_test_fixture ? 'Synthetic test fixture' : dataset.provenance_status === 'REGISTRANT_SUPPLIED' ? 'Registrant supplied' : 'Not recorded'}</small></td>
                <td><span className="purpose-chip">{dataset.permitted_purposes?.[0] || 'Not specified'}</span>{dataset.permitted_purposes?.length > 1 && <small className="more-count">+{dataset.permitted_purposes.length - 1}</small>}</td>
                <td><DecisionBadge decision={statusMap[dataset.id]?.decision} /></td>
                <td><Badge tone={dataset.evidence_independently_verified ? 'success' : 'warning'}>{dataset.evidence_independently_verified ? 'Independently verified' : dataset.evidence_verified ? 'Registry claim' : 'Unverified'}</Badge></td>
                <td>{costMap[dataset.id] ? <span>{formatMoney(costMap[dataset.id].cost)}<small className="sub-label">synthetic</small></span> : <span className="muted-copy">No cost data</span>}</td>
                <td><button className="table-link" onClick={() => setSelectedId(dataset.id)}>Details <span>→</span></button></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
      {showForm && <DatasetRegistrationForm onRegistered={refresh} onCancel={() => setShowForm(false)} />}
      {selectedId !== null && <DatasetDetails id={selectedId} onClose={() => setSelectedId(null)} />}
    </div>
  );
}

export function RightsPage() {
  const datasets = useLoad(() => api.datasets());
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [lastRequest, setLastRequest] = useState(null);
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError(''); setResult(null);
    const form = new FormData(event.currentTarget);
    const request = {
      dataset_id: Number(form.get('dataset_id')),
      purpose: form.get('purpose'),
      context: form.get('context'),
      geography: form.get('geography') || null,
      redistribution_requested: form.get('redistribution_requested') === 'on',
      derivative_work_requested: form.get('derivative_work_requested') === 'on',
    };
    setLastRequest(request);
    try { setResult(await api.decide(request)); } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  return (
    <div className="two-column-layout page-stack">
      <Card title="Evaluate intended use" subtitle="The backend licensing engine makes the decision. Each check is recorded in the audit log.">
        {datasets.loading ? <LoadingState /> : datasets.error ? <ErrorState message={datasets.error} onRetry={datasets.reload} /> : (datasets.data || []).length === 0 ? <EmptyState title="No datasets to check" description="Register a dataset first." /> : (
          <form className="stacked-form" onSubmit={submit}>
            <Field label="Dataset"><select name="dataset_id" required defaultValue=""><option value="" disabled>Select a dataset</option>{datasets.data.map((dataset) => <option key={dataset.id} value={dataset.id}>#{dataset.id} · {dataset.name}</option>)}</select></Field>
            <Field label="Intended training purpose"><input name="purpose" required defaultValue={DEFAULT_PURPOSE} placeholder="e.g. research_training" /></Field>
            <Field label="Use context"><select name="context" defaultValue="research"><option value="research">Research</option><option value="commercial">Commercial</option></select></Field>
            <Field label="Geographic context" hint="Leave blank if no specific geography applies"><input name="geography" placeholder="e.g. India" /></Field>
            <label className="confirmation-check"><input type="checkbox" name="redistribution_requested" /><span>Redistribution is part of the intended use</span></label>
            <label className="confirmation-check"><input type="checkbox" name="derivative_work_requested" /><span>Creating derivative works is part of the intended use</span></label>
            <InlineNotice tone="info">A valid signature verifies the signed claim's integrity, not legal authority. Approval requires independently verified evidence.</InlineNotice>
            {error && <InlineNotice tone="danger">{error}</InlineNotice>}
            <Button type="submit" disabled={busy || datasets.loading}>{busy ? 'Checking rights…' : 'Evaluate with backend'}</Button>
          </form>
        )}
      </Card>
      <Card title="Decision result" subtitle={lastRequest ? `Dataset #${lastRequest.dataset_id} · ${lastRequest.purpose}` : 'Run an evaluation to see current policy results.'}>
        {busy ? <LoadingState label="Evaluating rights…" /> : !result ? <EmptyState title="Awaiting a rights check" description="Decisions, explanations, and evidence status are returned directly by the licensing engine." /> : (
          <div className="decision-result">
            <div className="decision-result-top"><DecisionBadge decision={result.decision} /><span>Policy {result.policy_version}</span></div>
            <p className="decision-explanation">{result.human_readable_explanation}</p>
            <div className="result-fields"><div><span>Evidence status</span><strong>{result.evidence_verification_status}</strong></div><div><span>Audit reference</span><strong>#{result.audit_event_reference ?? '—'}</strong></div><div><span>Rights record</span><strong>{result.rights_ledger_reference ? `#${result.rights_ledger_reference.record_id}` : 'None applied'}</strong></div></div>
            <div className="reason-code-block"><span>Reason codes</span><div>{result.reason_codes?.length ? result.reason_codes.map((code) => <code key={code}>{code}</code>) : <span className="muted-copy">No reason codes returned</span>}</div></div>
            <div className="ownership-note">This response is a policy evaluation, not a legal ownership determination.</div>
          </div>
        )}
      </Card>
    </div>
  );
}

export function EvidenceReviewPage() {
  const status = useLoad(() => api.reviewerStatus());
  const [token, setToken] = useState('');
  const [queue, setQueue] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busyId, setBusyId] = useState(null);
  const loadQueue = async (event) => {
    event.preventDefault();
    setLoading(true);
    setError('');
    setNotice('');
    try {
      setQueue(await api.evidenceReviewQueue(token));
    } catch (reason) {
      setQueue(null);
      setError(reason.message);
    } finally {
      setLoading(false);
    }
  };
  const submitReview = async (item, decision, form) => {
    const formData = new FormData(form);
    setBusyId(item.evidence.id);
    setError('');
    setNotice('');
    try {
      const result = await api.reviewEvidence(item.evidence.id, token, {
        decision,
        reason: formData.get('reason'),
      });
      setNotice(`Evidence #${result.evidence.id}: ${result.evidence.review_status.replaceAll('_', ' ').toLowerCase()}. Dataset evidence ${result.dataset_evidence_approved ? 'is fully reviewed' : 'remains unapproved'}.`);
      setQueue(await api.evidenceReviewQueue(token));
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusyId(null);
    }
  };
  return (
    <div className="page-stack">
      {status.loading ? <LoadingState label="Checking reviewer configuration…" /> : status.error ? <ErrorState message={status.error} onRetry={status.reload} /> : !status.data?.configured ? (
        <InlineNotice tone="danger">Reviewer access is not configured on the backend. Set BHASHATRUST_REVIEWER_ID and a random BHASHATRUST_REVIEWER_TOKEN of at least 32 characters, then restart FastAPI. Review actions remain unavailable.</InlineNotice>
      ) : (
        <InlineNotice tone="info">Reviewer access is configured. Approval is an internal evidence review—not proof of ownership, signer authority, or legal validity.</InlineNotice>
      )}
      {status.data?.configured && (
        <Card title="Reviewer access" subtitle="The credential is sent only to the backend and held in this page's memory; it is not saved.">
          <form className="stacked-form" onSubmit={loadQueue}>
            <Field label="Reviewer credential" hint="Only the server-configured reviewer can submit a decision.">
              <input type="password" autoComplete="off" value={token} onChange={(event) => { setToken(event.target.value); setQueue(null); }} />
            </Field>
            <Button type="submit" disabled={loading || !token.trim()}>{loading ? 'Authorizing…' : 'Authorize and load queue'}</Button>
          </form>
        </Card>
      )}
      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
      {notice && <InlineNotice tone="success">{notice}</InlineNotice>}
      {loading ? <LoadingState label="Loading evidence queue…" /> : queue && (
        <>
          <InlineNotice tone="info">Authenticated reviewer: {queue.reviewer_id}. Decisions apply to evidence only; licensing restrictions are re-evaluated separately.</InlineNotice>
          {queue.items.length === 0 ? <EmptyState title="No evidence submitted" description="Evidence submissions will appear here after a registrant adds a reference." /> : queue.items.map((item) => (
            <Card key={item.evidence.id} title={`${item.dataset_name} · Evidence #${item.evidence.id}`} subtitle={`Dataset #${item.evidence.dataset_id} · ${item.evidence.evidence_type} · ${formatDate(item.evidence.submitted_at)}`}>
              <div className="page-stack">
                {item.evidence.evidence_type.trim().toLowerCase() === 'synthetic demo license reference' && (
                  <InlineNotice tone="danger">Synthetic demo placeholder only—not evidence. Approval is disabled and the backend will reject attempts to approve it.</InlineNotice>
                )}
                <p><strong>Owner claim (registrant-supplied):</strong> {item.owner_claim || 'Not recorded'}</p>
                <p><strong>Submitter:</strong> {item.evidence.submitter_id || 'Not provided'} · <strong>Review status:</strong> {item.evidence.review_status}</p>
                <p><strong>Description:</strong> {item.evidence.description}</p>
                <p><strong>Evidence reference (not fetched):</strong> <code>{item.evidence.reference}</code></p>
                <div><DecisionBadge decision={item.current_decision} /><p>{item.current_explanation}</p><small>Reason codes: {item.current_reason_codes.join(', ') || 'None'}</small></div>
                {item.evidence.review_reason && <InlineNotice tone="info">Previous review: {item.evidence.review_reason}</InlineNotice>}
                <form className="stacked-form" onSubmit={(event) => event.preventDefault()}>
                  <Field label="Review reason" hint="Do not include credentials or confidential evidence contents.">
                    <textarea name="reason" required maxLength="1000" rows="2" defaultValue={item.evidence.review_reason || ''} />
                  </Field>
                  <div className="form-actions">
                    {['APPROVED', 'REJECTED', 'MORE_EVIDENCE_REQUESTED'].map((decision) => (
                      <Button
                        key={decision}
                        type="button"
                        variant={decision === 'APPROVED' ? 'primary' : 'secondary'}
                        disabled={!queue || busyId === item.evidence.id || (
                          decision === 'APPROVED'
                          && item.evidence.evidence_type.trim().toLowerCase() === 'synthetic demo license reference'
                        )}
                        onClick={(event) => {
                          const form = event.currentTarget.closest('form');
                          if (form?.reportValidity()) submitReview(item, decision, form);
                        }}
                      >
                        {decision === 'MORE_EVIDENCE_REQUESTED' ? 'Request more evidence' : decision[0] + decision.slice(1).toLowerCase()}
                      </Button>
                    ))}
                  </div>
                </form>
              </div>
            </Card>
          ))}
        </>
      )}
    </div>
  );
}

export function LedgerPage() {
  const datasets = useLoad(() => api.datasets());
  const [datasetId, setDatasetId] = useState('');
  const [records, setRecords] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [actionNote, setActionNote] = useState('');
  const [actionError, setActionError] = useState('');
  const [busyRecord, setBusyRecord] = useState(null);
  const [retryToken, setRetryToken] = useState(0);
  useEffect(() => {
    if (!datasetId) { setRecords(null); return undefined; }
    let active = true; setLoading(true); setError('');
    api.rightsLedger(datasetId).then((value) => { if (active) setRecords(value); }).catch((reason) => { if (active) setError(reason.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [datasetId, retryToken]);
  const anchor = async (record) => {
    setBusyRecord(record.id); setActionError(''); setActionNote('');
    try {
      const result = await api.anchorRightsRecord(record.id, { purpose: 'research_training', context: 'research' });
      if (result.ledger_status === 'ANCHORED') setActionNote(`Record #${record.id} is anchored on local Hardhat${result.block_number ? ` at block ${result.block_number}` : ''}.`);
      else if (result.ledger_status === 'PENDING') setActionNote(`Record #${record.id} was submitted locally and is pending confirmation.`);
      else setActionError(result.error_message || `Local anchor status: ${result.ledger_status}.`);
      setRetryToken((value) => value + 1);
    } catch (reason) { setActionError(reason.message); setRetryToken((value) => value + 1); }
    finally { setBusyRecord(null); }
  };
  const verify = async (record) => {
    setBusyRecord(record.id); setActionError(''); setActionNote('');
    try {
      const result = await api.verifyRightsAnchor(record.id);
      if (result.verification_status === 'MATCH') setActionNote(`Record #${record.id} matches its local Hardhat anchor.`);
      else if (result.verification_status === 'MISMATCH') setActionError(`Record #${record.id} differs from its anchored digest.`);
      else setActionNote(result.error_message || `Verification status: ${result.verification_status}.`);
      setRetryToken((value) => value + 1);
    } catch (reason) { setActionError(reason.message); setRetryToken((value) => value + 1); }
    finally { setBusyRecord(null); }
  };
  return (
    <div className="page-stack">
      <InlineNotice tone="info">Only a canonical digest and policy metadata are anchored. These are local Hardhat transactions, not public-chain transactions. An anchor shows that a hash was recorded on this local network; it does not prove ownership or the truth of the claim.</InlineNotice>
      {actionNote && <InlineNotice tone="success">{actionNote}</InlineNotice>}
      {actionError && <InlineNotice tone="danger">{actionError}</InlineNotice>}
      <Card title="Rights ledger" subtitle="Database records and local blockchain status. Anchor is an explicit action and requires current backend approval.">
        <div className="ledger-filter"><Field label="Choose dataset"><select value={datasetId} onChange={(event) => setDatasetId(event.target.value)}><option value="">Select a dataset</option>{(datasets.data || []).map((dataset) => <option key={dataset.id} value={dataset.id}>#{dataset.id} · {dataset.name}</option>)}</select></Field></div>
        {datasets.error && <ErrorState message={datasets.error} onRetry={datasets.reload} />}
        {loading ? <LoadingState /> : error ? <ErrorState message={error} onRetry={() => setRetryToken((value) => value + 1)} /> : datasetId && records?.length === 0 ? <EmptyState title="No rights records stored" description="The backend returned an empty ledger for this dataset." /> : !datasetId ? <EmptyState title="Select a dataset" description="Choose a dataset to inspect its stored signatures and rights payloads." /> : (
          <div className="records-grid">{(records || []).map((record) => <div className="ledger-record" key={record.id}>
            <div className="record-topline"><div className="record-id">RR-{String(record.id).padStart(4, '0')}</div><div className="ledger-status-pills"><DecisionBadge decision={record.licensing_decision} /><Badge tone={record.ledger_status === 'ANCHORED' ? 'success' : record.ledger_status === 'FAILED' ? 'danger' : record.ledger_status === 'PENDING' ? 'warning' : 'neutral'} dot>{record.ledger_status.replaceAll('_', ' ')}</Badge></div></div>
            <h3>{record.payload.license_name}</h3><p>Grantor: {record.payload.grantor}</p>
            <div className="record-meta-grid"><span>Policy version<strong>{record.policy_version}</strong></span><span>Signature<strong>{record.signature_valid ? 'Valid for submitted claim' : 'Invalid'}</strong></span><span>Decision scope<strong>{record.decision_scope.purpose} · {record.decision_scope.context}{record.decision_scope.redistribution_requested ? ' · redistribution' : ''}{record.decision_scope.derivative_work_requested ? ' · derivatives' : ''}</strong></span><span>Created<strong>{formatDate(record.created_at)}</strong></span></div>
            <div className="ledger-hash"><span>Current canonical record hash</span><code title={record.current_record_hash}>{record.current_record_hash}</code></div>
            {record.ledger_status === 'ANCHORED' && <div className="ledger-chain-meta"><span>Network<strong>{record.network_name} · local</strong></span><span>Transaction<strong title={record.transaction_hash}>{record.transaction_hash ? `${record.transaction_hash.slice(0, 12)}…${record.transaction_hash.slice(-8)}` : 'Not available'}</strong></span><span>Block<strong>{record.block_number ?? 'Not available'}</strong></span><span>Authorized sender<strong>{record.authorized_sender || 'Not available'}</strong></span></div>}
            {record.anchored_record_hash && record.anchored_record_hash !== record.current_record_hash && <div className="record-warning warning-danger">The database content differs from the stored anchor hash.</div>}
            {record.anchor_error && <div className="record-warning warning-danger">{record.anchor_error}</div>}
            <div className="ledger-actions">
              {record.ledger_status === 'ANCHORED' || record.ledger_status === 'PENDING'
                ? <Button type="button" variant="secondary" disabled={busyRecord === record.id} onClick={() => verify(record)}>{busyRecord === record.id ? 'Checking…' : 'Verify anchor'}</Button>
                : <Button type="button" disabled={busyRecord === record.id || !record.signature_valid || record.licensing_decision !== 'APPROVED'} onClick={() => anchor(record)}>{busyRecord === record.id ? 'Submitting locally…' : record.ledger_status === 'FAILED' ? 'Retry local anchor' : 'Anchor on local Hardhat'}</Button>}
              {!record.signature_valid && <small>Only signature-verified records can be anchored.</small>}
              {record.licensing_decision !== 'APPROVED' && record.signature_valid && <small>Anchoring is blocked unless the backend currently approves this scope.</small>}
            </div>
            <div className="record-warning">A valid signature and blockchain anchor do not prove legal ownership or signer authority.</div>
          </div>)}</div>
        )}
      </Card>
    </div>
  );
}

export function CostsPage() {
  const datasets = useLoad(() => api.datasets());
  const [purpose, setPurpose] = useState(DEFAULT_PURPOSE);
  const [context, setContext] = useState(DEFAULT_CONTEXT);
  const [geography, setGeography] = useState('');
  const [redistributionRequested, setRedistributionRequested] = useState(false);
  const [derivativeWorkRequested, setDerivativeWorkRequested] = useState(false);
  const eligibility = useLoad(
    () => api.eligibleDatasets({
      purpose,
      context,
      geography: geography || null,
      redistribution_requested: redistributionRequested,
      derivative_work_requested: derivativeWorkRequested,
    }),
    [purpose, context, geography, redistributionRequested, derivativeWorkRequested],
  );
  const [selected, setSelected] = useState([]);
  const [budget, setBudget] = useState('250.00');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setSelected([]);
    setResult(null);
  }, [purpose, context, geography, redistributionRequested, derivativeWorkRequested]);
  const toggle = (id) => setSelected((items) => items.includes(id) ? items.filter((value) => value !== id) : [...items, id]);
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError(''); setResult(null);
    try {
      setResult(await api.allocateCosts({
        dataset_ids: selected,
        purpose,
        context,
        geography: geography || null,
        redistribution_requested: redistributionRequested,
        derivative_work_requested: derivativeWorkRequested,
        budget,
        currency: 'USD',
      }));
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  const eligibilityById = new Map([
    ...(eligibility.data?.eligible_datasets || []),
    ...(eligibility.data?.excluded_datasets || []),
  ].map((item) => [item.dataset_id, item]));
  return (
    <div className="two-column-layout page-stack">
      <Card title="Allocate licensing costs" subtitle="Costs come from structured synthetic demo records; they are not supplier quotes.">
        {datasets.loading ? <LoadingState /> : datasets.error ? <ErrorState message={datasets.error} onRetry={datasets.reload} /> : (datasets.data || []).length === 0 ? <EmptyState title="No datasets available" description="Register datasets before requesting an allocation." /> : (
          <form className="stacked-form" onSubmit={submit}>
            <Field label="Training purpose"><input value={purpose} onChange={(event) => setPurpose(event.target.value)} required /></Field>
            <Field label="Context"><select value={context} onChange={(event) => setContext(event.target.value)}><option value="research">Research</option><option value="commercial">Commercial</option></select></Field>
            <Field label="Geography"><input value={geography} onChange={(event) => setGeography(event.target.value)} placeholder="Optional" /></Field>
            <label className="confirmation-check"><input type="checkbox" checked={redistributionRequested} onChange={(event) => setRedistributionRequested(event.target.checked)} /><span>Redistribution requested</span></label>
            <label className="confirmation-check"><input type="checkbox" checked={derivativeWorkRequested} onChange={(event) => setDerivativeWorkRequested(event.target.checked)} /><span>Derivative works requested</span></label>
            <Field label="Available budget (USD)"><input type="number" min="0" step="0.01" required value={budget} onChange={(event) => setBudget(event.target.value)} /></Field>
            <div className="field"><span className="field-label">Eligible datasets to price</span><div className="check-list">{datasets.data.map((dataset) => {
              const decision = eligibilityById.get(dataset.id);
              const isEligible = decision?.eligible === true;
              return <label className="check-row" key={dataset.id}><input type="checkbox" checked={selected.includes(dataset.id)} disabled={eligibility.loading || Boolean(eligibility.error) || !isEligible} onChange={() => toggle(dataset.id)} /><span><strong>{dataset.name}</strong><small>{dataset.language} · ID {dataset.id} · {decision ? isEligible ? 'Eligible for this scope' : decision.reason_codes.join(', ') || decision.explanation : 'Checking eligibility'}</small></span>{decision && <DecisionBadge decision={decision.decision} />}</label>;
            })}</div></div>
            {eligibility.loading && <LoadingState label="Checking dataset eligibility…" />}
            {eligibility.error && <ErrorState message={eligibility.error} onRetry={eligibility.reload} />}
            {error && <InlineNotice tone="danger">{error}</InlineNotice>}
            <InlineNotice tone="warning">The backend rechecks the requested rights scope before allocation. Ineligible datasets cannot be priced; successful allocations use synthetic demo costs and are audited.</InlineNotice>
            <Button type="submit" disabled={busy || eligibility.loading || Boolean(eligibility.error) || !selected.length}>{busy ? 'Calculating…' : 'Calculate allocation'}</Button>
          </form>
        )}
      </Card>
      <Card title="Allocation result" subtitle="Returned by the backend cost-allocation service">
        {busy ? <LoadingState /> : error && !result ? <ErrorState message={error} /> : !result ? <EmptyState title="No allocation yet" description="Select datasets and submit a budget to see the cost breakdown." /> : (
          <div className="allocation-result"><div className="allocation-total"><span>Total licensing cost</span><strong>{formatMoney(result.total_cost, result.currency)}</strong><Badge tone={result.budget_status === 'WITHIN_BUDGET' ? 'success' : 'danger'}>{result.budget_status.replaceAll('_', ' ')}</Badge></div><div className="allocation-summary"><div><span>Budget</span><strong>{formatMoney(result.budget, result.currency)}</strong></div><div><span>Remaining</span><strong className={Number(result.remaining_budget) < 0 ? 'text-danger' : ''}>{formatMoney(result.remaining_budget, result.currency)}</strong></div></div><div className="table-wrap"><table><thead><tr><th>Dataset</th><th>Utility</th><th>Cost</th><th>Source</th></tr></thead><tbody>{result.items.map((item) => <tr key={item.dataset_id}><td>{item.dataset_name}<small className="sub-label">ID {item.dataset_id}</small></td><td>{item.utility_score}</td><td>{formatMoney(item.cost, item.currency)}</td><td><Badge tone="warning">{item.synthetic_demo_cost ? 'Synthetic demo' : 'Configured'}</Badge></td></tr>)}</tbody></table></div></div>
        )}
      </Card>
    </div>
  );
}

export function OptimizerPage() {
  const datasets = useLoad(() => api.datasets());
  const [selected, setSelected] = useState([]);
  const [budget, setBudget] = useState('250.00');
  const [purpose, setPurpose] = useState(DEFAULT_PURPOSE);
  const [context, setContext] = useState(DEFAULT_CONTEXT);
  const [geography, setGeography] = useState('');
  const [redistributionRequested, setRedistributionRequested] = useState(false);
  const [derivativeWorkRequested, setDerivativeWorkRequested] = useState(false);
  const [solver, setSolver] = useState('local');
  const [confirmRemote, setConfirmRemote] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const toggle = (id) => setSelected((items) => items.includes(id) ? items.filter((value) => value !== id) : [...items, id]);
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError(''); setResult(null);
    try {
      setResult(await api.optimize({ purpose, context, geography: geography || null, redistribution_requested: redistributionRequested, derivative_work_requested: derivativeWorkRequested, dataset_ids: selected, budget, currency: 'USD', solver, confirm_remote_job: solver === 'qpiai_opt' && confirmRemote }));
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  return (
    <div className="page-stack">
      <div className="integration-strip"><div className="integration-mark">!</div><div><strong>QpiAI Opt live authentication is unverified</strong><p>The backend reports the solver that actually ran. A missing credential or unconfirmed remote request can return a local baseline result.</p></div><Badge tone="warning">Not verified</Badge></div>
      <div className="two-column-layout">
        <Card title="Optimization request" subtitle="Eligibility, expiry, revocation, and budget checks run in the backend.">
          {datasets.loading ? <LoadingState /> : datasets.error ? <ErrorState message={datasets.error} onRetry={datasets.reload} /> : (
            <form className="stacked-form" onSubmit={submit}>
              <Field label="Training purpose"><input value={purpose} onChange={(event) => setPurpose(event.target.value)} required /></Field>
              <Field label="Context"><select value={context} onChange={(event) => setContext(event.target.value)}><option value="research">Research</option><option value="commercial">Commercial</option></select></Field>
              <Field label="Geography"><input value={geography} onChange={(event) => setGeography(event.target.value)} placeholder="Optional" /></Field>
              <label className="confirmation-check"><input type="checkbox" checked={redistributionRequested} onChange={(event) => setRedistributionRequested(event.target.checked)} /><span>Redistribution requested</span></label>
              <label className="confirmation-check"><input type="checkbox" checked={derivativeWorkRequested} onChange={(event) => setDerivativeWorkRequested(event.target.checked)} /><span>Derivative works requested</span></label>
              <Field label="Budget (USD)"><input type="number" min="0" step="0.01" value={budget} onChange={(event) => setBudget(event.target.value)} required /></Field>
              <Field label="Solver"><select value={solver} onChange={(event) => { setSolver(event.target.value); setConfirmRemote(false); }}><option value="local">Local baseline — exact search</option><option value="qpiai_opt">QpiAI Opt — remote solver request</option></select></Field>
              {solver === 'qpiai_opt' && <label className="confirmation-check"><input type="checkbox" checked={confirmRemote} onChange={(event) => setConfirmRemote(event.target.checked)} /><span>I understand a remote QpiAI Opt request may consume credits.</span></label>}
              <div className="field"><span className="field-label">Candidate datasets</span><div className="check-list">{datasets.data.map((dataset) => <label className="check-row" key={dataset.id}><input type="checkbox" checked={selected.includes(dataset.id)} onChange={() => toggle(dataset.id)} /><span><strong>{dataset.name}</strong><small>{dataset.language} · {dataset.license_name}</small></span></label>)}</div></div>
              {error && <InlineNotice tone="danger">{error}</InlineNotice>}
              <InlineNotice tone="info">The client does not provide an eligibility flag. The optimizer uses current backend rights records.</InlineNotice>
              <Button type="submit" disabled={busy || !selected.length}>{busy ? 'Optimizing…' : 'Run optimization'}</Button>
            </form>
          )}
        </Card>
        <Card title="Optimization result" subtitle="Actual solver origin and independently checked selection">
          {busy ? <LoadingState label="Checking eligibility and optimizing…" /> : !result ? <EmptyState title="No optimization run yet" description="Your selected solver, decision scope, and budget will be used for the backend request." /> : (
            <div className="optimization-result"><div className="result-mode"><span>Solver actually used</span><Badge tone={result.solver_used === 'qpiai_opt' ? 'violet' : 'blue'} dot>{result.solver_used === 'qpiai_opt' ? 'QpiAI Opt' : 'Local baseline'}</Badge><small>{result.execution_status === 'fallback' ? result.fallback_reason : `${result.execution_status} · ${result.algorithm}`}</small>{result.solver_status && <small>Solver status: {result.solver_status}</small>}{result.solver_job_id && <small>Platform job: {result.solver_job_id}</small>}</div><div className="optimization-metrics"><div><strong>{result.total_utility}</strong><small>Total utility</small></div><div><strong>{formatMoney(result.total_licensing_cost, result.currency)}</strong><small>Licensing cost</small></div><div><strong>{formatMoney(result.remaining_budget, result.currency)}</strong><small>Budget remaining</small></div></div><div className="result-validation"><span>Selection validation</span><Badge tone={result.validation_passed ? 'success' : 'danger'}>{result.validation_passed ? 'Passed' : 'Failed'}</Badge></div><div><h3>Selected datasets</h3>{result.selected_datasets.length ? result.selected_datasets.map((item) => <div className="selected-line" key={item.dataset_id}><span>{item.dataset_name}</span><strong>{formatMoney(item.cost, item.currency)}</strong></div>) : <p className="muted-copy">No datasets selected.</p>}</div><div><h3>Excluded datasets</h3>{result.excluded_datasets.length ? result.excluded_datasets.map((item, index) => <div className="excluded-line" key={`${item.dataset_id}-${index}`}><div><strong>Dataset #{item.dataset_id}</strong><span>{item.reason_codes.join(', ') || item.explanation}</span></div><DecisionBadge decision={item.decision} /></div>) : <p className="muted-copy">No excluded datasets.</p>}</div><div className="result-meta"><span>Algorithm<strong>{result.algorithm}</strong></span><span>Origin<strong>{result.result_origin}</strong></span><span>Audit reference<strong>#{result.audit_event_reference}</strong></span></div></div>
          )}
        </Card>
      </div>
    </div>
  );
}

export function QuantumPage() {
  const datasets = useLoad(() => api.datasets());
  const [selected, setSelected] = useState([]);
  const [budget, setBudget] = useState('125.00');
  const [redistributionRequested, setRedistributionRequested] = useState(false);
  const [derivativeWorkRequested, setDerivativeWorkRequested] = useState(false);
  const [shots, setShots] = useState('256');
  const [seed, setSeed] = useState('1729');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const toggle = (id) => setSelected((items) => items.includes(id) ? items.filter((value) => value !== id) : [...items, id]);
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError(''); setResult(null);
    try { setResult(await api.quantumExperiment({ purpose: DEFAULT_PURPOSE, context: DEFAULT_CONTEXT, redistribution_requested: redistributionRequested, derivative_work_requested: derivativeWorkRequested, dataset_ids: selected, budget, shots: Number(shots), seed: Number(seed), mode: 'local_simulation' })); } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  return (
    <div className="page-stack">
      <div className="experiment-banner"><div className="experiment-symbol">Ψ</div><div><strong>Local statevector simulation</strong><p>This dashboard sends no QCloud job. QCloud authentication and credit status are not queried by a local experiment.</p></div><Badge tone="blue" dot>Simulation only</Badge></div>
      <div className="two-column-layout">
        <Card title="Experiment setup" subtitle="A small seeded p=1 QAOA circuit is built after backend eligibility filtering.">
          {datasets.loading ? <LoadingState /> : datasets.error ? <ErrorState message={datasets.error} onRetry={datasets.reload} /> : (
            <form className="stacked-form" onSubmit={submit}>
              <Field label="Budget (USD)"><input type="number" min="0" step="0.01" value={budget} onChange={(event) => setBudget(event.target.value)} required /></Field>
              <div className="form-two"><Field label="Shots"><input type="number" min="32" max="4096" step="1" value={shots} onChange={(event) => setShots(event.target.value)} /></Field><Field label="Seed"><input type="number" min="0" step="1" value={seed} onChange={(event) => setSeed(event.target.value)} /></Field></div>
              <label className="confirmation-check"><input type="checkbox" checked={redistributionRequested} onChange={(event) => setRedistributionRequested(event.target.checked)} /><span>Redistribution requested</span></label>
              <label className="confirmation-check"><input type="checkbox" checked={derivativeWorkRequested} onChange={(event) => setDerivativeWorkRequested(event.target.checked)} /><span>Derivative works requested</span></label>
              <div className="field"><span className="field-label">Candidate datasets</span><div className="check-list">{datasets.data.map((dataset) => <label className="check-row" key={dataset.id}><input type="checkbox" checked={selected.includes(dataset.id)} onChange={() => toggle(dataset.id)} /><span><strong>{dataset.name}</strong><small>{dataset.language} · ID {dataset.id}</small></span></label>)}</div></div>
              {error && <InlineNotice tone="danger">{error}</InlineNotice>}
              <Button type="submit" disabled={busy || !selected.length}>{busy ? 'Simulating locally…' : 'Run local experiment'}</Button>
            </form>
          )}
        </Card>
        <Card title="Experiment result" subtitle="Simulation results are not hardware results">
          {busy ? <LoadingState label="Running the local statevector simulator…" /> : !result ? <EmptyState title="No experiment run yet" description="The result will include sampled counts, selected datasets, and the classical baseline." /> : (
            <div className="experiment-result"><div className="result-mode"><span>Execution mode</span><Badge tone={result.result_kind === 'simulation_result' ? 'blue' : 'violet'} dot>{result.result_kind === 'simulation_result' ? 'Local simulation' : 'QCloud measurements'}</Badge><small>{result.sdk_name} {result.sdk_version || ''} · {result.experiment_configuration.algorithm}</small></div><div className="optimization-metrics"><div><strong>{result.total_utility}</strong><small>Observed utility</small></div><div><strong>{formatMoney(result.total_licensing_cost)}</strong><small>Observed cost</small></div><div><strong>{formatMoney(result.remaining_budget)}</strong><small>Remaining budget</small></div></div><div className="result-validation"><span>Independent validation</span><Badge tone={result.validation_passed ? 'success' : 'danger'}>{result.validation_passed ? 'Passed' : 'Failed'}</Badge></div><div className="sample-comparison"><div><span>Quantum sample</span><strong>{result.selected_dataset_ids.length ? result.selected_dataset_ids.map((id) => `#${id}`).join(', ') : 'No dataset selected'}</strong></div><div><span>Classical exact baseline</span><strong>{result.baseline_selected_dataset_ids.length ? result.baseline_selected_dataset_ids.map((id) => `#${id}`).join(', ') : 'No dataset selected'}</strong></div></div><div className="sample-counts"><h3>Measurement counts</h3>{Object.entries(result.sample_counts).sort((a, b) => b[1] - a[1]).map(([bits, count]) => <div className="count-row" key={bits}><code>{bits}</code><div className="count-track"><i style={{ width: `${100 * count / Math.max(...Object.values(result.sample_counts))}%` }} /></div><strong>{count}</strong></div>)}</div><div className="limitations-box"><strong>Limitations</strong><ul>{result.limitations.map((item) => <li key={item}>{item}</li>)}</ul></div></div>
          )}
        </Card>
      </div>
    </div>
  );
}

export function ManifestsPage() {
  const manifests = useLoad(() => api.latestManifests());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [generated, setGenerated] = useState(null);
  const [purpose, setPurpose] = useState(DEFAULT_PURPOSE);
  const [context, setContext] = useState(DEFAULT_CONTEXT);
  const [geography, setGeography] = useState('');
  const [redistributionRequested, setRedistributionRequested] = useState(false);
  const [derivativeWorkRequested, setDerivativeWorkRequested] = useState(false);
  const generate = async (event) => {
    event.preventDefault(); setBusy(true); setError(''); setGenerated(null);
    try { setGenerated(await api.generateManifests({ purpose, context, geography: geography || null, redistribution_requested: redistributionRequested, derivative_work_requested: derivativeWorkRequested })); manifests.reload(); }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  const categories = [
    ['accepted', 'Accepted datasets', 'success'],
    ['rejected', 'Rejected datasets', 'danger'],
    ['needs_review', 'Needs review', 'warning'],
  ];
  return (
    <div className="page-stack">
      <Card title="Generate current manifests" subtitle="Generation evaluates every registered dataset and records decision and generation audit events.">
        <form className="manifest-form" onSubmit={generate}><Field label="Training purpose"><input value={purpose} onChange={(event) => setPurpose(event.target.value)} required /></Field><Field label="Context"><select value={context} onChange={(event) => setContext(event.target.value)}><option value="research">Research</option><option value="commercial">Commercial</option></select></Field><Field label="Geography"><input value={geography} onChange={(event) => setGeography(event.target.value)} placeholder="Optional" /></Field><label className="confirmation-check"><input type="checkbox" checked={redistributionRequested} onChange={(event) => setRedistributionRequested(event.target.checked)} /><span>Redistribution requested</span></label><label className="confirmation-check"><input type="checkbox" checked={derivativeWorkRequested} onChange={(event) => setDerivativeWorkRequested(event.target.checked)} /><span>Derivative works requested</span></label><Button type="submit" disabled={busy}>{busy ? 'Generating…' : 'Generate manifests'}</Button></form>
        {error && <InlineNotice tone="danger">{error}</InlineNotice>}{generated && <InlineNotice tone="success">Manifest generation completed. Audit reference #{generated.audit_event_reference}; counts {Object.entries(generated.counts).map(([key, value]) => `${key}: ${value}`).join(' · ')}.</InlineNotice>}
      </Card>
      {manifests.loading ? <LoadingState /> : manifests.error ? <ErrorState message={manifests.error} onRetry={manifests.reload} /> : (
        <div className="manifest-grid">{categories.map(([key, label, tone]) => {
          const entry = manifests.data?.manifests?.[key];
          const rows = entry?.manifest?.entries || [];
          return <Card key={key} title={label} subtitle={entry ? `Generated ${formatDate(entry.manifest.generated_at)}` : 'No manifest file available'} action={entry && <><Badge tone={tone}>{rows.length} entries</Badge>{entry.manifest.synthetic_test_output && <Badge tone="warning">Synthetic test output</Badge>}</>}>
            {!entry ? <EmptyState title="No saved manifest" description="Generate manifests to create actual category files." /> : rows.length === 0 ? <EmptyState title="No entries in this category" description={`The actual ${key.replaceAll('_', ' ')} manifest is empty.`} /> : <div className="manifest-entries">{rows.map((item) => <div className="manifest-entry" key={`${key}-${item.dataset_id}`}><span className="entry-id">#{item.dataset_id}</span><div><DecisionBadge decision={item.decision} /><small>{item.reason_codes?.join(', ') || 'No reason codes'}</small></div><span className="entry-policy">Policy {item.policy_version}</span></div>)}</div>}
            {entry && <div className="manifest-file">{entry.filename}</div>}
          </Card>;
        })}</div>
      )}
    </div>
  );
}

export function AuditPage() {
  const events = useLoad(() => api.auditEvents());
  const integrity = useLoad(() => api.verifyAudit());
  return (
    <div className="page-stack">
      <div className="audit-integrity"><div><span className="integrity-mark">⌁</span><div><strong>Audit chain integrity</strong><small>Verified against the backend hash chain</small></div></div>{integrity.loading ? <Badge>Checking…</Badge> : integrity.error ? <Badge tone="danger">Unavailable</Badge> : <Badge tone={integrity.data.intact ? 'success' : 'danger'} dot>{integrity.data.intact ? 'Intact' : 'Integrity issue'}</Badge>}</div>
      {integrity.data && <p className="integrity-detail">{integrity.data.event_count} recorded events{integrity.data.first_invalid_event_id ? ` · first invalid event #${integrity.data.first_invalid_event_id}` : ''}</p>}
      <Card title="Audit events" subtitle="Actual records returned by GET /audit/events" action={<Button variant="secondary" className="button-small" onClick={events.reload}>Refresh</Button>}>
        <PageError error={events.error} reload={events.reload} />
        {events.loading ? <LoadingState /> : (events.data || []).length === 0 ? <EmptyState title="No audit events yet" description="Backend actions will be listed here when recorded." /> : <div className="table-wrap"><table><thead><tr><th>Event</th><th>Timestamp</th><th>Resource</th><th>Decision</th><th>Reference</th></tr></thead><tbody>{[...events.data].reverse().map((event) => { const decision = event.payload?.decision || event.payload?.result?.decision; return <tr key={event.id}><td><strong className="event-label">{event.event_type.replaceAll('_', ' ')}</strong><small className="sub-label">{event.payload?.execution_mode || event.payload?.solver_used || ''}</small></td><td>{formatDate(event.created_at)}</td><td>{event.entity_type} · {event.entity_id}</td><td>{decision ? <DecisionBadge decision={decision} /> : <span className="muted-copy">—</span>}</td><td><code>#{event.id}</code></td></tr>; })}</tbody></table></div>}
      </Card>
    </div>
  );
}

export function EvaluationPage() {
  return (
    <div className="page-stack">
      <div className="integration-strip"><div className="integration-mark">i</div><div><strong>No evaluation metrics endpoint is available</strong><p>This view does not infer success rates from audit events or simulator samples.</p></div><Badge tone="neutral">Not yet measured</Badge></div>
      <div className="stats-grid evaluation-stats"><StatCard label="Decision accuracy" value="Not yet measured" detail="No labeled evaluation set configured" icon="◫" tone="blue" /><StatCard label="Evidence coverage" value="Not yet measured" detail="No evaluation metric endpoint" icon="▧" tone="green" /><StatCard label="Optimizer quality" value="Not yet measured" detail="No benchmark results stored" icon="⌘" tone="amber" /></div>
      <Card title="Evaluation coverage" subtitle="Metrics will appear when an actual backend evaluation workflow reports them."><EmptyState title="No measured evaluation results" description="The current backend exposes policy decisions, audit integrity, optimizer outputs, and quantum experiment samples, but no aggregate evaluation metrics." /></Card>
    </div>
  );
}

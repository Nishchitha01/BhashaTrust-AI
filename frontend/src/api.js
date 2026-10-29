const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim();
export const API_BASE_URL = (configuredBase || 'http://127.0.0.1:8000').replace(/\/$/, '');

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    });
  } catch {
    throw new Error(`Could not reach the backend at ${API_BASE_URL}. Check that FastAPI is running.`);
  }

  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json')
    ? await response.json().catch(() => null)
    : await response.text().catch(() => '');
  if (!response.ok) {
    const detail = payload?.detail || payload?.message || payload || `Request failed (${response.status})`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return payload;
}

const json = (method, body) => ({ method, body: JSON.stringify(body) });

export const api = {
  health: () => request('/health'),
  datasets: () => request('/api/datasets'),
  dataset: (id) => request(`/api/datasets/${encodeURIComponent(id)}`),
  registerDataset: (body) => request('/api/datasets', json('POST', body)),
  dashboardSummary: (purpose = 'research_training', context = 'research') => {
    const query = new URLSearchParams({ purpose, context });
    return request(`/api/dashboard/summary?${query}`);
  },
  eligibleDatasets: (body) => request('/api/datasets/eligible', json('POST', body)),
  decide: (body) => request('/api/decisions', json('POST', body)),
  reviewerStatus: () => request('/api/reviewer/status'),
  submitEvidence: (id, body) => request(`/api/datasets/${encodeURIComponent(id)}/evidence`, json('POST', body)),
  evidenceStatus: (id) => request(`/api/datasets/${encodeURIComponent(id)}/evidence/status`),
  evidenceReviewQueue: (token) => request('/api/review/queue', {
    headers: { Authorization: `Bearer ${token}` },
  }),
  reviewEvidence: (id, token, body) => request(`/api/evidence/${encodeURIComponent(id)}/review`, {
    ...json('POST', body),
    headers: { Authorization: `Bearer ${token}` },
  }),
  rightsRecords: (id) => request(`/datasets/${encodeURIComponent(id)}/rights-records`),
  submitRightsRecord: (id, body) => request(`/datasets/${encodeURIComponent(id)}/rights-records`, json('POST', body)),
  rightsLedger: (id, scope = {}) => {
    const query = new URLSearchParams({ purpose: scope.purpose || 'research_training', context: scope.context || 'research' });
    if (scope.geography) query.set('geography', scope.geography);
    return request(`/api/datasets/${encodeURIComponent(id)}/rights-ledger?${query}`);
  },
  anchorRightsRecord: (id, body) => request(`/api/rights-records/${encodeURIComponent(id)}/anchor`, json('POST', body)),
  verifyRightsAnchor: (id) => request(`/api/rights-records/${encodeURIComponent(id)}/verify-anchor`, json('POST', {})),
  allocateCosts: (body) => request('/api/costs/allocate', json('POST', body)),
  optimize: (body) => request('/api/optimizations', json('POST', body)),
  quantumExperiment: (body) => request('/api/quantum-experiments', json('POST', body)),
  latestManifests: () => request('/api/manifests/latest'),
  generateManifests: (body) => request('/api/manifests/generate', json('POST', body)),
  auditEvents: () => request('/audit/events'),
  verifyAudit: () => request('/audit/verify'),
};

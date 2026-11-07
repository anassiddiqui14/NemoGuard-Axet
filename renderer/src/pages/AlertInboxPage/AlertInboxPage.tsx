import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { authFetch } from '../../contexts/AuthGateContext';

type Result = { status?: string; reasoning?: string; incident_id?: string; message?: string };
type InboxItem = { delivery_key: string; subject: string; state: string; received_at: string; updated_at: string; result: Result };
type Review = { assessment: string; reason: string; actor_id: string; created_at: string };
type Promotion = { alert_id: string; actor_id: string; reason: string; created_at: string };
type Detail = InboxItem & { body: string; reviews: Review[]; promotion: Promotion | null };
type BridgeReport = { last_scan_at: string; pending: number; blocked: number; baseline: number };

function stamp(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function AlertInboxPage() {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [assessment, setAssessment] = useState('uncertain');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [bridge, setBridge] = useState<BridgeReport | null>(null);
  const [bridgeError, setBridgeError] = useState(false);
  const [severity, setSeverity] = useState('warning');
  const [promotionReason, setPromotionReason] = useState('');
  const [promoting, setPromoting] = useState(false);
  const [promotionMessage, setPromotionMessage] = useState('');
  const [retrying, setRetrying] = useState(false);
  const [retryMessage, setRetryMessage] = useState('');

  const refresh = useCallback(async () => {
    try {
      const response = await authFetch('/api/v2/outlook-inbox');
      if (!response.ok) throw new Error(response.status === 403 ? 'Operator access is required.' : 'Unable to load the inbox.');
      const data = await response.json();
      if (!Array.isArray(data.items)) throw new Error('Invalid inbox response.');
      setItems(data.items);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load the inbox.');
    }
  }, []);

  const refreshHealth = useCallback(async () => {
    try {
      const response = await authFetch('/api/v2/outlook-bridge-health');
      if (!response.ok) throw new Error('Health unavailable');
      const data = await response.json();
      setBridge(data.last_report ?? null);
      setBridgeError(false);
    } catch {
      setBridgeError(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    void refreshHealth();
    const timer = window.setInterval(() => {
      void refresh();
      void refreshHealth();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [refresh, refreshHealth]);

  const loadDetail = useCallback(async (key: string, signal?: AbortSignal) => {
    const response = await authFetch(`/api/v2/outlook-inbox/${encodeURIComponent(key)}`, { signal });
    if (!response.ok) throw new Error('Unable to load this email.');
    return await response.json() as Detail;
  }, []);

  useEffect(() => {
    setDetail(null);
    setDetailError('');
    setMessage('');
    setReason('');
    setPromotionReason('');
    setPromotionMessage('');
    setRetryMessage('');
    if (!selected) return;
    const controller = new AbortController();
    void loadDetail(selected, controller.signal).then(setDetail).catch(() => {
      if (!controller.signal.aborted) setDetailError('Unable to load this email.');
    });
    return () => controller.abort();
  }, [selected, loadDetail]);

  async function submitReview(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !reason.trim() || saving) return;
    setSaving(true);
    setMessage('');
    try {
      const response = await authFetch(`/api/v2/outlook-inbox/${encodeURIComponent(selected)}/reviews`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ assessment, reason: reason.trim() }),
      });
      if (!response.ok) throw new Error('Could not save assessment.');
      setReason('');
      setMessage('Assessment recorded. It did not change classification or incident correlation.');
      try {
        const updated = await loadDetail(selected);
        setDetail((previous) => previous?.delivery_key === selected ? updated : previous);
      } catch {
        setMessage('Assessment recorded, but review history could not be refreshed. Reopen this email to see it.');
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Could not save assessment.');
    } finally {
      setSaving(false);
    }
  }

  async function retryDelivery() {
    if (!selected || retrying) return;
    const key = selected;
    setRetrying(true);
    setRetryMessage('');
    try {
      const response = await authFetch(`/api/v2/outlook-inbox/${encodeURIComponent(key)}/retry`, { method: 'POST' });
      if (!response.ok) throw new Error('Retry did not complete. Refresh the item before trying again.');
      setRetryMessage('Retry completed. Check the processing outcome for the result.');
      try {
        const updated = await loadDetail(key);
        setDetail(previous => previous?.delivery_key === key ? updated : previous);
      } catch {
        setRetryMessage('Retry completed, but details could not be refreshed. Reopen this email.');
      }
      void refresh();
    } catch {
      setRetryMessage('Retry was not confirmed. Refresh the item before trying again.');
    } finally {
      setRetrying(false);
    }
  }

  async function promote(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !promotionReason.trim() || promoting) return;
    const key = selected;
    setPromoting(true);
    setPromotionMessage('');
    try {
      const response = await authFetch(`/api/v2/outlook-inbox/${encodeURIComponent(key)}/promote`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ severity, reason: promotionReason.trim() }),
      });
      if (!response.ok) throw new Error('Promotion failed. Refresh the item before retrying.');
      const result = await response.json();
      setPromotionMessage(`Alert ${result.alert_id} created. No incident was created or linked.`);
      setPromotionReason('');
      try {
        const updated = await loadDetail(key);
        setDetail(previous => previous?.delivery_key === key ? updated : previous);
      } catch {
        setPromotionMessage(`Alert ${result.alert_id} created, but details could not be refreshed. Reopen this email to see the result.`);
      }
      void refresh();
    } catch (e) {
      setPromotionMessage(e instanceof Error ? e.message : 'Promotion failed.');
    } finally {
      setPromoting(false);
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-app-bg px-5 py-7 text-text-primary sm:px-8">
      <div className="mx-auto max-w-7xl space-y-5">
        <header className="flex flex-wrap justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">Alert Inbox</h1>
            <p className="mt-2 text-sm text-text-secondary">Outlook emails accepted by NemoGuard, including classification failures and items flagged for review.</p>
          </div>
          <button onClick={() => { void refresh(); void refreshHealth(); }} className="rounded-lg border border-white/15 px-4 py-2 text-sm">Refresh</button>
        </header>
        <div className="rounded-xl border border-warning/30 bg-warning/5 p-4 text-sm text-text-secondary">
          {bridgeError ? 'Bridge report unavailable. ' : bridge ? `Last scan report received ${stamp(bridge.last_scan_at)}: ${bridge.pending} pending, ${bridge.blocked} blocked, ${bridge.baseline} baselined. ` : 'No bridge scan report received. '}
          This report may be stale and does not prove production coverage. Baselined messages and emails not delivered here are not included.
        </div>
        {error && <p role="alert" className="text-sm text-critical">{error}{items !== null ? ' Showing previously loaded items.' : ''}</p>}
        <p className="text-xs text-text-muted">Showing at most the 200 most recent accepted emails. Email content is untrusted and is not independent confirmation of production state.</p>
        <div className="grid gap-4 lg:grid-cols-[minmax(240px,1fr)_minmax(0,2fr)]">
          <section aria-label="Inbox messages" className="rounded-xl border border-white/10 bg-white/[0.03]">
            {items === null ? <p className="p-5 text-sm text-text-muted">{error ? 'Inbox unavailable.' : 'Loading…'}</p> : items.length === 0 ? <p className="p-5 text-sm text-text-muted">No accepted emails found in this workspace.</p> :
              <ul className="divide-y divide-white/10">{items.map(item => <li key={item.delivery_key}>
                <button onClick={() => setSelected(item.delivery_key)} aria-pressed={selected === item.delivery_key} className={`w-full p-4 text-left hover:bg-white/[0.05] ${selected === item.delivery_key ? 'bg-white/[0.07]' : ''}`}>
                  <span className="block break-words text-sm font-medium">{item.subject || '(no subject)'}</span>
                  <span className="mt-1 block text-xs text-text-muted">{item.state.replace(/_/g, ' ')} · {stamp(item.received_at)}</span>
                </button>
              </li>)}</ul>}
          </section>
          <section aria-label="Email detail" className="min-w-0 rounded-xl border border-white/10 bg-white/[0.03] p-5">
            {!selected ? <p className="text-sm text-text-muted">Select an email to inspect it.</p> : detailError ? <p role="alert" className="text-sm text-critical">{detailError}</p> : !detail ? <p className="text-sm text-text-muted">Loading email…</p> : <div className="space-y-5">
              <div><h2 className="break-words text-lg font-semibold">{detail.subject || '(no subject)'}</h2><p className="mt-1 text-xs text-text-muted">Received {stamp(detail.received_at)} · {detail.state.replace(/_/g, ' ')}</p></div>
              <div><h3 className="text-sm font-semibold">Processing outcome</h3><p className="mt-1 break-words text-sm text-text-secondary">{detail.result.status || 'Not yet completed'}{detail.result.reasoning ? ` — ${detail.result.reasoning}` : ''}{detail.result.message ? ` — ${detail.result.message}` : ''}</p>
                {detail.result.incident_id && <Link className="mt-2 inline-block text-sm text-primary" to={`/app/command-center?incident=${encodeURIComponent(detail.result.incident_id)}`}>Open linked incident →</Link>}
              </div>
              {(detail.state === 'failed' || detail.state === 'received') && <div className="rounded-lg border border-warning/30 p-3">
                <p className="mb-2 text-xs text-text-muted">Retry uses the original delivery. It may create an alert or incident and start triage. A received item may still be processing, and the bridge may also retry automatically.</p>
                <button type="button" disabled={retrying} onClick={() => void retryDelivery()} className="rounded-lg bg-primary px-4 py-2 text-sm text-white disabled:opacity-50">{retrying ? 'Retrying…' : 'Retry processing'}</button>
              </div>}
              {retryMessage && <p role="status" className="text-sm text-text-secondary">{retryMessage}</p>}
              <div><h3 className="text-sm font-semibold">Email body <span className="font-normal text-text-muted">(untrusted source)</span></h3><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/10 p-3 font-sans text-sm text-text-secondary">{detail.body}</pre></div>
              {detail.promotion && <div className="rounded-lg border border-white/10 p-3 text-sm text-text-secondary">
                Promoted as alert {detail.promotion.alert_id} by {detail.promotion.actor_id} on {stamp(detail.promotion.created_at)}: {detail.promotion.reason}. No incident was automatically created or linked.
              </div>}
              {detail.state === 'needs_review' && detail.result.status === 'ignored' && <form onSubmit={promote} className="space-y-3 rounded-lg border border-warning/30 p-3">
                <h3 className="text-sm font-semibold">Promote ignored email to an alert</h3>
                <p className="text-xs text-text-muted">Creates an alert record only. It will not create or link an incident or start an investigation.</p>
                <label className="block text-sm">Severity
                  <select value={severity} onChange={e => setSeverity(e.target.value)} className="mt-1 block w-full rounded border border-white/15 bg-app-bg p-2">
                    <option value="info">Info</option><option value="warning">Warning</option><option value="high">High</option><option value="critical">Critical</option>
                  </select>
                </label>
                <label className="block text-sm">Reason
                  <textarea required maxLength={2000} value={promotionReason} onChange={e => setPromotionReason(e.target.value)} className="mt-1 block w-full rounded border border-white/15 bg-app-bg p-2" rows={2} />
                </label>
                <button disabled={promoting || !promotionReason.trim()} className="rounded-lg bg-primary px-4 py-2 text-sm text-white disabled:opacity-50">{promoting ? 'Creating…' : 'Create alert'}</button>
              </form>}
              {promotionMessage && <p role="status" className="text-sm text-text-secondary">{promotionMessage}</p>}
              <div><h3 className="text-sm font-semibold">Operator assessment</h3><p className="mt-1 text-xs text-text-muted">An assessment records your judgment only. It does not create an incident or change classification or correlation.</p>
                <form onSubmit={submitReview} className="mt-3 space-y-3">
                  <label className="block text-sm">Assessment
                    <select value={assessment} onChange={e => setAssessment(e.target.value)} className="mt-1 block w-full rounded border border-white/15 bg-app-bg p-2">
                      <option value="uncertain">Uncertain</option><option value="alert">Alert</option><option value="noise">Noise</option>
                    </select>
                  </label>
                  <label className="block text-sm">Reason
                    <textarea required maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} className="mt-1 block w-full rounded border border-white/15 bg-app-bg p-2" rows={3} />
                  </label>
                  <button disabled={saving || !reason.trim()} className="rounded-lg bg-primary px-4 py-2 text-sm text-white disabled:opacity-50">{saving ? 'Saving…' : 'Record assessment'}</button>
                  {message && <p role="status" className="text-sm text-text-secondary">{message}</p>}
                </form>
              </div>
              <div><h3 className="text-sm font-semibold">Review history</h3>
                {detail.reviews.length === 0 ? <p className="mt-2 text-sm text-text-muted">No assessments recorded.</p> : <ul className="mt-2 space-y-3">{detail.reviews.map((review, index) => <li key={`${review.created_at}-${index}`} className="break-words text-sm text-text-secondary"><strong>{review.assessment}</strong> · {review.actor_id} · {stamp(review.created_at)}<br />{review.reason}</li>)}</ul>}
              </div>
            </div>}
          </section>
        </div>
      </div>
    </div>
  );
}

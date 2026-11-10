import { useEffect, useRef, useState, type FormEvent } from 'react';
import { authFetch } from '../../contexts/AuthGateContext';
import { useCurrentUser } from '../../hooks/useCurrentUser';
import { ContributionReview } from './ContributionReview';

type KnowledgeItem = {
    knowledge_id: string;
    title: string;
    kind: string;
    status: string;
    incident_id: string;
    content: string;
    job_key: string;
};

type HistoryItem = {
    incident_id: string;
    title: string;
    incident_status: string;
    summary: string | null;
    root_cause: string | null;
    resolution: string | null;
    verification_status: string;
    executed_steps: Array<{ action: string; status: string }>;
};

type KnowledgeResponse = {
    items: KnowledgeItem[];
    submissions: KnowledgeItem[];
    history: HistoryItem[];
    items_truncated?: boolean;
    job_match_source?: string | null;
    sop_decision?: string | null;
};

function fileToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error('Could not read the selected file.'));
        reader.onload = () => {
            if (typeof reader.result !== 'string' || !reader.result.includes(',')) {
                reject(new Error('Could not read the selected file.'));
                return;
            }
            resolve(reader.result.slice(reader.result.indexOf(',') + 1));
        };
        reader.readAsDataURL(file);
    });
}

export function IncidentKnowledgePanel({ incidentId, initialJob, incidentStatus, hasPlan }: { incidentId: string; initialJob?: string | null; incidentStatus?: string; hasPlan: boolean }) {
    const [data, setData] = useState<KnowledgeResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [message, setMessage] = useState('');
    const [kind, setKind] = useState<'SOP' | 'DEPENDENCY'>('SOP');
    const [job, setJob] = useState(initialJob || '');
    const [title, setTitle] = useState('');
    const [content, setContent] = useState('');
    const [file, setFile] = useState<File | null>(null);
    const fileInput = useRef<HTMLInputElement>(null);
    const [busy, setBusy] = useState(false);
    const [unassigned, setUnassigned] = useState(false);
    const [assignmentReason, setAssignmentReason] = useState('');
    const user = useCurrentUser();
    const canEdit = user.status === 'authenticated' && user.roles.some((role) => ['admin', 'operator', 'commander'].includes(role));
    const status = (incidentStatus || '').toUpperCase();
    const canSubmit = ['DETECTED', 'PLAN_READY', 'AWAITING_APPROVAL', 'MANUAL_ACTION_REQUIRED'].includes(status);
    const canReinvestigate = hasPlan && ['PLAN_READY', 'AWAITING_APPROVAL', 'MANUAL_ACTION_REQUIRED'].includes(status);

    const load = async () => {
        const res = await authFetch(`/api/v2/incidents/${encodeURIComponent(incidentId)}/knowledge`);
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail || `Unable to load references (HTTP ${res.status}).`);
        }
        setData(await res.json());
    };

    useEffect(() => {
        let active = true;
        setData(null);
        setUnassigned(false);
        setAssignmentReason('');
        setJob(initialJob || '');
        setError(null);
        setMessage('');
        setTitle('');
        setContent('');
        setFile(null);
        authFetch(`/api/v2/incidents/${encodeURIComponent(incidentId)}/knowledge`)
            .then(async (res) => {
                if (!res.ok) {
                    const body = await res.json().catch(() => ({}));
                    if (active && res.status === 409) setUnassigned(true);
                    throw new Error(body.detail || `Unable to load references (HTTP ${res.status}).`);
                }
                return res.json();
            })
            .then((body) => { if (active) setData(body); })
            .catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : 'Unable to load references.'); });
        return () => { active = false; };
    }, [incidentId, initialJob]);

    const assignWorkspace = async (event: FormEvent) => {
        event.preventDefault();
        if (!window.confirm(`Assign this incident to workspace "${user.workspaceId}"? Verify its ownership first. This cannot be undone here.`)) return;
        setBusy(true);
        setError(null);
        try {
            const response = await authFetch(`/api/v2/admin/incidents/${encodeURIComponent(incidentId)}/assign-workspace`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reason: assignmentReason }),
            });
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(typeof body.detail === 'string' ? body.detail : `Assignment failed (HTTP ${response.status}).`);
            }
            setUnassigned(false);
            setAssignmentReason('');
            await load();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Assignment failed.');
        } finally {
            setBusy(false);
        }
    };

    const confirmNoSop = async () => {
        if (!window.confirm('Confirm that no SOP is available for this incident?')) return;
        setBusy(true);
        setError(null);
        setMessage('');
        try {
            const response = await authFetch(`/api/v2/incidents/${encodeURIComponent(incidentId)}/knowledge/no-sop`, { method: 'POST' });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Unable to record decision (HTTP ${response.status}).`);
            await load();
            setMessage(hasPlan
                ? 'Decision recorded. Request re-investigation when ready.'
                : 'Decision recorded. Start triage from the Overview tab.');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Unable to record decision.');
        } finally {
            setBusy(false);
        }
    };

    const requestReinvestigation = async () => {
        setBusy(true);
        setError(null);
        setMessage('');
        try {
            const response = await authFetch(`/api/v2/incidents/${encodeURIComponent(incidentId)}/knowledge/reinvestigate`, { method: 'POST' });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (HTTP ${response.status}).`);
            setMessage(body.reinvestigation_triggered
                ? 'Re-investigation requested. Check Commander for its result.'
                : 'Re-investigation was not started. Check Commander for details.');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Unable to request re-investigation.');
        } finally {
            setBusy(false);
        }
    };

    const submit = async (event: FormEvent) => {
        event.preventDefault();
        setBusy(true);
        setError(null);
        setMessage('');
        try {
            if (kind === 'SOP' && (!file || file.size > 2 * 1024 * 1024)) {
                throw new Error('Select an SOP file no larger than 2 MB.');
            }
            const payload = {
                kind,
                job_key: job,
                title,
                content: kind === 'DEPENDENCY' ? content : '',
                filename: kind === 'SOP' && file ? file.name : '',
                file_base64: kind === 'SOP' && file ? await fileToBase64(file) : '',
            };
            const res = await authFetch(`/api/v2/incidents/${encodeURIComponent(incidentId)}/knowledge`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(typeof body.detail === 'string' ? body.detail : `Submission failed (HTTP ${res.status}).`);
            }
            setMessage(hasPlan
                ? 'Submitted for admin review. Request re-investigation to consider it.'
                : 'Submitted for admin review. Use Start Triage in the Overview tab to investigate.');
            setTitle('');
            setContent('');
            setFile(null);
            if (fileInput.current) fileInput.current.value = '';
            try {
                await load();
            } catch {
                setError('Submission succeeded, but the reference list could not be refreshed. Reload the page to check its status.');
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Submission failed.');
        } finally {
            setBusy(false);
        }
    };

    return (
        <section className="console-surface rounded-2xl p-5 space-y-4" aria-labelledby="incident-reference-title">
            <div>
                <h2 id="incident-reference-title" className="text-sm font-semibold text-text-primary">SOPs, dependencies and history</h2>
                <p className="mt-1 text-xs text-text-secondary">Have an SOP for this failure, or know a dependency that is missing? Submit it for review. A new investigation is needed to consider it.</p>
                <p className="mt-1 text-xs text-text-muted">Submissions are unverified incident context. Admin approval is required before reuse in other incidents. Agents can request additional pages of accepted references, but incomplete initial context still requires manual review. Reference approval does not authorize execution.</p>
            </div>
            {error && <p role="alert" className="text-xs text-critical">{error}</p>}
            {message && <p role="status" className="text-xs text-healthy">{message}</p>}
            {unassigned && user.status === 'authenticated' && user.roles.includes('admin') && (
                <form onSubmit={assignWorkspace} className="grid gap-2 text-xs">
                    <p className="text-text-secondary">After confirming ownership, assign this incident to your workspace: {user.workspaceId}.</p>
                    <label className="grid gap-1">Reason for assignment
                        <input required maxLength={1000} value={assignmentReason} onChange={(e) => setAssignmentReason(e.target.value)} className="rounded-lg border border-border-color bg-surface px-3 py-2 text-text-primary" />
                    </label>
                    <button disabled={busy} type="submit" className="justify-self-start rounded-lg bg-primary px-4 py-2 font-semibold text-white disabled:opacity-50">Assign workspace</button>
                </form>
            )}
            {data && (
                <>
                    <div className="space-y-1 text-xs text-text-secondary">
                        <button type="button" disabled={busy} onClick={() => { setError(null); void load().catch((err: unknown) => setError(err instanceof Error ? err.message : 'Unable to load references.')); }} className="rounded-lg border border-border-color px-3 py-1.5 text-text-primary disabled:opacity-50">Refresh reference statuses</button>
                        {data.items_truncated && <p role="alert" className="text-warning">Only the first 20 matching references are shown. Additional references require manual review.</p>}
                        {data.job_match_source === 'unverified_email_identifier' && <p role="status" className="text-warning">A possible SOP match was found in the alert email. Confirm the workflow and SOP applicability before relying on it.</p>}
                        {data.items.length === 0 && <p>No submitted or approved references matched this incident.</p>}
                        {data.items.map((item) => (
                            <details key={item.knowledge_id} className="rounded-xl border border-border-color p-3">
                                <summary className="cursor-pointer font-medium text-text-primary">
                                    {item.kind}: {item.title} · {item.status}{item.incident_id !== incidentId ? ' · from a previous incident' : ''}
                                </summary>
                                <p className="mt-2 text-text-muted">Reference {item.knowledge_id} · Job or service: {item.job_key} · Incident: {item.incident_id}</p>
                                <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans text-text-secondary">{item.content}</pre>
                            </details>
                        ))}
                        {data.submissions.filter((item) => item.status === 'REJECTED').map((item) => (
                            <p key={item.knowledge_id}>{item.kind}: {item.title} · REJECTED</p>
                        ))}
                    </div>
                    {data.history.length > 0 && (
                        <div className="space-y-2 text-xs text-text-secondary">
                            <h3 className="font-semibold text-text-primary">Previous incidents for this job</h3>
                            {data.history.map((item) => (
                                <div key={item.incident_id} className="rounded-xl border border-border-color p-3">
                                    <p className="font-semibold">{item.title} · {item.incident_id} · {item.incident_status}</p>
                                    {item.summary && <p className="mt-1">{item.summary}</p>}
                                    {item.root_cause && <p className="mt-1">Recorded cause: {item.root_cause}</p>}
                                    {item.resolution && <p className="mt-1">Recorded resolution: {item.resolution}</p>}
                                    {item.executed_steps.length > 0 && (
                                        <ul className="mt-1 list-disc pl-4">
                                            {item.executed_steps.map((step, index) => <li key={index}>{step.action} · {step.status}</li>)}
                                        </ul>
                                    )}
                                    <p className="mt-1">Verification: {item.verification_status}</p>
                                </div>
                            ))}
                            <p className="text-text-muted">Past incidents are references, not proof of the current cause.</p>
                        </div>
                    )}
                    {canEdit && <ContributionReview key={incidentId} incidentId={incidentId} canDraft={status === 'RESOLVED'} initialJob={initialJob} />}
                    {canEdit && canReinvestigate && (
                        <button type="button" disabled={busy} onClick={() => void requestReinvestigation()} className="rounded-lg border border-border-color px-4 py-2 text-xs font-semibold text-text-primary disabled:opacity-50">
                            Re-investigate using references
                        </button>
                    )}
                    {canEdit && !canSubmit && (
                        <p className="text-xs text-text-secondary">References can be submitted when the incident is detected or at a plan-review checkpoint, not during investigation or execution.</p>
                    )}
                    {canEdit && canSubmit && (
                        <div className="space-y-2 text-xs text-text-secondary">
                            <p>Is there an SOP for this failure? Submit it below, or confirm none is available before starting triage.</p>
                            {!data.sop_decision && !data.items.some((item) => item.kind === 'SOP') && (
                                <button type="button" disabled={busy} onClick={() => void confirmNoSop()} className="rounded-lg border border-border-color px-4 py-2 font-semibold text-text-primary disabled:opacity-50">No SOP is available</button>
                            )}
                            {data.sop_decision === 'NO_SOP' && <p>No-SOP decision recorded. {hasPlan ? 'Request re-investigation when ready.' : 'Start triage from the Overview tab.'}</p>}
                        </div>
                    )}
                    {canEdit && canSubmit && <form onSubmit={submit} className="grid gap-3 text-xs">
                        <label className="grid gap-1">Reference type
                            <select value={kind} onChange={(e) => setKind(e.target.value as 'SOP' | 'DEPENDENCY')} className="rounded-lg border border-border-color bg-surface px-3 py-2 text-text-primary">
                                <option value="SOP">SOP document</option>
                                <option value="DEPENDENCY">Dependency description</option>
                            </select>
                        </label>
                        <label className="grid gap-1">Job or service identifier
                            <input required maxLength={200} value={job} onChange={(e) => setJob(e.target.value)} className="rounded-lg border border-border-color bg-surface px-3 py-2 text-text-primary" />
                        </label>
                        <label className="grid gap-1">Title
                            <input required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} className="rounded-lg border border-border-color bg-surface px-3 py-2 text-text-primary" />
                        </label>
                        {kind === 'SOP' ? (
                            <label className="grid gap-1">Document (.txt, .docx, text-based .pdf; up to 2 MB)
                                <input ref={fileInput} required type="file" accept=".txt,.docx,.pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} className="text-text-secondary" />
                            </label>
                        ) : (
                            <label className="grid gap-1">Dependency details
                                <textarea required maxLength={100000} rows={4} value={content} onChange={(e) => setContent(e.target.value)} placeholder="Describe upstream and downstream systems and how they depend on each other." className="rounded-lg border border-border-color bg-surface px-3 py-2 text-text-primary" />
                            </label>
                        )}
                        <button disabled={busy} type="submit" className="justify-self-start rounded-lg bg-primary px-4 py-2 font-semibold text-white disabled:opacity-50">{busy ? 'Submitting…' : 'Submit for review'}</button>
                    </form>}
                </>
            )}
        </section>
    );
}

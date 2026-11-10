import { useEffect, useState, type FormEvent } from 'react';
import { authFetch } from '../../contexts/AuthGateContext';

type Contribution = {
    message_id: string;
    intent: string | null;
    content: string;
    created_by: string | null;
    created_at: string;
};

type ContributionPage = {
    items: Contribution[];
    next_offset: number | null;
    notice: string;
};

export function ContributionReview({ incidentId, canDraft, initialJob }: { incidentId: string; canDraft: boolean; initialJob?: string | null }) {
    const [items, setItems] = useState<Contribution[]>([]);
    const [nextOffset, setNextOffset] = useState<number | null>(null);
    const [loaded, setLoaded] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [selected, setSelected] = useState<string[]>([]);
    const [job, setJob] = useState(initialJob || '');
    const [title, setTitle] = useState('');
    const [content, setContent] = useState('');
    const [confirmed, setConfirmed] = useState(false);
    const [message, setMessage] = useState('');
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        setItems([]);
        setNextOffset(null);
        setLoaded(false);
        setError('');
    }, [incidentId]);

    const load = async (offset: number) => {
        setLoading(true);
        setError('');
        try {
            const response = await authFetch(
                `/api/v2/incidents/${encodeURIComponent(incidentId)}/knowledge/contributions?offset=${offset}&limit=100`,
            );
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(typeof body.detail === 'string' ? body.detail : `Unable to load contributions (HTTP ${response.status}).`);
            }
            const page: ContributionPage = await response.json();
            setItems((current) => offset === 0 ? page.items : [...current, ...page.items]);
            setNextOffset(page.next_offset);
            setLoaded(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Unable to load contributions.');
        } finally {
            setLoading(false);
        }
    };

    const submitDraft = async (event: FormEvent) => {
        event.preventDefault();
        if (!selected.length || !confirmed) {
            setError('Select at least one source and confirm the proposed procedure.');
            return;
        }
        setSubmitting(true);
        setError('');
        setMessage('');
        try {
            const response = await authFetch(`/api/v2/incidents/${encodeURIComponent(incidentId)}/knowledge/contribution-drafts`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_key: job, title, content, message_ids: selected, confirmed }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Submission failed (HTTP ${response.status}).`);
            setMessage('Draft submitted for administrator review. It is not approved for reuse.');
            setSelected([]);
            setTitle('');
            setContent('');
            setConfirmed(false);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Unable to submit draft.');
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <section className="space-y-2 rounded-xl border border-border-color p-3 text-xs">
            <h3 className="font-semibold text-text-primary">Operator contributions</h3>
            <p className="text-text-secondary">
                These messages are unverified source material, not approved SOP steps. Review and confirm any proposed procedure before submitting it for administrator approval.
            </p>
            {error && <p role="alert" className="text-critical">{error}</p>}
            {message && <p role="status" className="text-healthy">{message}</p>}
            <button type="button" disabled={loading} onClick={() => void load(0)} className="rounded-lg border border-border-color px-3 py-1.5 font-semibold text-text-primary disabled:opacity-50">
                {loading ? 'Loading…' : loaded ? 'Refresh contributions' : 'View contributions'}
            </button>
            {loaded && items.length === 0 && <p className="text-text-secondary">No operator contributions recorded.</p>}
            {items.map((item) => (
                <article key={item.message_id} className="rounded-lg bg-surface p-3">
                    <p className="text-text-muted">{item.created_by || 'Operator'} · {item.created_at} · {item.intent || 'Message'}</p>
                    <p className="mt-1 whitespace-pre-wrap break-words text-text-primary">{item.content}</p>
                    {canDraft && <label className="mt-2 flex gap-2 text-text-secondary">
                        <input type="checkbox" checked={selected.includes(item.message_id)} onChange={(e) => {
                            setSelected((current) => e.target.checked
                                ? [...current, item.message_id]
                                : current.filter((id) => id !== item.message_id));
                            setConfirmed(false);
                        }} />
                        Use as a source for a proposed SOP
                    </label>}
                </article>
            ))}
            {nextOffset !== null && (
                <button type="button" disabled={loading} onClick={() => void load(nextOffset)} className="rounded-lg border border-border-color px-3 py-1.5 font-semibold text-text-primary disabled:opacity-50">
                    Load more contributions
                </button>
            )}
            {canDraft && loaded && items.length > 0 && (
                <form onSubmit={submitDraft} className="grid gap-2 border-t border-border-color pt-3">
                    <h4 className="font-semibold text-text-primary">Propose an SOP for review</h4>
                    <label className="grid gap-1">Job or service
                        <input required maxLength={200} value={job} onChange={(e) => { setJob(e.target.value); setConfirmed(false); }} className="rounded-lg border border-border-color bg-surface p-2 text-text-primary" />
                    </label>
                    <label className="grid gap-1">Title
                        <input required maxLength={200} value={title} onChange={(e) => { setTitle(e.target.value); setConfirmed(false); }} className="rounded-lg border border-border-color bg-surface p-2 text-text-primary" />
                    </label>
                    <label className="grid gap-1">Proposed procedure
                        <textarea required maxLength={100000} rows={5} value={content} onChange={(e) => { setContent(e.target.value); setConfirmed(false); }} className="rounded-lg border border-border-color bg-surface p-2 text-text-primary" />
                    </label>
                    <label className="flex gap-2 text-text-secondary">
                        <input required type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
                        I have reviewed these sources and confirm this is my proposed procedure.
                    </label>
                    <button type="submit" disabled={submitting || selected.length === 0 || selected.length > 100 || !confirmed} className="justify-self-start rounded-lg bg-primary px-3 py-2 font-semibold text-white disabled:opacity-50">
                        {submitting ? 'Submitting…' : 'Submit draft for admin review'}
                    </button>
                </form>
            )}
        </section>
    );
}

import { useEffect, useState } from 'react';
import { authFetch } from '../../contexts/AuthGateContext';
import { KnowledgeSources } from './KnowledgeSources';

type Item = {
    knowledge_id: string;
    incident_id: string;
    job_key: string;
    kind: string;
    title: string;
    content: string;
    submitted_by: string;
    submitted_at: string;
};

export function KnowledgeReview({ refreshRevision, onChanged }: { refreshRevision: number; onChanged: () => void }) {
    const [items, setItems] = useState<Item[]>([]);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState('');
    const [note, setNote] = useState<Record<string, string>>({});

    const load = async () => {
        const response = await authFetch('/api/v2/admin/incident-knowledge');
        if (!response.ok) throw new Error(`Unable to load submissions (HTTP ${response.status}).`);
        const body = await response.json();
        setItems(body.items);
    };

    useEffect(() => {
        let active = true;
        setLoading(true);
        setError('');
        authFetch('/api/v2/admin/incident-knowledge')
            .then(async (res) => {
                if (!res.ok) throw new Error(`Unable to load submissions (HTTP ${res.status}).`);
                return res.json();
            })
            .then((body) => { if (active) setItems(body.items); })
            .catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : 'Unable to load submissions.'); })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [refreshRevision]);

    const review = async (item: Item, decision: 'APPROVED' | 'REJECTED') => {
        setBusy(item.knowledge_id);
        setError('');
        try {
            const response = await authFetch(`/api/v2/admin/incident-knowledge/${encodeURIComponent(item.knowledge_id)}/review`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decision, note: note[item.knowledge_id] || '' }),
            });
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(typeof body.detail === 'string' ? body.detail : `Review failed (HTTP ${response.status}).`);
            }
            onChanged();
            setItems((current) => current.filter((pending) => pending.knowledge_id !== item.knowledge_id));
            try {
                await load();
            } catch {
                setError('Review saved, but the list could not be refreshed. Reload the page to see the current queue.');
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Review failed.');
        } finally {
            setBusy(null);
        }
    };

    return (
        <section className="rounded-[24px] border border-[#0F3F32]/10 bg-[#FCFDFB] p-5 space-y-4">
            <h2 className="text-base font-bold text-[#13231E]">SOP and dependency review</h2>
            <p className="text-xs text-[#62716B]">Review the full submitted text before allowing reuse for the specified job or service in this workspace. Approval does not authorize execution.</p>
            {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
            {loading ? <p className="text-xs text-[#62716B]">Loading submissions…</p> : items.length === 0 ? (
                !error && <p className="text-xs text-[#62716B]">No submissions awaiting review.</p>
            ) : items.map((item) => (
                <article key={item.knowledge_id} className="rounded-xl border border-[#0F3F32]/15 p-4 space-y-3">
                    <h3 className="text-sm font-semibold text-[#13231E]">{item.title} · {item.kind}</h3>
                    <p className="text-xs text-[#62716B]">Job: {item.job_key} · Incident: {item.incident_id} · Submitted by {item.submitted_by} · {item.submitted_at}</p>
                    <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-[#EDF3EE] p-3 text-xs text-[#13231E]">{item.content}</pre>
                    <KnowledgeSources knowledgeId={item.knowledge_id} />
                    <label className="block text-xs text-[#62716B]">Review note
                        <textarea maxLength={2000} value={note[item.knowledge_id] || ''} onChange={(e) => setNote((prev) => ({ ...prev, [item.knowledge_id]: e.target.value }))} className="mt-1 block w-full rounded-lg border border-[#BED2C7] bg-white p-2 text-[#13231E]" />
                    </label>
                    <div className="flex gap-2">
                        <button type="button" disabled={busy !== null} onClick={() => void review(item, 'APPROVED')} className="rounded-lg bg-[#0C6F58] px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Approve reuse</button>
                        <button type="button" disabled={busy !== null} onClick={() => void review(item, 'REJECTED')} className="rounded-lg border border-red-300 px-3 py-2 text-xs font-semibold text-red-700 disabled:opacity-50">Reject</button>
                    </div>
                </article>
            ))}
        </section>
    );
}

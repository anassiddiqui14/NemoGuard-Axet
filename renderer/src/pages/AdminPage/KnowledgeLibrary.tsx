import { useEffect, useState, type FormEvent } from 'react';
import { authFetch } from '../../contexts/AuthGateContext';

type Document = {
    knowledge_id: string;
    incident_id: string;
    job_key: string;
    kind: string;
    title: string;
    content: string;
    status: string;
};

export function KnowledgeLibrary({ refreshRevision, onChanged }: { refreshRevision: number; onChanged: () => void }) {
    const [items, setItems] = useState<Document[]>([]);
    const [offset, setOffset] = useState(0);
    const [nextOffset, setNextOffset] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [notice, setNotice] = useState('');
    const [editing, setEditing] = useState<Document | null>(null);
    const [reason, setReason] = useState('');
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        authFetch(`/api/v2/admin/incident-knowledge-library?offset=${offset}&limit=50`, { signal: controller.signal })
            .then(async (response) => {
                if (!response.ok) throw new Error(`Unable to load documents (HTTP ${response.status}).`);
                return response.json();
            })
            .then((body) => { setItems(body.items); setNextOffset(body.next_offset); })
            .catch((err: unknown) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load documents.'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [offset, refreshRevision]);

    const refresh = async () => {
        const response = await authFetch(`/api/v2/admin/incident-knowledge-library?offset=${offset}&limit=50`);
        if (!response.ok) throw new Error('Change saved, but the library could not be refreshed.');
        const body = await response.json();
        setItems(body.items);
        setNextOffset(body.next_offset);
    };

    const save = async (event: FormEvent) => {
        event.preventDefault();
        if (!editing || !reason.trim()) return;
        setBusy(true);
        setError('');
        setNotice('');
        try {
            const response = await authFetch(`/api/v2/admin/incident-knowledge/${encodeURIComponent(editing.knowledge_id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: editing.title, job_key: editing.job_key, content: editing.content, reason }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Unable to save document.');
            onChanged();
            setEditing(null);
            setReason('');
            setNotice('Document updated and returned to pending review.');
            await refresh();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Unable to save document.');
        } finally {
            setBusy(false);
        }
    };

    const archive = async (item: Document) => {
        const why = window.prompt(`Reason for archiving "${item.title}":`);
        if (why === null) return;
        if (!why.trim()) { setError('A reason is required.'); return; }
        if (!window.confirm('Archive this document? Pending plans in this workspace will require review.')) return;
        setBusy(true);
        setError('');
        setNotice('');
        try {
            const response = await authFetch(`/api/v2/admin/incident-knowledge/${encodeURIComponent(item.knowledge_id)}/archive`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reason: why }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Unable to archive document.');
            onChanged();
            setNotice('Document archived.');
            await refresh();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Unable to archive document.');
        } finally {
            setBusy(false);
        }
    };

    return (
        <section className="rounded-[24px] border border-[#0F3F32]/10 bg-[#FCFDFB] p-5 space-y-4">
            <h2 className="text-base font-bold text-[#13231E]">SOP and dependency library</h2>
            <p className="text-xs text-[#62716B]">Changes are blocked during active workspace investigations or execution. Editing returns a document to pending review; changes require pending workspace plans to be reviewed again.</p>
            {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
            {notice && <p role="status" className="text-xs text-[#0C6F58]">{notice}</p>}
            {loading ? <p>Loading documents…</p> : items.length === 0 ? <p>No documents on this page.</p> : items.map((item) => (
                <article key={item.knowledge_id} className="rounded-xl border border-[#0F3F32]/15 p-4 space-y-2 text-xs">
                    <h3 className="font-semibold">{item.title} · {item.kind} · {item.status}</h3>
                    <p>Job: {item.job_key} · Incident: {item.incident_id}</p>
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-[#EDF3EE] p-3">{item.content}</pre>
                    {item.status !== 'ARCHIVED' && (
                        <div className="flex gap-2">
                            <button type="button" disabled={busy} onClick={() => { setEditing({ ...item }); setReason(''); setError(''); }} className="rounded-lg border border-[#BED2C7] px-3 py-2 disabled:opacity-50">Edit</button>
                            <button type="button" disabled={busy} onClick={() => void archive(item)} className="rounded-lg border border-red-300 px-3 py-2 text-red-700 disabled:opacity-50">Archive</button>
                        </div>
                    )}
                </article>
            ))}
            <div className="flex gap-2 text-xs">
                <button type="button" disabled={loading || busy || offset === 0} onClick={() => { setEditing(null); setOffset(Math.max(0, offset - 50)); }} className="rounded-lg border px-3 py-2 disabled:opacity-50">Previous</button>
                <button type="button" disabled={loading || busy || nextOffset === null} onClick={() => { if (nextOffset !== null) { setEditing(null); setOffset(nextOffset); } }} className="rounded-lg border px-3 py-2 disabled:opacity-50">Next</button>
            </div>
            {editing && (
                <form onSubmit={save} className="grid gap-2 rounded-xl border p-4 text-xs">
                    <h3 className="font-semibold">Edit {editing.title}</h3>
                    <label>Title<input required maxLength={200} value={editing.title} onChange={(e) => setEditing({ ...editing, title: e.target.value })} className="block w-full rounded border p-2" /></label>
                    <label>Job or service<input required maxLength={200} value={editing.job_key} onChange={(e) => setEditing({ ...editing, job_key: e.target.value })} className="block w-full rounded border p-2" /></label>
                    <label>Content<textarea required maxLength={100000} rows={8} value={editing.content} onChange={(e) => setEditing({ ...editing, content: e.target.value })} className="block w-full rounded border p-2" /></label>
                    <label>Reason<input required maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} className="block w-full rounded border p-2" /></label>
                    <div className="flex gap-2">
                        <button type="submit" disabled={busy} className="rounded-lg bg-[#0C6F58] px-3 py-2 font-semibold text-white disabled:opacity-50">Save for review</button>
                        <button type="button" disabled={busy} onClick={() => setEditing(null)} className="rounded-lg border px-3 py-2">Cancel</button>
                    </div>
                </form>
            )}
        </section>
    );
}

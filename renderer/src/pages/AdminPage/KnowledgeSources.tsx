import { useState } from 'react';
import { authFetch } from '../../contexts/AuthGateContext';

type Source = {
    message_id: string;
    intent: string | null;
    content: string;
    created_by: string | null;
    created_at: string;
};

export function KnowledgeSources({ knowledgeId }: { knowledgeId: string }) {
    const [sources, setSources] = useState<Source[] | null>(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    async function load() {
        setLoading(true);
        setError('');
        try {
            const response = await authFetch(`/api/v2/admin/incident-knowledge/${encodeURIComponent(knowledgeId)}/sources`);
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(typeof body.detail === 'string' ? body.detail : `Unable to load sources (HTTP ${response.status}).`);
            }
            const body: { items: Source[] } = await response.json();
            setSources(body.items);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Unable to load sources.');
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="space-y-2 text-xs text-[#62716B]">
            <button type="button" disabled={loading} onClick={() => void load()} className="rounded-lg border border-[#BED2C7] px-3 py-2 font-semibold disabled:opacity-50">
                {loading ? 'Loading sources…' : 'View contribution sources'}
            </button>
            {error && <p role="alert" className="text-red-700">{error}</p>}
            {sources && sources.length === 0 && <p>No linked operator contribution sources.</p>}
            {sources && sources.length > 0 && <p>Operator messages are unverified source material, not proof that the proposed procedure is safe.</p>}
            {sources?.map((source) => (
                <article key={source.message_id} className="rounded-lg bg-[#EDF3EE] p-3">
                    <p>{source.created_by || 'Operator'} · {source.created_at} · {source.intent || 'Message'}</p>
                    <p className="mt-1 whitespace-pre-wrap break-words text-[#13231E]">{source.content}</p>
                </article>
            ))}
        </div>
    );
}

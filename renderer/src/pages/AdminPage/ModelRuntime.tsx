import { useEffect, useState } from 'react';
import { authFetch } from '../../contexts/AuthGateContext';

type Operation = { key: string; label: string; override: string | null };
type RuntimeStatus = {
    model: string | null;
    source: string;
    operations: Operation[];
    scope: string;
    editable: boolean;
};

export function ModelRuntime() {
    const [status, setStatus] = useState<RuntimeStatus | null>(null);
    const [drafts, setDrafts] = useState<Record<string, string>>({});
    const [error, setError] = useState('');
    const [notice, setNotice] = useState('');
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState<string | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        authFetch('/api/v2/admin/model-runtime', { signal: controller.signal })
            .then(async (response) => {
                if (!response.ok) throw new Error(`Model status unavailable (HTTP ${response.status}). Check that the updated backend is deployed.`);
                return response.json() as Promise<RuntimeStatus>;
            })
            .then((body) => {
                if (!controller.signal.aborted) {
                    setStatus(body);
                    setDrafts(Object.fromEntries(body.operations.map((operation) => [operation.key, operation.override || ''])));
                }
            })
            .catch((err: unknown) => {
                if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load model status.');
            })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, []);

    const save = async (operation: Operation, modelId: string | null) => {
        if (modelId !== null && !modelId.trim()) {
            setError('Enter the exact provider model ID, or use Reset to default.');
            return;
        }
        const value = modelId === null ? null : modelId.trim();
        if (!window.confirm(value
            ? `Use "${value}" for ${operation.label}? The provider must support this exact ID or future operations may fail.`
            : `Reset ${operation.label} to its runtime default?`)) return;
        setBusy(operation.key);
        setError('');
        setNotice('');
        try {
            const response = await authFetch(`/api/v2/admin/model-runtime/${encodeURIComponent(operation.key)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model_id: value }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Unable to save model (HTTP ${response.status}).`);
            setStatus((current) => current ? {
                ...current,
                operations: current.operations.map((item) => item.key === operation.key ? { ...item, override: value } : item),
            } : current);
            setDrafts((current) => ({ ...current, [operation.key]: value || '' }));
            setNotice(`${operation.label} model setting saved for new operations.`);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Unable to save model.');
        } finally {
            setBusy(null);
        }
    };

    return (
        <section className="rounded-[24px] border border-[#0F3F32]/10 bg-[#FCFDFB] p-5 space-y-3">
            <h2 className="text-base font-bold text-[#13231E]">AI model configuration</h2>
            {loading && <p className="text-xs">Loading model configuration…</p>}
            {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
            {notice && <p role="status" className="text-xs text-[#0C6F58]">{notice}</p>}
            {status && (
                <>
                    <p className="text-xs">API process default: <strong>{status.model || 'Not configured'}</strong> ({status.source})</p>
                    <p className="text-xs text-[#62716B]">{status.scope} Unscoped alerts use the runtime default. Changes do not affect in-flight operations.</p>
                    <p className="text-xs text-[#9A5A00]">Enter the exact model ID supported by your aXet provider. Availability and compatibility are not verified here; an unsupported ID can cause operations to fail. GPT-6.1 Terra has not been automatically selected.</p>
                    {status.operations.map((operation) => (
                        <div key={operation.key} className="rounded-xl border border-[#0F3F32]/15 p-3 text-xs space-y-2">
                            <h3 className="font-semibold">{operation.label}</h3>
                            <p>Workspace setting: {operation.override || 'Runtime default (worker default may differ)'}</p>
                            <label className="block">Provider model ID
                                <input value={drafts[operation.key] || ''} onChange={(event) => setDrafts((current) => ({ ...current, [operation.key]: event.target.value }))} maxLength={200} className="mt-1 block w-full rounded border p-2" />
                            </label>
                            <div className="flex gap-2">
                                <button type="button" disabled={busy !== null || !status.editable} onClick={() => void save(operation, drafts[operation.key] || '')} className="rounded-lg bg-[#0C6F58] px-3 py-2 text-white disabled:opacity-50">Save model</button>
                                <button type="button" disabled={busy !== null || !operation.override || !status.editable} onClick={() => void save(operation, null)} className="rounded-lg border px-3 py-2 disabled:opacity-50">Reset to default</button>
                            </div>
                        </div>
                    ))}
                </>
            )}
        </section>
    );
}

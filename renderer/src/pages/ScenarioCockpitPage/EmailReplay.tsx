import { useRef, useState } from 'react';
import { authFetch } from '../../contexts/AuthGateContext';

type Delivery = { payload: string; key: string };

export function EmailReplay() {
    const [file, setFile] = useState<File | null>(null);
    const [secret, setSecret] = useState('');
    const [subject, setSubject] = useState('');
    const [body, setBody] = useState('');
    const [previewed, setPreviewed] = useState(false);
    const [confirmed, setConfirmed] = useState(false);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState('');
    const delivery = useRef<Delivery | null>(null);

    async function preview() {
        if (!file || !secret || busy) return;
        if (!file.name.toLowerCase().endsWith('.msg') || file.size > 5 * 1024 * 1024) {
            setMessage('Choose an Outlook .msg file no larger than 5 MB.');
            return;
        }
        setBusy(true);
        setPreviewed(false);
        setConfirmed(false);
        setMessage('');
        try {
            const response = await authFetch('/api/v2/ingest/email-preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/octet-stream', 'X-NemoGuard-Webhook-Secret': secret },
                body: file,
                cache: 'no-store',
            });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.detail || `Preview failed (HTTP ${response.status}).`);
            }
            const data = await response.json();
            setSubject(data.subject);
            setBody(data.body);
            delivery.current = null;
            setPreviewed(true);
            setMessage('Preview loaded. Review and redact before sending.');
        } catch (error) {
            setMessage(error instanceof Error ? error.message : 'Preview failed.');
        } finally {
            setBusy(false);
        }
    }

    async function send() {
        if (!previewed || !confirmed || !secret || !file || busy) return;
        const payload = JSON.stringify({
            source: 'Outlook email export',
            type: 'Email alert',
            monitor_name: subject,
            message: body,
        });
        setBusy(true);
        setMessage('');
        try {
            if (!delivery.current || delivery.current.payload !== payload) {
                if (!crypto.subtle) throw new Error('A secure browser context is required for replay.');
                const fileBytes = new Uint8Array(await file.arrayBuffer());
                const payloadBytes = new TextEncoder().encode(payload);
                const bytes = new Uint8Array(fileBytes.length + payloadBytes.length);
                bytes.set(fileBytes);
                bytes.set(payloadBytes, fileBytes.length);
                const digest = await crypto.subtle.digest('SHA-256', bytes);
                const hash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
                delivery.current = { payload, key: `email-replay:${hash}` };
            }
            const response = await authFetch('/api/v2/ingest/email-replay', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-NemoGuard-Webhook-Secret': secret,
                    'Idempotency-Key': delivery.current.key,
                },
                body: payload,
                cache: 'no-store',
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                setMessage(`Delivery not confirmed (HTTP ${response.status}). Retry the unchanged preview with the same key; do not assume it failed.`);
            } else if (data.status === 'ignored') {
                setMessage('The watcher classified this message as noise; no alert was created.');
            } else if (typeof data.status === 'string' && data.status.startsWith('ingested')) {
                setMessage(`Result: ${data.status}${data.duplicate ? ' (duplicate delivery)' : ''}${data.incident_id ? ` — incident ${data.incident_id}` : ''}. Ingestion does not guarantee triage completion.`);
            } else {
                setMessage('Delivery outcome is unknown. Retry the unchanged preview with the same key.');
            }
        } catch {
            setMessage('Delivery outcome is unknown. Retry the unchanged preview with the same key.');
        } finally {
            setBusy(false);
        }
    }

    return (
        <section className="glass-panel rounded-2xl ring-1 ring-white/[0.06] p-5 space-y-4">
            <div>
                <h2 className="font-semibold text-text-primary">Email Alert Replay</h2>
                <p className="text-sm text-text-muted">Preview one Outlook .msg export, redact it, then send the reviewed subject and body to NemoGuard.</p>
            </div>
            <p className="text-sm text-warning">Preview uploads the entire file, including attachments, to NemoGuard for parsing. Only the subject and body are displayed; attachments are excluded from replay. Sending passes the reviewed text and incident context to the configured LLM; model output may appear in logs. Confirm your organization permits this data flow.</p>
            <p className="text-sm text-text-muted">If delivery is uncertain, retry the same file and unchanged reviewed text. Editing changes the delivery key and may create a duplicate. Identical files with identical reviewed text are treated as the same delivery.</p>
            <label className="block text-sm">Webhook secret (kept in this page’s memory)
                <input type="password" value={secret} onChange={e => setSecret(e.target.value)} autoComplete="off" className="block w-full mt-1 rounded-lg bg-black/20 p-2 ring-1 ring-white/10" />
            </label>
            <label className="block text-sm">Outlook export (.msg, maximum 5 MB)
                <input type="file" accept=".msg" disabled={busy} onChange={e => { setFile(e.target.files?.[0] ?? null); setPreviewed(false); setConfirmed(false); setSubject(''); setBody(''); setMessage(''); delivery.current = null; }} className="block w-full mt-1" />
            </label>
            <button type="button" disabled={!file || !secret || busy} onClick={preview} className="rounded-lg px-4 py-2 ring-1 ring-white/20 disabled:opacity-40">Preview email</button>
            {previewed && <>
                <label className="block text-sm">Subject
                    <input value={subject} maxLength={1000} disabled={busy} onChange={e => { setSubject(e.target.value); setConfirmed(false); }} className="block w-full mt-1 rounded-lg bg-black/20 p-2 ring-1 ring-white/10" />
                </label>
                <label className="block text-sm">Body
                    <textarea value={body} maxLength={100000} disabled={busy} onChange={e => { setBody(e.target.value); setConfirmed(false); }} rows={12} className="block w-full mt-1 rounded-lg bg-black/20 p-2 ring-1 ring-white/10" />
                </label>
                <label className="flex items-start gap-2 text-sm">
                    <input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />
                    I reviewed and redacted this text and am authorized to send it to the configured model.
                </label>
                <button type="button" disabled={!confirmed || !secret || busy || !body.trim()} onClick={send} className="rounded-lg px-4 py-2 ring-1 ring-primary/40 disabled:opacity-40">{busy ? 'Working…' : 'Send reviewed alert'}</button>
            </>}
            {message && <p role="status" className="text-sm text-text-secondary">{message}</p>}
        </section>
    );
}

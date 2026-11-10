import { useEffect, useRef, useState, type FormEvent } from 'react';
import { MessageCircle, Send, X } from 'lucide-react';
import { authFetch } from '../../contexts/AuthGateContext';
import { CommanderPanel } from '../dashboard/CommanderPanel';
import { useChatContext } from './ChatContext';

type Turn = { role: 'user' | 'assistant'; content: string; failed?: boolean };

export function WorkspaceChat() {
    const { incidentId } = useChatContext();
    const [open, setOpen] = useState(false);
    const [mode, setMode] = useState<'workspace' | 'incident'>('workspace');
    const [turns, setTurns] = useState<Turn[]>([]);
    const [question, setQuestion] = useState('');
    const [sending, setSending] = useState(false);
    const [error, setError] = useState('');
    const bottom = useRef<HTMLDivElement>(null);
    const input = useRef<HTMLTextAreaElement>(null);
    const submitLock = useRef(false);

    useEffect(() => {
        if (!incidentId) setMode('workspace');
    }, [incidentId]);

    useEffect(() => {
        if (open && mode === 'workspace') input.current?.focus();
    }, [open, mode]);

    useEffect(() => {
        if (open && mode === 'workspace') bottom.current?.scrollIntoView({ block: 'nearest' });
    }, [open, mode, turns, sending]);

    async function submit(event: FormEvent) {
        event.preventDefault();
        const text = question.trim();
        if (!text || submitLock.current) return;
        submitLock.current = true;
        const history = turns.filter((turn) => !turn.failed).slice(-10).map(({ role, content }) => ({ role, content: content.slice(0, 2000) }));
        setTurns((current) => [...current, { role: 'user', content: text }]);
        setQuestion('');
        setSending(true);
        setError('');
        try {
            const response = await authFetch('/api/v2/workspace-chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question: text, history, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Chat is unavailable.');
            if (typeof body.answer !== 'string' || !body.answer.trim()) throw new Error('The assistant returned an empty response.');
            setTurns((current) => [...current, { role: 'assistant', content: body.answer }]);
        } catch (err) {
            setTurns((current) => current.map((turn, index) => index === current.length - 1 ? { ...turn, failed: true } : turn));
            setQuestion(text);
            setError(err instanceof Error ? err.message : 'Chat is unavailable.');
        } finally {
            submitLock.current = false;
            setSending(false);
        }
    }

    return (
        <div className="fixed bottom-4 right-4 z-50 sm:bottom-6 sm:right-6">
            <section aria-label="NemoGuard chat" style={{ display: open ? 'flex' : 'none' }} className="mb-3 h-[min(720px,calc(100dvh-7rem))] w-[min(640px,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-[#BED2C7] bg-white text-[#13231E] shadow-2xl">
                <header className="flex items-center justify-between bg-[#073F32] px-4 py-3 text-white">
                    <div><h2 className="font-semibold">NemoGuard assistant</h2><p className="text-xs text-white/75">Workspace and incident chat</p></div>
                    <button type="button" aria-label="Close chat" onClick={() => setOpen(false)} className="rounded-lg p-2 hover:bg-white/10"><X size={18} /></button>
                </header>
                {incidentId && <div className="flex gap-2 border-b p-2 text-xs">
                    <button type="button" aria-pressed={mode === 'workspace'} onClick={() => setMode('workspace')} className={`rounded-lg border px-3 py-2 ${mode === 'workspace' ? 'border-[#0C6F58] bg-[#E1F3EC] font-semibold text-[#0C6F58]' : 'border-[#BED2C7]'}`}>Workspace</button>
                    <button type="button" aria-pressed={mode === 'incident'} onClick={() => setMode('incident')} className={`rounded-lg border px-3 py-2 ${mode === 'incident' ? 'border-[#0C6F58] bg-[#E1F3EC] font-semibold text-[#0C6F58]' : 'border-[#BED2C7]'}`}>Incident: {incidentId}</button>
                </div>}
                <div style={{ display: mode === 'workspace' ? 'flex' : 'none' }} className="min-h-0 flex-1 flex-col">
                    <p className="border-b px-4 py-2 text-xs text-[#62716B]">Read-only. Uses workspace totals and recent samples; inbox outcomes require operator access. Workspace chat resets on reload.</p>
                    <div className="flex-1 space-y-3 overflow-y-auto p-4" aria-live="polite">
                        {turns.length === 0 && <p className="text-sm text-[#62716B]">Ask a question about your workspace or an operations topic.</p>}
                        {turns.map((turn, index) => <div key={index} className={`whitespace-pre-wrap break-words rounded-xl px-3 py-2 text-sm ${turn.role === 'user' ? 'ml-8 bg-[#E1F3EC]' : 'mr-8 bg-[#F1F4F1]'}`}>
                            <span className="mb-1 block text-xs font-semibold">{turn.role === 'user' ? 'You' : 'Assistant'}{turn.failed ? ' · no reply received' : ''}</span>{turn.content}
                        </div>)}
                        {sending && <p className="text-xs text-[#62716B]">Thinking…</p>}
                        <div ref={bottom} />
                    </div>
                    {error && <p role="alert" className="px-4 pb-2 text-xs text-red-700">{error} Your message is back in the input so you can retry.</p>}
                    <form onSubmit={submit} className="flex items-end gap-2 border-t p-3">
                        <label className="sr-only" htmlFor="workspace-chat-question">Ask a question</label>
                        <textarea ref={input} id="workspace-chat-question" value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} disabled={sending} maxLength={2000} rows={2} placeholder="Ask a question… (Shift+Enter for a new line)" className="min-w-0 flex-1 resize-none rounded-lg border border-[#BED2C7] p-2 text-sm" />
                        <button type="submit" disabled={sending || !question.trim()} aria-label="Send question" className="rounded-lg bg-[#0C6F58] p-3 text-white disabled:opacity-50"><Send size={16} /></button>
                    </form>
                </div>
                {incidentId && <div style={{ display: mode === 'incident' ? 'block' : 'none' }} className="min-h-0 flex-1"><CommanderPanel key={incidentId} incidentId={incidentId} embedded /></div>}
            </section>
            <button type="button" aria-label={open ? 'Hide chat' : 'Open chat'} aria-expanded={open} onClick={() => setOpen((value) => !value)} className="ml-auto flex h-14 w-14 items-center justify-center rounded-full bg-[#073F32] text-white shadow-xl hover:bg-[#0C6F58] focus-ring"><MessageCircle size={21} /></button>
        </div>
    );
}

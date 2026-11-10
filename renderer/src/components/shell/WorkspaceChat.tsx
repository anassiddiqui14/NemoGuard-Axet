import { useEffect, useRef, useState, type FormEvent } from 'react';
import { MessageCircle, Send, X } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { authFetch } from '../../contexts/AuthGateContext';

type Turn = { role: 'user' | 'assistant'; content: string };

export function WorkspaceChat() {
    const { pathname } = useLocation();
    const [open, setOpen] = useState(false);
    const [turns, setTurns] = useState<Turn[]>([]);
    const [question, setQuestion] = useState('');
    const [sending, setSending] = useState(false);
    const [error, setError] = useState('');
    const bottom = useRef<HTMLDivElement>(null);
    const input = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        if (open) {
            bottom.current?.scrollIntoView({ block: 'nearest' });
            input.current?.focus();
        }
    }, [open, turns]);

    async function submit(event: FormEvent) {
        event.preventDefault();
        const text = question.trim();
        if (!text || sending) return;
        const history = turns.slice(-10).map((turn) => ({ ...turn, content: turn.content.slice(0, 2000) }));
        setSending(true);
        setError('');
        try {
            const response = await authFetch('/api/v2/workspace-chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question: text, history }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Chat is unavailable.');
            if (typeof body.answer !== 'string' || !body.answer.trim()) throw new Error('The assistant returned an empty response.');
            setTurns((current) => [...current, { role: 'user', content: text }, { role: 'assistant', content: body.answer }]);
            setQuestion('');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Chat is unavailable.');
        } finally {
            setSending(false);
        }
    }

    return (
        <div className={`fixed right-4 z-50 sm:right-6 ${pathname.startsWith('/app/command-center') ? 'bottom-24' : 'bottom-4 sm:bottom-6'}`}>
            {open && (
                <section aria-label="Workspace assistant" className="mb-3 flex h-[min(520px,70vh)] w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-[#BED2C7] bg-white text-[#13231E] shadow-2xl">
                    <header className="flex items-center justify-between bg-[#073F32] px-4 py-3 text-white">
                        <div><h2 className="font-semibold">Workspace assistant</h2><p className="text-xs text-white/75">General questions · read-only</p></div>
                        <button type="button" aria-label="Close chat" onClick={() => setOpen(false)} className="rounded-lg p-2 hover:bg-white/10"><X size={18} /></button>
                    </header>
                    <p className="border-b px-4 py-2 text-xs text-[#62716B]">Uses up to 10 recent incidents from your workspace. For detailed incident questions, use its Commander chat. Messages remain in this session only.</p>
                    <div className="flex-1 space-y-3 overflow-y-auto p-4" aria-live="polite">
                        {turns.length === 0 && <p className="text-sm text-[#62716B]">Ask a question about your workspace or a general operations topic.</p>}
                        {turns.map((turn, index) => (
                            <div key={index} className={`whitespace-pre-wrap break-words rounded-xl px-3 py-2 text-sm ${turn.role === 'user' ? 'ml-8 bg-[#E1F3EC]' : 'mr-8 bg-[#F1F4F1]'}`}>
                                <span className="mb-1 block text-xs font-semibold">{turn.role === 'user' ? 'You' : 'Assistant'}</span>{turn.content}
                            </div>
                        ))}
                        {sending && <p className="text-xs text-[#62716B]">Thinking…</p>}
                        <div ref={bottom} />
                    </div>
                    {error && <p role="alert" className="px-4 pb-2 text-xs text-red-700">{error}</p>}
                    <form onSubmit={submit} className="flex items-end gap-2 border-t p-3">
                        <label className="sr-only" htmlFor="workspace-chat-question">Ask a question</label>
                        <textarea ref={input} id="workspace-chat-question" value={question} onChange={(event) => setQuestion(event.target.value)} disabled={sending} maxLength={2000} rows={2} placeholder="Ask a question…" className="min-w-0 flex-1 resize-none rounded-lg border border-[#BED2C7] p-2 text-sm" />
                        <button type="submit" disabled={sending || !question.trim()} aria-label="Send question" className="rounded-lg bg-[#0C6F58] p-3 text-white disabled:opacity-50"><Send size={16} /></button>
                    </form>
                </section>
            )}
            <button type="button" aria-label={open ? 'Hide workspace chat' : 'Open workspace chat'} aria-expanded={open} onClick={() => setOpen((value) => !value)} className="ml-auto flex h-14 w-14 items-center justify-center rounded-full bg-[#073F32] text-white shadow-xl hover:bg-[#0C6F58] focus-ring">
                <MessageCircle size={21} />
            </button>
        </div>
    );
}

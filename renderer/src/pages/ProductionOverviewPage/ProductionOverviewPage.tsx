import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
    Activity,
    AlertTriangle,
    ArrowRight,
    Bot,
    CheckCircle2,
    ChevronRight,
    CircleAlert,
    Clock3,
    Database,
    Inbox,
    MessageCircle,
    RefreshCw,
    ShieldCheck,
    Sparkles,
    TriangleAlert,
    Workflow,
} from 'lucide-react';
import {
    Area,
    AreaChart,
    CartesianGrid,
    ResponsiveContainer,
    Tooltip as ChartTooltip,
    XAxis,
    YAxis,
} from 'recharts';
import { authFetch } from '../../contexts/AuthGateContext';
import { Surface } from '../../design-system/components/Surface';

type AttentionItem = {
    id: string;
    kind: string;
    score: number;
    incident_id?: string | null;
    title: string;
    detail: string;
    reasons: string[];
    severity?: string | null;
    status?: string | null;
    action: string;
    to: string;
    observed_at?: string | null;
};

type CoverageNeed = {
    key: string;
    title: string;
    detail: string;
    recommendation: string;
    affected_count: number;
    incident_ids: string[];
    priority: 'high' | 'medium';
};

type OperationsSnapshot = {
    generated_at: string;
    summary: {
        open_incidents: number;
        high_priority_incidents: number;
        high_priority_alerts: number;
        verified_recoveries_30d: number;
        median_triage_seconds: number | null;
        alerts_last_24h: number;
    };
    commander_brief: { headline: string; summary: string };
    needs_attention: AttentionItem[];
    coverage: {
        score: number | null;
        dimensions: Record<string, number | null>;
        active_incidents_assessed: number;
        needs: CoverageNeed[];
    };
    shift_brief: {
        window_hours: number;
        incidents_opened: number;
        incidents_resolved: number;
        alerts_received: number;
        high_priority_alerts: number;
        verified_recoveries: number;
        attention_items: number;
    };
    agents: Array<{
        name: string;
        status: 'active' | 'recent' | 'standing_by';
        detail: string;
        last_event_at?: string | null;
        incident_id?: string | null;
        events_24h: number;
    }>;
    activity_series: Array<{ date: string; alerts: number; incidents: number; resolved: number }>;
    recent_investigations: Array<{
        incident_id: string;
        title: string;
        status: string;
        severity: string;
        rca_confidence?: number | null;
        evidence_count: number;
        impact_count: number;
        plan_status?: string | null;
        updated_at?: string | null;
    }>;
    live_events: Array<{
        id: string;
        incident_id?: string | null;
        actor: string;
        event_type: string;
        summary: string;
        created_at: string;
    }>;
    system_health: Array<{
        key: string;
        label: string;
        state: 'healthy' | 'warning' | 'critical' | 'unknown';
        detail: string;
    }>;
    outlook_bridge?: {
        last_scan_at?: string | null;
        pending: number;
        blocked: number;
        baseline: number;
    } | null;
};

function relativeTime(value?: string | null) {
    if (!value) return 'not recorded';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return 'not recorded';
    const seconds = Math.max(0, Math.round((Date.now() - parsed.getTime()) / 1000));
    if (seconds < 45) return 'just now';
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
    return `${Math.round(seconds / 86400)}d ago`;
}

function formatDuration(seconds: number | null) {
    if (seconds == null) return '—';
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function severityTone(value?: string | null) {
    const normalized = String(value || '').toUpperCase().replace('_', '-');
    if (normalized === 'CRITICAL' || normalized === 'SEV-1') return 'critical';
    if (normalized === 'HIGH' || normalized === 'SEV-2') return 'warning';
    return 'neutral';
}

function toneClasses(tone: 'critical' | 'warning' | 'healthy' | 'info' | 'neutral') {
    return {
        critical: 'bg-[#FDEBEC] text-[#C7424D] border-[#DE565E]/20',
        warning: 'bg-[#FFF1DE] text-[#A85D12] border-[#E5A52B]/20',
        healthy: 'bg-[#E1F3EC] text-[#0C6F58] border-[#18A566]/20',
        info: 'bg-[#E8F0FF] text-[#2D64B6] border-[#3777D6]/20',
        neutral: 'bg-[#EDF3EE] text-[#62716B] border-[#0F3F32]/10',
    }[tone];
}

function MetricCard({ title, value, detail, icon, tone = 'healthy', to }: {
    title: string;
    value: string | number;
    detail: string;
    icon: ReactNode;
    tone?: 'critical' | 'warning' | 'healthy' | 'info' | 'neutral';
    to: string;
}) {
    return (
        <Link to={to} className="v22-card v22-interactive focus-ring flex min-h-[132px] flex-col justify-between p-4 sm:p-5">
            <div className="flex items-start justify-between gap-3">
                <p className="text-[12px] font-semibold text-[#62716B]">{title}</p>
                <span className={`flex h-9 w-9 items-center justify-center rounded-xl border ${toneClasses(tone)}`}>{icon}</span>
            </div>
            <div className="mt-3">
                <p className="text-[32px] font-bold leading-none tracking-[-0.05em] text-[#13231E] tabular-nums">{value}</p>
                <p className="mt-2 line-clamp-2 text-[11.5px] leading-relaxed text-[#62716B]">{detail}</p>
            </div>
        </Link>
    );
}

function HealthRow({ item }: { item: OperationsSnapshot['system_health'][number] }) {
    const tone = item.state === 'critical' ? 'critical' : item.state === 'warning' ? 'warning' : item.state === 'healthy' ? 'healthy' : 'neutral';
    const dot = item.state === 'critical' ? 'bg-[#DE565E]' : item.state === 'warning' ? 'bg-[#E5A52B]' : item.state === 'healthy' ? 'bg-[#18A566]' : 'bg-[#8C9994]';
    const Icon = item.key === 'database' ? Database : item.key === 'workflow' ? Workflow : item.key === 'outlook' ? Inbox : ShieldCheck;
    return (
        <div className="flex items-center gap-3 border-b border-[#0F3F32]/[0.07] py-3.5 last:border-b-0">
            <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border ${toneClasses(tone)}`}><Icon size={16} /></span>
            <div className="min-w-0 flex-1">
                <div className="text-[13px] font-bold text-[#13231E]">{item.label}</div>
                <div className="mt-0.5 truncate text-[11.5px] text-[#62716B]">{item.detail}</div>
            </div>
            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} title={item.state} />
        </div>
    );
}

function ActionQueueItem({ item }: { item: AttentionItem }) {
    const tone = item.kind === 'verification_failed' || item.score >= 95
        ? 'critical'
        : item.score >= 80
            ? 'warning'
            : 'neutral';
    return (
        <Link to={item.to} className="group flex items-start gap-3 rounded-2xl border border-[#0F3F32]/[0.08] bg-[#F7F9F6] p-3.5 transition-all hover:border-[#0C6F58]/25 hover:bg-white focus-ring">
            <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border ${toneClasses(tone)}`}>
                {tone === 'critical' ? <TriangleAlert size={15} /> : tone === 'warning' ? <AlertTriangle size={15} /> : <CircleAlert size={15} />}
            </span>
            <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-[12.5px] font-bold text-[#13231E]">{item.title}</p>
                    <span className="text-[10px] font-bold tabular-nums text-[#8C9994]">{item.score}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-[#62716B]">{item.detail}</p>
                <div className="mt-2 flex items-center justify-between gap-3">
                    <span className="truncate text-[10px] font-semibold text-[#8C9994]">{item.incident_id || 'Platform'}</span>
                    <span className="inline-flex shrink-0 items-center gap-1 text-[10.5px] font-bold text-[#0C6F58]">{item.action}<ChevronRight size={12} /></span>
                </div>
            </div>
        </Link>
    );
}

function AgentRow({ agent }: { agent: OperationsSnapshot['agents'][number] }) {
    const dot = agent.status === 'active' ? 'bg-[#18A566]' : agent.status === 'recent' ? 'bg-[#3777D6]' : 'bg-[#8C9994]';
    const label = agent.status === 'standing_by' ? 'Standing by' : agent.status === 'active' ? 'Active' : 'Recent';
    return (
        <Link to={`/app/agent-operations?agent=${encodeURIComponent(agent.name.toLowerCase())}`} className="group flex min-w-0 items-center gap-3 rounded-2xl border border-[#0F3F32]/[0.07] bg-[#F7F9F6] px-3.5 py-3 transition-all hover:border-[#0C6F58]/20 hover:bg-white focus-ring">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />
            <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[12.5px] font-bold text-[#13231E]">{agent.name}</span>
                    <span className="shrink-0 text-[10px] font-semibold tabular-nums text-[#8C9994]">{agent.events_24h} events</span>
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[10.5px] text-[#62716B]">
                    <span>{label}</span>
                    <span className="text-[#B3BDB8]">•</span>
                    <span className="truncate">{agent.detail}</span>
                </div>
            </div>
        </Link>
    );
}

export function ProductionOverviewPage() {
    const [snapshot, setSnapshot] = useState<OperationsSnapshot | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

    useEffect(() => {
        let alive = true;
        const load = async () => {
            try {
                const res = await authFetch('/api/v2/operations/snapshot');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const body = await res.json();
                if (alive) {
                    setSnapshot(body);
                    setError(null);
                    setUpdatedAt(new Date());
                }
            } catch (e) {
                if (alive) setError(e instanceof Error ? e.message : 'Unable to refresh operations snapshot');
            } finally {
                if (alive) setLoading(false);
            }
        };
        void load();
        const timer = window.setInterval(() => void load(), 15000);
        return () => {
            alive = false;
            window.clearInterval(timer);
        };
    }, []);

    const chartData = useMemo(() => snapshot?.activity_series.map((point) => ({
        ...point,
        label: new Date(`${point.date}T00:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    })) ?? [], [snapshot]);

    if (!snapshot && loading) {
        return (
            <main className="v22-canvas h-full overflow-y-auto px-4 py-5 sm:px-6 lg:px-7">
                <div className="mx-auto max-w-[1540px] animate-pulse space-y-4">
                    <div className="h-12 w-80 rounded-2xl bg-[#DDE6E0]" />
                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
                        <div className="h-64 rounded-[24px] bg-[#DDE6E0] xl:col-span-8" />
                        <div className="h-64 rounded-[24px] bg-[#E5EBE7] xl:col-span-4" />
                    </div>
                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
                        <div className="h-72 rounded-[24px] bg-[#E5EBE7] xl:col-span-4" />
                        <div className="grid grid-cols-2 gap-4 xl:col-span-8 xl:grid-cols-4">{[0, 1, 2, 3].map((n) => <div key={n} className="h-36 rounded-[24px] bg-[#E5EBE7]" />)}</div>
                    </div>
                </div>
            </main>
        );
    }

    if (!snapshot) {
        return (
            <main className="v22-canvas flex h-full items-center justify-center p-8">
                <div className="max-w-md text-center">
                    <TriangleAlert className="mx-auto text-[#DE565E]" size={28} />
                    <h1 className="mt-4 text-xl font-bold text-[#13231E]">Operations snapshot unavailable</h1>
                    <p className="mt-2 text-[13px] text-[#62716B]">{error || 'NemoGuard could not load the operational projection.'}</p>
                </div>
            </main>
        );
    }

    const { summary, coverage, shift_brief: shift, commander_brief: commander } = snapshot;
    const stale = Boolean(error && updatedAt);
    const activeAgents = snapshot.agents.filter((agent) => agent.status === 'active').length;

    return (
        <main className="v22-canvas h-full overflow-y-auto px-4 py-5 sm:px-6 lg:px-7">
            <div className="v22-content-enter mx-auto max-w-[1540px] space-y-4 pb-20">
                <header className="flex flex-wrap items-center justify-between gap-4 px-0.5">
                    <div>
                        <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#0C6F58]">
                            <span className={`h-2 w-2 rounded-full ${stale ? 'bg-[#E5A52B]' : 'bg-[#18A566]'}`} />
                            Production overview
                        </div>
                        <h1 className="mt-1.5 text-[28px] font-bold tracking-[-0.045em] text-[#13231E] sm:text-[31px]">Operational command view</h1>
                    </div>
                    <div className="flex items-center gap-2 rounded-2xl border border-[#0F3F32]/10 bg-white/70 px-3.5 py-2.5 backdrop-blur-sm">
                        <RefreshCw size={13} className={stale ? 'text-[#E5A52B]' : 'text-[#0C6F58]'} />
                        <div>
                            <div className="text-[11.5px] font-bold text-[#13231E]">{stale ? 'Showing last known state' : 'Live operational snapshot'}</div>
                            <div className="mt-0.5 text-[10.5px] text-[#8C9994]">{updatedAt ? `Updated ${relativeTime(updatedAt.toISOString())}` : relativeTime(snapshot.generated_at)}</div>
                        </div>
                    </div>
                </header>

                {stale && (
                    <div className="rounded-2xl border border-[#E5A52B]/25 bg-[#FFF1DE] px-4 py-3 text-[12px] text-[#8E551C]">
                        Refresh failed ({error}). NemoGuard is preserving the last successful snapshot.
                    </div>
                )}

                <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
                    <Surface className="relative overflow-hidden bg-[#073F32] p-5 text-white sm:p-6 xl:col-span-8">
                        <div className="absolute -right-16 -top-20 h-64 w-64 rounded-full bg-[#8FE4C9]/15 blur-3xl" />
                        <div className="absolute -bottom-28 left-1/3 h-56 w-56 rounded-full bg-[#15876D]/20 blur-3xl" />
                        <div className="relative flex min-h-[228px] flex-col justify-between gap-5">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                <div className="flex items-center gap-2.5">
                                    <span className="flex h-10 w-10 items-center justify-center rounded-2xl border border-white/10 bg-white/10 text-[#8FE4C9]"><Sparkles size={18} /></span>
                                    <div>
                                        <p className="text-[13px] font-bold">Commander Brief</p>
                                        <p className="mt-0.5 text-[10.5px] text-white/55">Deterministic summary of persisted production state</p>
                                    </div>
                                </div>
                                <div className="flex items-center gap-2">
                                    <span className="rounded-full border border-white/10 bg-white/[0.08] px-3 py-1.5 text-[10.5px] font-semibold text-white/75">Coverage {coverage.score == null ? '—' : `${coverage.score}%`}</span>
                                    {coverage.needs.length > 0 && <span className="rounded-full bg-[#FFF1DE] px-3 py-1.5 text-[10.5px] font-bold text-[#8E551C]">{coverage.needs.length} gap{coverage.needs.length === 1 ? '' : 's'}</span>}
                                </div>
                            </div>

                            <div>
                                <h2 className="max-w-4xl text-[25px] font-bold leading-[1.15] tracking-[-0.04em] sm:text-[29px]">{commander.headline}</h2>
                                <p className="mt-2.5 max-w-4xl text-[13px] leading-relaxed text-white/68">{commander.summary}</p>
                            </div>

                            <div className="flex flex-wrap items-end justify-between gap-4">
                                <div className="grid flex-1 grid-cols-2 gap-2 sm:grid-cols-4">
                                    {[
                                        ['Opened', shift.incidents_opened],
                                        ['Resolved', shift.incidents_resolved],
                                        ['Alerts', shift.alerts_received],
                                        ['Verified', shift.verified_recoveries],
                                    ].map(([label, value]) => (
                                        <div key={String(label)} className="rounded-xl border border-white/10 bg-white/[0.07] px-3 py-2">
                                            <div className="text-[16px] font-bold tabular-nums">{value}</div>
                                            <div className="mt-0.5 text-[9.5px] font-medium uppercase tracking-[0.08em] text-white/45">{label} · {shift.window_hours}h</div>
                                        </div>
                                    ))}
                                </div>
                                <Link to="/app/command-center" className="inline-flex h-10 items-center gap-2 rounded-xl bg-[#FF9C55] px-4 text-[12px] font-bold text-[#13231E] transition-transform hover:-translate-y-0.5 active:scale-[0.985]">Open Command Center<ArrowRight size={14} /></Link>
                            </div>
                        </div>
                    </Surface>

                    <Surface className="flex min-h-[228px] flex-col p-5 sm:p-6 xl:col-span-4">
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <p className="text-[17px] font-bold text-[#13231E]">Action Queue</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Only items that currently need an operator or approver.</p>
                            </div>
                            <span className={`rounded-full px-3 py-1.5 text-[10.5px] font-bold ${snapshot.needs_attention.length ? 'bg-[#FFF1DE] text-[#A85D12]' : 'bg-[#E1F3EC] text-[#0C6F58]'}`}>{snapshot.needs_attention.length}</span>
                        </div>
                        <div className="mt-4 flex-1 space-y-2.5">
                            {snapshot.needs_attention.length === 0 ? (
                                <div className="flex h-full min-h-[145px] items-center justify-center rounded-2xl border border-[#18A566]/15 bg-[#E1F3EC] p-5 text-center text-[12px] leading-relaxed text-[#0C6F58]">
                                    No operator action is currently derived from persisted incident, verification, SLA, or health state.
                                </div>
                            ) : snapshot.needs_attention.slice(0, 3).map((item) => <ActionQueueItem key={item.id} item={item} />)}
                        </div>
                        {snapshot.needs_attention.length > 3 && <Link to="/app/incidents?state=open" className="mt-3 inline-flex items-center gap-1 text-[11.5px] font-bold text-[#0C6F58]">View all attention items<ChevronRight size={13} /></Link>}
                    </Surface>
                </div>

                <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
                    <Surface className="p-5 sm:p-6 xl:col-span-4">
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <p className="text-[17px] font-bold text-[#13231E]">Agent Fleet</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">{activeAgents} active now · activity is projected from real audit events.</p>
                            </div>
                            <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#0C6F58]/15 bg-[#E1F3EC] text-[#0C6F58]"><Bot size={17} /></span>
                        </div>
                        <div className="mt-4 grid gap-2.5 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                            {snapshot.agents.slice(0, 6).map((agent) => <AgentRow key={agent.name} agent={agent} />)}
                        </div>
                    </Surface>

                    <div className="grid grid-cols-2 gap-4 xl:col-span-8 xl:grid-cols-4">
                        <MetricCard title="Open incidents" value={summary.open_incidents} detail={`${summary.high_priority_incidents} high priority in live operational state`} icon={<CircleAlert size={17} />} tone={summary.high_priority_incidents ? 'warning' : 'healthy'} to="/app/incidents?state=open" />
                        <MetricCard title="High-priority alerts" value={summary.high_priority_alerts} detail={`${summary.alerts_last_24h} alerts received in the last 24 hours`} icon={<AlertTriangle size={17} />} tone={summary.high_priority_alerts ? 'critical' : 'healthy'} to="/app/alert-inbox" />
                        <MetricCard title="Verified recoveries" value={summary.verified_recoveries_30d} detail="Independent verification passed in the last 30 days" icon={<CheckCircle2 size={17} />} tone="info" to="/app/intelligence" />
                        <MetricCard title="Median triage" value={formatDuration(summary.median_triage_seconds)} detail="Detection to first persisted investigation or plan milestone" icon={<Clock3 size={17} />} tone="neutral" to="/app/intelligence" />
                    </div>
                </div>

                <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
                    <Surface className="p-5 sm:p-6 xl:col-span-8">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[17px] font-bold text-[#13231E]">Incident & Alert Activity</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Persisted production activity only; no simulated telemetry.</p>
                            </div>
                            <Activity size={18} className="text-[#0C6F58]" />
                        </div>
                        <div className="mt-4 h-[248px] w-full">
                            <ResponsiveContainer width="100%" height="100%">
                                <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
                                    <defs>
                                        <linearGradient id="alertsFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#0C6F58" stopOpacity={0.24} /><stop offset="100%" stopColor="#0C6F58" stopOpacity={0.01} /></linearGradient>
                                        <linearGradient id="incidentsFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#3777D6" stopOpacity={0.16} /><stop offset="100%" stopColor="#3777D6" stopOpacity={0.01} /></linearGradient>
                                    </defs>
                                    <CartesianGrid stroke="rgba(15,63,50,.08)" vertical={false} />
                                    <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: '#8C9994', fontSize: 10.5 }} minTickGap={24} />
                                    <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: '#8C9994', fontSize: 10.5 }} />
                                    <ChartTooltip contentStyle={{ borderRadius: 14, border: '1px solid rgba(15,63,50,.12)', background: '#FCFDFB', boxShadow: '0 18px 40px -28px rgba(7,63,50,.45)', fontSize: 11.5 }} />
                                    <Area type="monotone" dataKey="alerts" name="Alerts" stroke="#0C6F58" strokeWidth={2.1} fill="url(#alertsFill)" activeDot={{ r: 4 }} />
                                    <Area type="monotone" dataKey="incidents" name="Incidents" stroke="#3777D6" strokeWidth={2} fill="url(#incidentsFill)" activeDot={{ r: 4 }} />
                                    <Area type="monotone" dataKey="resolved" name="Resolved" stroke="#18A566" strokeWidth={1.6} fillOpacity={0} strokeDasharray="4 4" />
                                </AreaChart>
                            </ResponsiveContainer>
                        </div>
                    </Surface>

                    <Surface className="p-5 sm:p-6 xl:col-span-4">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[17px] font-bold text-[#13231E]">System Health</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Probe-backed platform and integration state.</p>
                            </div>
                            <ShieldCheck size={18} className="text-[#0C6F58]" />
                        </div>
                        <div className="mt-3">{snapshot.system_health.map((item) => <HealthRow key={item.key} item={item} />)}</div>
                        {snapshot.outlook_bridge && (
                            <Link to="/app/alert-inbox" className="mt-3 flex items-center justify-between rounded-2xl bg-[#F3F6F2] px-4 py-3 text-[11.5px] text-[#62716B] focus-ring">
                                <span>Alert intake pending</span>
                                <span className="font-bold tabular-nums text-[#13231E]">{snapshot.outlook_bridge.pending}</span>
                            </Link>
                        )}
                    </Surface>
                </div>

                <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
                    <Surface className="p-5 sm:p-6 xl:col-span-6">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[17px] font-bold text-[#13231E]">Recent Investigations</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Latest incident state with evidence and impact coverage.</p>
                            </div>
                            <Link to="/app/incidents" className="text-[11px] font-bold text-[#0C6F58]">View all</Link>
                        </div>
                        <div className="mt-3 space-y-1">
                            {snapshot.recent_investigations.slice(0, 5).map((incident) => (
                                <Link key={incident.incident_id} to={`/app/command-center?incident=${encodeURIComponent(incident.incident_id)}`} className="group flex items-center gap-3 rounded-2xl px-2.5 py-3 transition-colors hover:bg-[#F3F6F2] focus-ring">
                                    <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${severityTone(incident.severity) === 'critical' ? 'bg-[#DE565E]' : severityTone(incident.severity) === 'warning' ? 'bg-[#E5A52B]' : 'bg-[#8C9994]'}`} />
                                    <div className="min-w-0 flex-1">
                                        <p className="truncate text-[12.5px] font-bold text-[#13231E]">{incident.title || incident.incident_id}</p>
                                        <p className="mt-0.5 text-[10.5px] text-[#8C9994]">{incident.incident_id} · {incident.status?.replace(/_/g, ' ')}</p>
                                    </div>
                                    <div className="hidden text-right text-[10.5px] text-[#62716B] sm:block">
                                        <div>{incident.evidence_count} evidence</div>
                                        <div>{incident.impact_count} impact</div>
                                    </div>
                                    <ChevronRight size={14} className="shrink-0 text-[#B3BDB8] transition-transform group-hover:translate-x-0.5" />
                                </Link>
                            ))}
                            {snapshot.recent_investigations.length === 0 && <p className="rounded-2xl bg-[#F3F6F2] p-4 text-[12px] text-[#62716B]">No incidents have been recorded yet.</p>}
                        </div>
                    </Surface>

                    <Surface className="p-5 sm:p-6 xl:col-span-6">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[17px] font-bold text-[#13231E]">Live Operations</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Latest persisted audit activity across production incidents.</p>
                            </div>
                            <Activity size={17} className="text-[#0C6F58]" />
                        </div>
                        <div className="mt-4 space-y-3">
                            {snapshot.live_events.slice(0, 5).map((event) => (
                                <div key={event.id} className="flex gap-3">
                                    <div className="flex flex-col items-center">
                                        <span className="mt-1.5 h-2.5 w-2.5 rounded-full bg-[#0C6F58]" />
                                        <span className="mt-1 h-full w-px bg-[#DDE6E0]" />
                                    </div>
                                    <div className="min-w-0 pb-2">
                                        <p className="text-[11.5px] font-bold text-[#13231E]">{event.actor} · {event.event_type.replace(/_/g, ' ')}</p>
                                        <p className="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-[#62716B]">{event.summary}</p>
                                        <p className="mt-1 text-[10px] text-[#8C9994]">{event.incident_id || 'system'} · {relativeTime(event.created_at)}</p>
                                    </div>
                                </div>
                            ))}
                            {snapshot.live_events.length === 0 && <p className="rounded-2xl bg-[#F3F6F2] p-4 text-[12px] text-[#62716B]">No audit events have been recorded yet.</p>}
                        </div>
                    </Surface>
                </div>
            </div>

            <Link
                to="/app/command-center"
                aria-label="Open Commander"
                className="fixed bottom-5 right-5 z-30 flex h-12 items-center gap-2 rounded-full bg-[#073F32] px-4 text-white shadow-[0_18px_40px_-16px_rgba(7,63,50,.75)] transition-transform hover:-translate-y-1 active:scale-[0.96] focus-ring"
            >
                <MessageCircle size={18} />
                <span className="hidden text-[12px] font-bold sm:inline">Commander</span>
            </Link>
        </main>
    );
}

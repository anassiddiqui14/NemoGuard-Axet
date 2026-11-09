import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
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
    TriangleAlert,
    Workflow,
    Wrench,
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

function CommanderSignalIcon() {
    return (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className="h-6 w-6 motion-safe:animate-[v22ContentEnter_400ms_ease-out_both]">
            <circle cx="12" cy="12" r="2.5" fill="currentColor" />
            <path d="M5.5 5.5a9.2 9.2 0 0 1 13 0M5.5 18.5a9.2 9.2 0 0 0 13 0M8 8a5.7 5.7 0 0 1 8 0M8 16a5.7 5.7 0 0 0 8 0" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
    );
}

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
    to?: string;
}) {
    const body = (
        <>
            <div className="flex items-start justify-between gap-3">
                <p className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[#62716B]">{title}</p>
                <span className={`flex h-10 w-10 items-center justify-center rounded-2xl border ${toneClasses(tone)}`}>{icon}</span>
            </div>
            <p className="mt-5 text-[34px] font-bold leading-none tracking-[-0.055em] text-[#13231E] tabular-nums">{value}</p>
            <p className="mt-3 text-[12px] leading-relaxed text-[#62716B]">{detail}</p>
        </>
    );
    if (!to) return <Surface className="min-h-[172px] p-5">{body}</Surface>;
    return (
        <Link to={to} className="v22-card v22-interactive block min-h-[172px] p-5 focus-ring">
            {body}
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
                <div className="mt-0.5 truncate text-[11px] text-[#62716B]">{item.detail}</div>
            </div>
            <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} title={item.state} />
        </div>
    );
}

function AttentionCard({ item }: { item: AttentionItem }) {
    const tone = item.kind === 'verification_failed' || item.score >= 95
        ? 'critical'
        : item.score >= 80
            ? 'warning'
            : 'neutral';
    return (
        <Link to={item.to} className="group block rounded-[20px] border border-[#0F3F32]/10 bg-[#F7F9F6] p-4 transition-all duration-150 hover:-translate-y-0.5 hover:border-[#0C6F58]/25 hover:bg-white hover:shadow-[0_18px_34px_-28px_rgba(7,63,50,.5)] focus-ring">
            <div className="flex items-start gap-3">
                <span className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border ${toneClasses(tone)}`}>
                    {tone === 'critical' ? <TriangleAlert size={16} /> : tone === 'warning' ? <AlertTriangle size={16} /> : <CircleAlert size={16} />}
                </span>
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="truncate text-[13px] font-bold text-[#13231E]">{item.title}</p>
                        <span className="rounded-full bg-[#13231E] px-2 py-1 text-[9px] font-bold text-white tabular-nums">ATTN {item.score}</span>
                    </div>
                    <p className="mt-1.5 text-[11.5px] leading-relaxed text-[#62716B]">{item.detail}</p>
                    <div className="mt-3 flex items-center justify-between gap-3">
                        <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-[#8C9994]">{item.incident_id || 'Platform'}</span>
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold text-[#0C6F58]">{item.action}<ChevronRight size={13} className="transition-transform group-hover:translate-x-0.5" /></span>
                    </div>
                </div>
            </div>
        </Link>
    );
}

function CoverageBar({ label, value }: { label: string; value: number | null }) {
    const width = value == null ? 0 : Math.max(2, value);
    return (
        <div>
            <div className="mb-1.5 flex items-center justify-between gap-3 text-[10.5px]">
                <span className="capitalize text-[#62716B]">{label.replace(/_/g, ' ')}</span>
                <span className="font-bold tabular-nums text-[#13231E]">{value == null ? '—' : `${value}%`}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-[#DDE6E0]">
                <motion.div initial={{ width: 0 }} animate={{ width: `${width}%` }} transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }} className="h-full rounded-full bg-[#0C6F58]" />
            </div>
        </div>
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
            <main className="v22-overview v22-canvas h-full overflow-y-auto px-4 py-5 sm:px-7 sm:py-7" aria-busy="true">
                <div className="mx-auto max-w-[1600px]">
                    <p role="status" className="mb-4 flex items-center gap-2 text-sm font-medium text-[#62716B]">
                        <span aria-hidden="true" className="h-2 w-2 rounded-full bg-[#0C6F58] motion-safe:animate-pulse" />
                        Loading the operations snapshot…
                    </p>
                </div>
                <div aria-hidden="true" className="mx-auto max-w-[1600px] motion-safe:animate-pulse space-y-5">
                    <div className="h-12 w-72 rounded-2xl bg-[#DDE6E0]" />
                    <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
                        <div className="h-72 rounded-[24px] bg-[#DDE6E0] xl:col-span-8" />
                        <div className="h-72 rounded-[24px] bg-[#E5EBE7] xl:col-span-4" />
                    </div>
                    <div className="grid grid-cols-2 gap-5 xl:grid-cols-4">{[0, 1, 2, 3].map((n) => <div key={n} className="h-44 rounded-[24px] bg-[#E5EBE7]" />)}</div>
                </div>
            </main>
        );
    }

    if (!snapshot) {
        return (
            <main className="v22-overview v22-canvas flex h-full items-center justify-center p-8">
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

    return (
        <main className="v22-overview v22-canvas h-full overflow-y-auto px-4 py-5 sm:px-7 sm:py-7">
            <div className="v22-content-enter mx-auto max-w-[1600px] space-y-5 pb-24">
                <header className="flex flex-wrap items-end justify-between gap-4">
                    <div>
                        <div className="flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-[#0C6F58]">
                            <span className={`h-2 w-2 rounded-full ${stale ? 'bg-[#E5A52B]' : 'bg-[#18A566]'}`} />
                            Production
                        </div>
                        <h1 className="mt-2 text-[30px] font-bold tracking-[-0.05em] text-[#13231E] sm:text-[34px]">Production overview</h1>
                        <p className="mt-1 max-w-2xl text-[12.5px] text-[#62716B]">Incidents, alerts and investigation coverage.</p>
                    </div>
                    <div className="rounded-2xl border border-[#0F3F32]/10 bg-white/60 px-4 py-2.5 text-right backdrop-blur-sm">
                        <div className="flex items-center justify-end gap-1.5 text-[11px] font-bold text-[#13231E]"><RefreshCw size={12} />{stale ? 'Showing last known state' : 'Live snapshot'}</div>
                        <div className="mt-0.5 text-[10.5px] text-[#62716B]">{updatedAt ? `Updated ${relativeTime(updatedAt.toISOString())}` : relativeTime(snapshot.generated_at)}</div>
                    </div>
                </header>

                {stale && (
                    <div className="rounded-2xl border border-[#E5A52B]/25 bg-[#FFF1DE] px-4 py-3 text-[12px] text-[#8E551C]">
                        Refresh failed ({error}). Showing the last successful snapshot.
                    </div>
                )}

                <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
                    <Surface variant="brand" className="relative overflow-hidden p-6 text-white xl:col-span-8 sm:p-7">
                        <div className="absolute -right-20 -top-24 h-72 w-72 rounded-full bg-[#8FE4C9]/15 blur-3xl" />
                        <div className="absolute -bottom-32 left-1/3 h-64 w-64 rounded-full bg-[#15876D]/20 blur-3xl" />
                        <div className="relative flex min-h-[250px] flex-col justify-between gap-8">
                            <div className="flex items-center justify-between gap-4">
                                <span className="flex h-11 w-11 items-center justify-center rounded-2xl border border-white/10 bg-white/10 text-[#8FE4C9]"><CommanderSignalIcon /></span>
                                <span className="rounded-full border border-white/10 bg-white/[0.08] px-3 py-1.5 text-[10.5px] font-semibold text-white/70">Incident summary</span>
                            </div>
                            <div>
                                <h2 className="max-w-3xl text-[28px] font-bold leading-[1.12] tracking-[-0.045em] sm:text-[32px]">{commander.headline}</h2>
                                <p className="mt-3 max-w-3xl text-[13.5px] leading-relaxed text-white/68">{commander.summary}</p>
                            </div>
                            <div className="flex flex-wrap gap-3">
                                <Link to="/app/command-center" className="inline-flex items-center gap-2 rounded-xl bg-[#FF9C55] px-4 py-2.5 text-[12.5px] font-bold text-[#13231E] transition-transform hover:-translate-y-0.5 active:scale-[0.985]">Open Command Center<ArrowRight size={15} /></Link>
                                <Link to="/app/incidents?state=open" className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/[0.07] px-4 py-2.5 text-[12.5px] font-semibold text-white transition-colors hover:bg-white/[0.12]">Review incidents<ChevronRight size={15} /></Link>
                            </div>
                        </div>
                    </Surface>

                    <Surface className="p-5 xl:col-span-4 sm:p-6">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[15px] font-bold text-[#13231E]">Shift Brief</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Last {shift.window_hours} hours</p>
                            </div>
                            <span className="flex h-10 w-10 items-center justify-center rounded-2xl border border-[#0C6F58]/15 bg-[#E1F3EC] text-[#0C6F58]"><Clock3 size={18} /></span>
                        </div>
                        <div className="mt-5 grid grid-cols-2 gap-2.5">
                            {[
                                ['Incidents opened', shift.incidents_opened],
                                ['Resolved', shift.incidents_resolved],
                                ['Alerts received', shift.alerts_received],
                                ['Verified recoveries', shift.verified_recoveries],
                            ].map(([label, value]) => (
                                <div key={String(label)} className="rounded-2xl border border-[#0F3F32]/[0.08] bg-[#F7F9F6] p-3.5">
                                    <div className="text-[24px] font-bold leading-none tracking-[-0.04em] text-[#13231E] tabular-nums">{value}</div>
                                    <div className="mt-2 text-[10.5px] text-[#62716B]">{label}</div>
                                </div>
                            ))}
                        </div>
                        <Link to="/app/alert-inbox" className="mt-4 inline-flex items-center gap-1.5 text-[11.5px] font-bold text-[#0C6F58]">Open alert intake<ChevronRight size={13} /></Link>
                    </Surface>
                </div>

                <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
                    <Surface className="p-5 xl:col-span-7 sm:p-6">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[15px] font-bold text-[#13231E]">Needs Attention</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Ranked by severity, workflow state, SLA and verification outcome.</p>
                            </div>
                            <span className={`rounded-full px-3 py-1.5 text-[10px] font-bold ${snapshot.needs_attention.length ? 'bg-[#FFF1DE] text-[#A85D12]' : 'bg-[#E1F3EC] text-[#0C6F58]'}`}>{snapshot.needs_attention.length} item{snapshot.needs_attention.length === 1 ? '' : 's'}</span>
                        </div>
                        <div className="mt-5 grid gap-3 md:grid-cols-2">
                            {snapshot.needs_attention.length === 0 ? (
                                <div className="md:col-span-2 rounded-[20px] border border-[#18A566]/15 bg-[#E1F3EC] p-5 text-[12px] leading-relaxed text-[#0C6F58]">
                                    No urgent operator action is currently derived from persisted incident, verification, SLA, or platform-health state.
                                </div>
                            ) : snapshot.needs_attention.slice(0, 6).map((item) => <AttentionCard key={item.id} item={item} />)}
                        </div>
                    </Surface>

                    <Surface className="p-5 xl:col-span-5 sm:p-6">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[15px] font-bold text-[#13231E]">Investigation coverage</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Available context and missing information.</p>
                            </div>
                            <div className="text-right">
                                <div className="text-[28px] font-bold leading-none tracking-[-0.05em] text-[#13231E] tabular-nums">{coverage.score == null ? '—' : `${coverage.score}%`}</div>
                                <div className="mt-1 text-[9.5px] font-semibold uppercase tracking-[0.1em] text-[#8C9994]">coverage</div>
                            </div>
                        </div>
                        <div className="mt-5 grid grid-cols-2 gap-x-5 gap-y-3">
                            {Object.entries(coverage.dimensions).map(([key, value]) => <CoverageBar key={key} label={key} value={value} />)}
                        </div>
                        <div className="mt-5 space-y-2.5">
                            {coverage.needs.length === 0 ? (
                                <div className="rounded-2xl bg-[#E1F3EC] p-4 text-[11.5px] leading-relaxed text-[#0C6F58]">No active incident coverage gap is currently detected.</div>
                            ) : coverage.needs.slice(0, 3).map((need) => (
                                <div key={need.key} className="rounded-2xl border border-[#0F3F32]/[0.08] bg-[#F7F9F6] p-3.5">
                                    <div className="flex items-start gap-2.5">
                                        <Wrench size={14} className={`mt-0.5 shrink-0 ${need.priority === 'high' ? 'text-[#DE565E]' : 'text-[#0C6F58]'}`} />
                                        <div>
                                            <p className="text-[11.5px] font-bold text-[#13231E]">{need.title}</p>
                                            <p className="mt-1 text-[10.5px] leading-relaxed text-[#62716B]">{need.recommendation}</p>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </Surface>
                </div>

                <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
                    <MetricCard title="Open Incidents" value={summary.open_incidents} detail={`${summary.high_priority_incidents} high-priority · live operational state`} icon={<CircleAlert size={18} />} tone={summary.high_priority_incidents ? 'warning' : 'healthy'} to="/app/incidents?state=open" />
                    <MetricCard title="High-Priority Alerts" value={summary.high_priority_alerts} detail={`${summary.alerts_last_24h} total alerts received in the last 24h`} icon={<AlertTriangle size={18} />} tone={summary.high_priority_alerts ? 'critical' : 'healthy'} to="/app/alert-inbox" />
                    <MetricCard title="Verified Recoveries" value={summary.verified_recoveries_30d} detail="Distinct incidents with independent verification passed · last 30d" icon={<CheckCircle2 size={18} />} tone="info" to="/app/intelligence" />
                    <MetricCard title="Median Triage Time" value={formatDuration(summary.median_triage_seconds)} detail="Detection to first persisted investigation/plan milestone" icon={<Clock3 size={18} />} tone="neutral" to="/app/intelligence" />
                </div>

                <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
                    <Surface className="p-5 xl:col-span-8 sm:p-6">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[15px] font-bold text-[#13231E]">Incident & Alert Activity</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Recorded activity over the last 14 days.</p>
                            </div>
                            <Activity size={18} className="text-[#0C6F58]" />
                        </div>
                        <div className="mt-5 h-[270px] w-full">
                            <ResponsiveContainer width="100%" height="100%">
                                <AreaChart data={chartData} margin={{ top: 8, right: 4, bottom: 0, left: -20 }}>
                                    <defs>
                                        <linearGradient id="alertsFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#0C6F58" stopOpacity={0.24} /><stop offset="100%" stopColor="#0C6F58" stopOpacity={0.01} /></linearGradient>
                                        <linearGradient id="incidentsFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#3777D6" stopOpacity={0.18} /><stop offset="100%" stopColor="#3777D6" stopOpacity={0.01} /></linearGradient>
                                    </defs>
                                    <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                                    <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: '#8C9994', fontSize: 10 }} minTickGap={24} />
                                    <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: '#8C9994', fontSize: 10 }} />
                                    <ChartTooltip contentStyle={{ borderRadius: 16, border: '1px solid var(--chart-tooltip-border)', background: 'var(--chart-tooltip-bg)', color: 'var(--chart-tooltip-text)', boxShadow: '0 18px 40px -28px rgba(7,63,50,.45)', fontSize: 11 }} />
                                    <Area type="monotone" dataKey="alerts" name="Alerts" stroke="#0C6F58" strokeWidth={2.1} fill="url(#alertsFill)" activeDot={{ r: 4 }} />
                                    <Area type="monotone" dataKey="incidents" name="Incidents" stroke="#3777D6" strokeWidth={2} fill="url(#incidentsFill)" activeDot={{ r: 4 }} />
                                    <Area type="monotone" dataKey="resolved" name="Resolved" stroke="#18A566" strokeWidth={1.6} fillOpacity={0} strokeDasharray="4 4" />
                                </AreaChart>
                            </ResponsiveContainer>
                        </div>
                    </Surface>

                    <Surface className="p-5 xl:col-span-4 sm:p-6">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[15px] font-bold text-[#13231E]">System Health</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Service status and configuration.</p>
                            </div>
                            <ShieldCheck size={18} className="text-[#0C6F58]" />
                        </div>
                        <div className="mt-4">{snapshot.system_health.map((item) => <HealthRow key={item.key} item={item} />)}</div>
                    </Surface>
                </div>

                <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
                    <Surface className="p-5 xl:col-span-4 sm:p-6">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[15px] font-bold text-[#13231E]">Agent Activity</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Recent activity from audit events; not agent health.</p>
                            </div>
                            <Bot size={18} className="text-[#0C6F58]" />
                        </div>
                        <div className="mt-4 grid grid-cols-2 gap-2.5">
                            {snapshot.agents.map((agent) => {
                                const statusTone = agent.status === 'active' ? 'healthy' : agent.status === 'recent' ? 'info' : 'neutral';
                                return (
                                    <Link key={agent.name} to={`/app/agent-operations?agent=${agent.name.toLowerCase()}`} className="v22-interactive rounded-2xl border border-[#0F3F32]/[0.08] bg-[#F7F9F6] p-3.5 focus-ring">
                                        <div className="flex items-center gap-2">
                                            <span className={`h-2 w-2 rounded-full ${agent.status === 'active' ? 'bg-[#18A566]' : agent.status === 'recent' ? 'bg-[#3777D6]' : 'bg-[#8C9994]'}`} />
                                            <span className="text-[11.5px] font-bold text-[#13231E]">{agent.name}</span>
                                        </div>
                                        <div className={`mt-2 inline-flex rounded-full border px-2 py-0.5 text-[9px] font-bold ${toneClasses(statusTone)}`}>{agent.status.replace('_', ' ')}</div>
                                        <p className="mt-2 line-clamp-2 text-[10px] leading-relaxed text-[#62716B]">{agent.detail}</p>
                                    </Link>
                                );
                            })}
                        </div>
                    </Surface>

                    <Surface className="p-5 xl:col-span-4 sm:p-6">
                        <div>
                            <p className="text-[15px] font-bold text-[#13231E]">Recent Investigations</p>
                            <p className="mt-1 text-[11.5px] text-[#62716B]">Latest incident state with evidence and impact coverage.</p>
                        </div>
                        <div className="mt-4 space-y-1">
                            {snapshot.recent_investigations.slice(0, 5).map((incident) => (
                                <Link key={incident.incident_id} to={`/app/command-center?incident=${encodeURIComponent(incident.incident_id)}`} className="group flex items-center gap-3 rounded-2xl px-2.5 py-3 transition-colors hover:bg-[#F3F6F2] focus-ring">
                                    <span className={`h-2 w-2 shrink-0 rounded-full ${severityTone(incident.severity) === 'critical' ? 'bg-[#DE565E]' : severityTone(incident.severity) === 'warning' ? 'bg-[#E5A52B]' : 'bg-[#8C9994]'}`} />
                                    <div className="min-w-0 flex-1">
                                        <p className="truncate text-[11.5px] font-bold text-[#13231E]">{incident.title || incident.incident_id}</p>
                                        <p className="mt-0.5 text-[9.5px] text-[#8C9994]">{incident.incident_id} · {incident.status?.replace(/_/g, ' ')}</p>
                                    </div>
                                    <div className="text-right text-[9.5px] text-[#62716B]">
                                        <div>{incident.evidence_count} evidence</div>
                                        <div>{incident.impact_count} impact</div>
                                    </div>
                                </Link>
                            ))}
                            {snapshot.recent_investigations.length === 0 && <p className="rounded-2xl bg-[#F3F6F2] p-4 text-[11px] text-[#62716B]">No incidents have been recorded yet.</p>}
                        </div>
                    </Surface>

                    <Surface className="p-5 xl:col-span-4 sm:p-6">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <p className="text-[15px] font-bold text-[#13231E]">Live Operations</p>
                                <p className="mt-1 text-[11.5px] text-[#62716B]">Latest persisted audit activity across incidents.</p>
                            </div>
                            <Activity size={17} className="text-[#0C6F58]" />
                        </div>
                        <div className="mt-4 space-y-3">
                            {snapshot.live_events.slice(0, 6).map((event) => (
                                <div key={event.id} className="flex gap-3">
                                    <div className="flex flex-col items-center">
                                        <span className="mt-1.5 h-2 w-2 rounded-full bg-[#0C6F58]" />
                                        <span className="mt-1 h-full w-px bg-[#DDE6E0] last:hidden" />
                                    </div>
                                    <div className="min-w-0 pb-2">
                                        <p className="text-[10.5px] font-bold text-[#13231E]">{event.actor} · {event.event_type.replace(/_/g, ' ')}</p>
                                        <p className="mt-1 line-clamp-2 text-[10.5px] leading-relaxed text-[#62716B]">{event.summary}</p>
                                        <p className="mt-1 text-[9.5px] text-[#8C9994]">{event.incident_id || 'system'} · {relativeTime(event.created_at)}</p>
                                    </div>
                                </div>
                            ))}
                            {snapshot.live_events.length === 0 && <p className="rounded-2xl bg-[#F3F6F2] p-4 text-[11px] text-[#62716B]">No audit events have been recorded yet.</p>}
                        </div>
                    </Surface>
                </div>

                <footer className="flex flex-wrap items-center justify-between gap-3 px-1 text-[10.5px] text-[#8C9994]">
                    <span>NemoGuard V22</span>
                    <span>Snapshot data</span>
                </footer>
            </div>

            <Link
                to="/app/command-center"
                aria-label="Open Commander"
                className="fixed bottom-6 right-6 z-30 flex h-14 w-14 items-center justify-center rounded-full bg-[#073F32] text-white shadow-[0_18px_40px_-16px_rgba(7,63,50,.75)] transition-transform hover:-translate-y-1 active:scale-[0.96] focus-ring"
            >
                <MessageCircle size={21} />
            </Link>
        </main>
    );
}

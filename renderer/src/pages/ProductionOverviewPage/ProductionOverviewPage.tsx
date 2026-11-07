import { useEffect, useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react';
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
  ListChecks,
  Radio,
  RefreshCw,
  Server,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Workflow,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthGateContext';

type IncidentSummary = {
  incident_id: string;
  title: string;
  status: string;
  severity: string;
  detected_at?: string;
  owner_team?: string;
  summary?: string;
};

type AlertRecord = {
  alert_id: string;
  status: string;
  severity?: string;
  alert_type?: string;
  source_system?: string;
  opened_ts?: string;
};

type SystemStatus = {
  environment?: string;
  database?: string;
  database_backend?: string;
  orchestrator?: string;
  policy_engine?: string;
  temporal?: string;
  inference_provider?: string;
  last_checked_at?: string;
};

type Feed<T> = {
  data: T | null;
  updatedAt: Date | null;
  error: boolean;
};

const emptyFeed = <T,>(): Feed<T> => ({ data: null, updatedAt: null, error: false });

function isHighPriority(value?: string) {
  const normalized = value?.toUpperCase();
  return normalized === 'CRITICAL' || normalized === 'HIGH' || normalized === 'SEV-1' || normalized === 'SEV-2';
}

function relativeTime(value?: string) {
  if (!value) return 'Time unavailable';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Time unavailable';
  const minutes = Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function freshness(feed: Feed<unknown>) {
  if (!feed.updatedAt) return feed.error ? 'Unavailable' : 'Loading';
  if (feed.error) return `Showing last known data · ${relativeTime(feed.updatedAt.toISOString())}`;
  return `Updated ${relativeTime(feed.updatedAt.toISOString())}`;
}

function V22Card({ children, className = '', interactive = false }: { children: ReactNode; className?: string; interactive?: boolean }) {
  return <section className={`v22-card ${interactive ? 'v22-interactive' : ''} ${className}`}>{children}</section>;
}

function MetricCard({
  title,
  value,
  detail,
  icon,
  tone = 'green',
  to,
}: {
  title: string;
  value: string | number;
  detail: string;
  icon: ReactNode;
  tone?: 'green' | 'amber' | 'blue' | 'muted';
  to?: string;
}) {
  const tones = {
    green: 'bg-[#E1F3EC] text-[#0C6F58]',
    amber: 'bg-[#FFF1DE] text-[#B96D1C]',
    blue: 'bg-[#E8F0FF] text-[#3777D6]',
    muted: 'bg-[#EDF3EE] text-[#62716B]',
  };

  const content = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[13px] font-semibold text-[#62716B]">{title}</p>
        <span className={`flex h-9 w-9 items-center justify-center rounded-2xl ${tones[tone]}`}>{icon}</span>
      </div>
      <p className="mt-5 font-mono text-[32px] font-bold leading-none tracking-[-0.06em] text-[#13231E]">{value}</p>
      <p className="mt-3 text-[12px] leading-relaxed text-[#62716B]">{detail}</p>
    </>
  );

  return to ? (
    <Link to={to} className="v22-card v22-interactive block min-h-[176px] p-5 focus-ring">
      {content}
    </Link>
  ) : (
    <V22Card className="min-h-[176px] p-5">{content}</V22Card>
  );
}

function HealthRow({ label, detail, state = 'healthy', icon }: { label: string; detail: string; state?: 'healthy' | 'warning' | 'unknown'; icon: ReactNode }) {
  const colors = {
    healthy: 'bg-[#18A566]',
    warning: 'bg-[#E5A52B]',
    unknown: 'bg-[#8C9994]',
  };

  return (
    <div className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
      <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#EDF3EE] text-[#0C6F58]">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] font-semibold text-[#13231E]">{label}</span>
        <span className="block truncate text-[11px] text-[#62716B]">{detail}</span>
      </span>
      <span className={`v22-status-dot ${colors[state]}`} aria-label={state} />
    </div>
  );
}

export function ProductionOverviewPage() {
  const [incidents, setIncidents] = useState<Feed<IncidentSummary[]>>(emptyFeed);
  const [alerts, setAlerts] = useState<Feed<AlertRecord[]>>(emptyFeed);
  const [system, setSystem] = useState<Feed<SystemStatus>>(emptyFeed);

  useEffect(() => {
    let alive = true;

    async function load<T>(url: string, setFeed: Dispatch<SetStateAction<Feed<T>>>, validate: (value: unknown) => value is T) {
      try {
        const response = await authFetch(url);
        if (!response.ok) throw new Error('Request failed');
        const data: unknown = await response.json();
        if (!validate(data)) throw new Error('Unexpected response');
        if (alive) setFeed({ data, updatedAt: new Date(), error: false });
      } catch {
        if (alive) {
          setFeed((previous) => ({ ...previous, error: true }));
        }
      }
    }

    const refresh = () => Promise.all([
      load<IncidentSummary[]>('/api/v2/incidents?state=open', setIncidents, Array.isArray),
      load<AlertRecord[]>('/api/v2/alerts', setAlerts, Array.isArray),
      load<SystemStatus>('/api/v2/status', setSystem, (value): value is SystemStatus => Boolean(value) && typeof value === 'object' && !Array.isArray(value)),
    ]);

    void refresh();
    const interval = window.setInterval(() => void refresh(), 30000);
    return () => {
      alive = false;
      window.clearInterval(interval);
    };
  }, []);

  const derived = useMemo(() => {
    const openIncidents = incidents.data ?? [];
    const allAlerts = alerts.data ?? [];
    const critical = openIncidents.filter((incident) => isHighPriority(incident.severity)).length;
    const highPriorityAlerts = allAlerts.filter((alert) => alert.status?.toLowerCase() === 'open' && isHighPriority(alert.severity)).length;
    const actionItems = openIncidents
      .filter((incident) => isHighPriority(incident.severity))
      .slice(0, 3)
      .map((incident) => ({
        incident,
        label: incident.severity?.toUpperCase() === 'SEV-1' || incident.severity?.toUpperCase() === 'CRITICAL'
          ? 'Critical incident requires review'
          : 'Open incident requires attention',
      }));

    return { openIncidents, critical, highPriorityAlerts, actionItems };
  }, [alerts.data, incidents.data]);

  const systemHealthy = system.data?.database === 'healthy';
  const temporalLabel = system.data?.temporal === 'connected'
    ? 'Temporal connected'
    : system.data?.temporal === 'disabled'
      ? 'Direct fallback mode'
      : system.data?.temporal === 'disconnected'
        ? 'Temporal disconnected'
        : 'Workflow state unavailable';

  return (
    <main className="v22-canvas h-full overflow-y-auto px-4 py-5 sm:px-7 sm:py-7">
      <div className="v22-content-enter mx-auto max-w-[1600px] space-y-5 pb-8">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.16em] text-[#0C6F58]">
              <span className="v22-status-dot bg-[#18A566]" />
              Production overview
            </div>
            <h1 className="mt-2 text-[30px] font-bold tracking-[-0.045em] text-[#13231E]">Operations, at a glance.</h1>
          </div>
          <p className="text-right text-[12px] text-[#62716B]">
            <span className="font-semibold text-[#13231E]">Overview snapshot</span>
            <br />
            {freshness(incidents)}
          </p>
        </header>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
          <V22Card className="relative overflow-hidden bg-[#073F32] p-6 text-white xl:col-span-8">
            <div className="absolute -right-16 -top-20 h-60 w-60 rounded-full bg-[#8FE4C9]/15 blur-3xl" />
            <div className="relative flex h-full flex-col justify-between gap-7">
              <div className="flex items-start justify-between gap-4">
                <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white/10 text-[#8FE4C9]"><Sparkles size={21} /></span>
                <span className="rounded-full bg-white/10 px-3 py-1.5 text-[11px] font-semibold text-white/75">Deterministic brief · V22.1</span>
              </div>
              <div>
                <h2 className="max-w-2xl text-[27px] font-bold leading-tight tracking-[-0.04em]">
                  {derived.openIncidents.length === 0
                    ? 'No open incidents are currently recorded.'
                    : `${derived.openIncidents.length} open ${derived.openIncidents.length === 1 ? 'incident requires' : 'incidents require'} attention.`}
                </h2>
                <p className="mt-3 max-w-2xl text-[14px] leading-relaxed text-white/70">
                  {incidents.error
                    ? 'Incident data could not be refreshed. Review the last known state before making an operational decision.'
                    : `${derived.critical} high-severity open incident${derived.critical === 1 ? '' : 's'} detected. ${systemHealthy ? 'Core database health is reporting normally.' : 'Platform health requires review.'}`}
                </p>
              </div>
              <div className="flex flex-wrap gap-3">
                <Link to="/app/command-center" className="inline-flex items-center gap-2 rounded-xl bg-[#FF9C55] px-4 py-2.5 text-[13px] font-bold text-[#13231E] transition-transform active:scale-[0.985]">
                  Open Command Center <ArrowRight size={16} />
                </Link>
                <Link to="/app/incidents?state=open" className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/5 px-4 py-2.5 text-[13px] font-semibold text-white hover:bg-white/10">
                  Review incidents <ChevronRight size={16} />
                </Link>
              </div>
            </div>
          </V22Card>

          <V22Card className="p-5 xl:col-span-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[15px] font-bold text-[#13231E]">Action Queue</p>
                <p className="mt-1 text-[12px] text-[#62716B]">Human attention, prioritized.</p>
              </div>
              <span className="flex h-9 w-9 items-center justify-center rounded-2xl bg-[#FFF1DE] text-[#B96D1C]"><ListChecks size={18} /></span>
            </div>
            <div className="mt-4 space-y-2">
              {incidents.data === null ? (
                <p className="rounded-2xl bg-[#EDF3EE] p-4 text-[12px] text-[#62716B]">{incidents.error ? 'Unable to load action items.' : 'Loading action items…'}</p>
              ) : derived.actionItems.length === 0 ? (
                <p className="rounded-2xl bg-[#E1F3EC] p-4 text-[12px] leading-relaxed text-[#0C6F58]">No high-severity open incidents are currently recorded.</p>
              ) : derived.actionItems.map(({ incident, label }) => (
                <Link key={incident.incident_id} to={`/app/command-center?incident=${encodeURIComponent(incident.incident_id)}`} className="v22-interactive block rounded-2xl bg-[#EDF3EE] p-3.5 focus-ring">
                  <div className="flex gap-3">
                    <TriangleAlert className="mt-0.5 shrink-0 text-[#DE565E]" size={16} />
                    <span className="min-w-0">
                      <span className="block truncate text-[12px] font-bold text-[#13231E]">{incident.title || incident.incident_id}</span>
                      <span className="mt-1 block text-[11px] text-[#62716B]">{incident.incident_id} · {label}</span>
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          </V22Card>
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
          <V22Card className="p-5 xl:col-span-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-[15px] font-bold text-[#13231E]">Agent Fleet</p>
                <p className="mt-1 text-[12px] text-[#62716B]">Runtime projection arrives in V22.3.</p>
              </div>
              <Bot size={19} className="text-[#0C6F58]" />
            </div>
            <div className="mt-5 grid grid-cols-2 gap-2">
              {['Watcher', 'RCA', 'Dependency', 'Runbook', 'Safety', 'Commander'].map((agent) => (
                <Link key={agent} to={`/app/agent-operations?agent=${agent.toLowerCase()}`} className="v22-interactive rounded-2xl bg-[#EDF3EE] px-3 py-3 focus-ring">
                  <span className="flex items-center gap-1.5 text-[12px] font-bold text-[#13231E]"><span className="v22-status-dot bg-[#8C9994]" />{agent}</span>
                  <span className="mt-1.5 block text-[10px] text-[#62716B]">Runtime state pending</span>
                </Link>
              ))}
            </div>
          </V22Card>

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:col-span-8 xl:grid-cols-4">
            <MetricCard title="Open Incidents" value={incidents.data === null ? '—' : derived.openIncidents.length} detail={incidents.error ? 'Refresh failed; last-known value shown.' : `${derived.critical} high severity · current snapshot`} icon={<CircleAlert size={18} />} to="/app/incidents?state=open" />
            <MetricCard title="High-Priority Alerts" value={alerts.data === null ? '—' : derived.highPriorityAlerts} detail={alerts.data?.some((alert) => alert.severity) ? 'Open Critical / High alerts' : 'Severity projection requires V22.2'} icon={<AlertTriangle size={18} />} tone="amber" to="/app/alert-inbox" />
            <MetricCard title="Verified Recoveries" value="—" detail="Verified recovery projection is planned for V22.2." icon={<CheckCircle2 size={18} />} tone="blue" />
            <MetricCard title="Median Triage Time" value="—" detail="Historical triage metric is planned for V22.2." icon={<Clock3 size={18} />} tone="muted" />
          </div>
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
          <V22Card className="p-5 xl:col-span-8">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-[15px] font-bold text-[#13231E]">Incident & Alert Activity</p>
                <p className="mt-1 text-[12px] text-[#62716B]">Current operational snapshot. Historical series arrives in V22.2.</p>
              </div>
              <span className="rounded-xl bg-[#EDF3EE] px-3 py-2 text-[11px] font-semibold text-[#62716B]">Snapshot only</span>
            </div>
            <div className="mt-7 grid grid-cols-2 gap-4 sm:grid-cols-4">
              {[
                { label: 'Open incidents', value: derived.openIncidents.length, color: '#0C6F58' },
                { label: 'High severity', value: derived.critical, color: '#DE565E' },
                { label: 'Stored alerts', value: alerts.data?.length ?? '—', color: '#3777D6' },
                { label: 'Open alerts', value: alerts.data?.filter((alert) => alert.status?.toLowerCase() === 'open').length ?? '—', color: '#E5A52B' },
              ].map((item) => (
                <div key={item.label} className="rounded-2xl bg-[#EDF3EE] p-4">
                  <div className="h-1.5 rounded-full" style={{ width: '42%', backgroundColor: item.color }} />
                  <p className="mt-6 font-mono text-[27px] font-bold tracking-[-0.06em] text-[#13231E]">{item.value}</p>
                  <p className="mt-1 text-[11px] text-[#62716B]">{item.label}</p>
                </div>
              ))}
            </div>
          </V22Card>

          <V22Card className="p-5 xl:col-span-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[15px] font-bold text-[#13231E]">System Health</p>
                <p className="mt-1 text-[12px] text-[#62716B]">{system.data?.last_checked_at ? `Checked ${relativeTime(system.data.last_checked_at)}` : freshness(system)}</p>
              </div>
              <Server size={19} className="text-[#0C6F58]" />
            </div>
            <div className="mt-5 divide-y divide-[#0F3F32]/10">
              <HealthRow label="Database" detail={system.data?.database_backend ? `${system.data.database_backend} · ${system.data.database ?? 'unknown'}` : 'Loading database state'} state={system.data?.database === 'healthy' ? 'healthy' : system.data ? 'warning' : 'unknown'} icon={<Database size={16} />} />
              <HealthRow label="Workflow" detail={temporalLabel} state={system.data?.temporal === 'connected' || system.data?.temporal === 'disabled' ? 'healthy' : system.data ? 'warning' : 'unknown'} icon={<Workflow size={16} />} />
              <HealthRow label="Policy Engine" detail={system.data?.policy_engine ?? 'Loading policy state'} state={system.data?.policy_engine === 'active' ? 'healthy' : system.data ? 'warning' : 'unknown'} icon={<ShieldCheck size={16} />} />
              <HealthRow label="Inference" detail={system.data?.inference_provider ? `${system.data.inference_provider} provider configured` : 'Provider status unavailable'} state={system.data?.inference_provider ? 'healthy' : 'unknown'} icon={<Sparkles size={16} />} />
            </div>
          </V22Card>
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
          <V22Card className="p-5 xl:col-span-7">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[15px] font-bold text-[#13231E]">Recent Investigations</p>
                <p className="mt-1 text-[12px] text-[#62716B]">Open incidents from NemoGuard’s authoritative feed.</p>
              </div>
              <Link to="/app/command-center" className="text-[12px] font-bold text-[#0C6F58]">Open Command Center</Link>
            </div>
            <div className="mt-4 space-y-2">
              {incidents.data === null ? (
                <p className="rounded-2xl bg-[#EDF3EE] p-4 text-[12px] text-[#62716B]">{incidents.error ? 'Incident feed unavailable. Retry from the command center.' : 'Loading investigations…'}</p>
              ) : derived.openIncidents.length === 0 ? (
                <p className="rounded-2xl bg-[#E1F3EC] p-4 text-[12px] text-[#0C6F58]">No open incident investigations are currently recorded.</p>
              ) : derived.openIncidents.slice(0, 4).map((incident) => (
                <Link key={incident.incident_id} to={`/app/command-center?incident=${encodeURIComponent(incident.incident_id)}`} className="v22-interactive flex items-center gap-3 rounded-2xl bg-[#EDF3EE] px-4 py-3 focus-ring">
                  <span className={`v22-status-dot ${isHighPriority(incident.severity) ? 'bg-[#DE565E]' : 'bg-[#E5A52B]'}`} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-bold text-[#13231E]">{incident.title || incident.incident_id}</span>
                    <span className="mt-1 block text-[11px] text-[#62716B]">{incident.incident_id} · {incident.status || 'Status unavailable'} · {relativeTime(incident.detected_at)}</span>
                  </span>
                  <ChevronRight size={17} className="shrink-0 text-[#62716B]" />
                </Link>
              ))}
            </div>
          </V22Card>

          <V22Card className="p-5 xl:col-span-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[15px] font-bold text-[#13231E]">Live Operations Feed</p>
                <p className="mt-1 text-[12px] text-[#62716B]">Authenticated cross-incident stream arrives in V22.3.</p>
              </div>
              <Radio size={19} className="text-[#3777D6]" />
            </div>
            <div className="mt-5 rounded-2xl border border-dashed border-[#0F3F32]/15 bg-[#EDF3EE] p-5">
              <Activity className="text-[#0C6F58]" size={19} />
              <p className="mt-3 text-[13px] font-bold text-[#13231E]">No simulated production activity</p>
              <p className="mt-1 text-[12px] leading-relaxed text-[#62716B]">V22.3 will consolidate authenticated streaming fetches, update affected panels, and show reconnecting or stale state when live data is unavailable.</p>
            </div>
          </V22Card>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 px-1 text-[11px] text-[#62716B]">
          <span className="inline-flex items-center gap-1.5"><RefreshCw size={12} /> Incidents and alerts refresh every 30 seconds in V22.1.</span>
          <span>{system.error ? 'System health refresh failed; shown values may be stale.' : 'Health indicators reflect the API status response, not independent external verification.'}</span>
        </div>
      </div>
    </main>
  );
}

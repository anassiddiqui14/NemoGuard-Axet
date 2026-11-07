import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, AlertTriangle, ArrowRight, ClipboardList, Server } from 'lucide-react';
import { authFetch } from '../../contexts/AuthGateContext';
import { severityPill, statusBadge } from '../../components/dashboard/shared';
import type { IncidentSummary } from '../../components/dashboard/shared';

type AlertRecord = { alert_id: string; status: string };
type Feed<T> = { data: T | null; updatedAt: Date | null; error: boolean };

const emptyFeed = <T, >(): Feed<T> => ({ data: null, updatedAt: null, error: false });

function Metric({ title, value, description, icon }: {
  title: string; value: string | number; description: string; icon: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5">
      <div className="flex items-center justify-between gap-3 text-text-secondary">
        <h2 className="text-sm font-medium">{title}</h2>
        <span className="text-primary">{icon}</span>
      </div>
      <div className="mt-4 text-3xl font-semibold text-text-primary">{value}</div>
      <p className="mt-2 text-xs leading-relaxed text-text-muted">{description}</p>
    </section>
  );
}

function feedStatus(label: string, feed: Feed<unknown>) {
  if (feed.error) return `${label}: ${feed.updatedAt ? `refresh failed; showing data from ${feed.updatedAt.toLocaleTimeString()}` : 'unavailable'}`;
  return `${label}: ${feed.updatedAt ? `updated ${feed.updatedAt.toLocaleTimeString()}` : 'loading'}`;
}

export function ProductionOverviewPage() {
  const [incidents, setIncidents] = useState<Feed<IncidentSummary[]>>(emptyFeed);
  const [alerts, setAlerts] = useState<Feed<AlertRecord[]>>(emptyFeed);

  useEffect(() => {
    let alive = true;
    let inFlight = false;

    async function refresh() {
      if (inFlight) return;
      inFlight = true;
      async function load<T,>(url: string, setFeed: React.Dispatch<React.SetStateAction<Feed<T[]>>>) {
        try {
          const response = await authFetch(url);
          if (!response.ok) throw new Error('Feed unavailable');
          const data: unknown = await response.json();
          if (!Array.isArray(data)) throw new Error('Invalid feed');
          if (alive) setFeed({ data: data as T[], updatedAt: new Date(), error: false });
        } catch {
          if (alive) setFeed((previous) => ({ ...previous, error: true }));
        }
      }
      await Promise.all([
        load<IncidentSummary>('/api/v2/incidents?state=open', setIncidents),
        load<AlertRecord>('/api/v2/alerts', setAlerts),
      ]);
      inFlight = false;
    }

    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  const openAlerts = alerts.data?.filter((alert) => alert.status?.toLowerCase() === 'open').length;
  return (
    <main className="h-full overflow-y-auto bg-app-bg px-5 py-7 text-text-primary sm:px-8">
      <div className="mx-auto max-w-7xl space-y-7">
        <header className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-primary">Production overview</p>
            <h1 className="mt-2 text-2xl font-semibold sm:text-3xl">Current situation</h1>
            <p className="mt-2 max-w-2xl text-sm text-text-secondary">A view of what NemoGuard has recorded. Missing alerts do not establish that production is healthy.</p>
          </div>
          <Link to="/app/command-center" className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white">
            Open command center <ArrowRight size={16} />
          </Link>
        </header>

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Metric title="Open alert records" value={openAlerts ?? '—'} icon={<AlertTriangle size={19} />} description="Stored alerts marked open; recovery may not update this status. Not a live firing count." />
          <Metric title="Open NemoGuard incidents" value={incidents.data?.length ?? '—'} icon={<Activity size={19} />} description="Active incident records in NemoGuard, including email-derived incidents." />
          <Metric title="ITSM incidents" value="Unavailable" icon={<ClipboardList size={19} />} description="No verified ITSM incident feed is available on this dashboard." />
          <Metric title="Services failed or down" value="Unknown" icon={<Server size={19} />} description="Requires independent service-health telemetry; email alone cannot verify this." />
        </div>

        <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-lg font-semibold">Open incidents</h2><p className="mt-1 text-xs text-text-muted">Most recently detected, from NemoGuard's incident feed.</p></div>
            <Link to="/app/command-center" className="text-sm text-primary">View command center →</Link>
          </div>
          {incidents.data === null ? (
            <p className="py-8 text-sm text-text-muted">{incidents.error ? 'Incident feed unavailable.' : 'Loading incidents…'}</p>
          ) : incidents.data.length === 0 ? (
            <p className="py-8 text-sm text-text-muted">No open incidents recorded. This does not confirm production is healthy.</p>
          ) : (
            <ul className="mt-4 divide-y divide-white/10">
              {incidents.data.slice(0, 8).map((incident) => (
                <li key={incident.incident_id}>
                  <Link to={`/app/command-center?incident=${encodeURIComponent(incident.incident_id)}`} className="flex flex-wrap items-center justify-between gap-3 py-4 hover:text-primary">
                    <span className="min-w-0">
                      <span className="block font-medium">{incident.title || incident.incident_id}</span>
                      <span className="mt-1 block text-xs text-text-muted">{incident.incident_id} · {incident.owner_team || 'Owner not specified'}</span>
                    </span>
                    <span className="flex items-center gap-2">{severityPill(incident.severity)}{statusBadge(incident.status)}<ArrowRight size={16} /></span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-live="polite" className="rounded-xl border border-white/10 p-4 text-xs leading-relaxed text-text-muted">
          <p className="font-semibold text-text-secondary">Data coverage</p>
          <p className="mt-1">{feedStatus('Incident feed', incidents)} · {feedStatus('Alert feed', alerts)}</p>
          <p className="mt-1">Feed timestamps indicate when the dashboard fetched records, not when production health was verified. Outlook bridge health and unreceived emails are not measured here.</p>
        </section>
      </div>
    </main>
  );
}

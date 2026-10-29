/**
 * Shared session cache for the Command Center (Dashboard.tsx) incident
 * queue/selection state.
 *
 * Why this exists: Dashboard.tsx is unmounted/remounted every time the
 * operator navigates to another route (Scenario Cockpit, Agent
 * Operations, Incidents, ...) and back -- React Router does not keep it
 * alive in the background. Every remount previously started from a
 * completely blank in-memory state (empty arrays, activeIncidentId=null,
 * queueLoaded=false) and had to wait for a fresh network round-trip
 * before it could show anything, which is what produced the "0 active
 * incidents" / "No incident selected" flash the operator saw for a few
 * seconds on every single return to Command Center.
 *
 * This module gives Dashboard.tsx a tiny, dependency-free
 * stale-while-revalidate cache: the last known-good queue + selection is
 * written to sessionStorage after every successful authoritative
 * refresh, and read back synchronously (via a lazy useState initializer,
 * NOT a useEffect) the moment the component mounts again, so the very
 * first render already shows real data while a background refresh goes
 * out to confirm/replace it.
 *
 * No new dependency (e.g. React Query) is introduced; this is a small,
 * purpose-built helper so the cache key and shape are defined exactly
 * once and reused by anything that needs to read, write, or invalidate
 * it (Dashboard.tsx for normal operation, ScenarioCockpitPage's "Reset
 * All Incidents" flow for explicit invalidation).
 */

import type { IncidentSummary } from '../components/dashboard/shared';

export const DASHBOARD_CACHE_KEY = 'nemoguard.dashboard.incident-state.v1';

export interface DashboardCacheState {
  openIncidents: IncidentSummary[];
  allIncidents: IncidentSummary[];
  selectedIncidentId: string | null;
  savedAt: number;
}

/**
 * Reads the last known-good dashboard state, or null if nothing was ever
 * saved (first-ever load) or the stored value is corrupt/unparseable.
 * Never throws.
 */
export function readDashboardCache(): DashboardCacheState | null {
  try {
    const raw = sessionStorage.getItem(DASHBOARD_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      !parsed ||
      !Array.isArray(parsed.openIncidents) ||
      !Array.isArray(parsed.allIncidents)
    ) {
      return null;
    }
    return parsed as DashboardCacheState;
  } catch {
    return null;
  }
}

/**
 * Persists the last known-good dashboard state. Called only after a
 * SUCCESSFUL authoritative refresh -- never on a failed/transient fetch,
 * so a temporary network error can never overwrite real data with a
 * stale/incomplete snapshot.
 */
export function writeDashboardCache(state: Omit<DashboardCacheState, 'savedAt'>): void {
  try {
    const toStore: DashboardCacheState = { ...state, savedAt: Date.now() };
    sessionStorage.setItem(DASHBOARD_CACHE_KEY, JSON.stringify(toStore));
  } catch {
    // sessionStorage can throw (quota exceeded, privacy mode, etc.) --
    // the cache is purely a UX nicety, never a correctness requirement,
    // so a failed write is silently ignored.
  }
}

/**
 * Explicitly invalidates the cache. Used by the Scenario Cockpit's
 * "Reset All Incidents" flow so a returning Dashboard mount cannot show
 * stale pre-reset incidents even momentarily; the very next authoritative
 * refresh (which runs on every Dashboard mount) would clear them anyway,
 * but clearing the cache up front avoids that one extra stale frame.
 */
export function clearDashboardCache(): void {
  try {
    sessionStorage.removeItem(DASHBOARD_CACHE_KEY);
  } catch {
    // Same as above -- best-effort only.
  }
}

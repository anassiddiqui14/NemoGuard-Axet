import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import { IncidentQueue } from './dashboard/IncidentQueue';
import { IncidentWorkspace } from './dashboard/IncidentWorkspace';
import { GreetingBar } from './shell/GreetingBar';
import type { IncidentSummary } from './dashboard/shared';
import { needsAttention } from './dashboard/shared';
import { authFetch } from '../contexts/AuthGateContext';
import { readDashboardCache, writeDashboardCache } from '../lib/dashboardCache';

export function Dashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const focusIncidentId = searchParams.get('incident');

  // Stale-while-revalidate: read the last known-good state synchronously
  // on mount (lazy initializer, not a useEffect) so the VERY FIRST render
  // after returning from another route already shows real data instead
  // of an empty queue / "No incident selected" while the authoritative
  // refresh is still in flight. This is what eliminates the multi-second
  // blank flash the operator saw on every Command Center revisit.
  const cached = useMemo(() => readDashboardCache(), []);

  const [activeIncidentId, setActiveIncidentId] = useState<string | null>(
    () => cached?.selectedIncidentId ?? null,
  );
  const [openIncidents, setOpenIncidents] = useState<IncidentSummary[]>(
    () => cached?.openIncidents ?? [],
  );
  const [resolvedIncidents, setResolvedIncidents] = useState<IncidentSummary[]>(() => {
    const all = cached?.allIncidents ?? [];
    return all.filter((i) => i.status?.toUpperCase() === 'RESOLVED');
  });

  // Separate "have we EVER successfully loaded real data" (either from
  // cache or from the network) from "is a background refresh currently in
  // flight". Only the former gates a first-load skeleton; a background
  // refresh must NEVER blank or dim the existing content.
  const [hasLoadedSuccessfully, setHasLoadedSuccessfully] = useState(!!cached);

  // UI MODERNIZATION (Phase 6, spec Section 31/62): a failed poll
  // previously wiped BOTH incident arrays to empty in the catch block --
  // identical in appearance to the legitimate "zero active incidents"
  // empty state. An operator whose network blipped mid-incident would see
  // the entire queue vanish and have no way to tell "the system is
  // actually healthy and quiet" apart from "the dashboard just lost its
  // connection to the API". Now a failed refresh keeps the last-known-good
  // data on screen and surfaces a dedicated, dismissable connection-error
  // banner instead of silently discarding state.
  const [queueError, setQueueError] = useState<string | null>(null);

  // Guards against out-of-order overlapping refreshes: if request A starts,
  // then request B starts before A finishes, and B's response arrives
  // first, A's (now-stale) response must be discarded when it eventually
  // arrives rather than overwriting B's newer state.
  const refreshSeqRef = useRef(0);

  // Tracks the operator's current selection synchronously (via a ref, not
  // just state) so refreshQueue's selection-preservation logic always sees
  // the up-to-date value even if several refreshes are reasoned about
  // back-to-back before React re-renders.
  const activeIncidentIdRef = useRef<string | null>(activeIncidentId);
  activeIncidentIdRef.current = activeIncidentId;

  // Guards against calling setState after this component has unmounted
  // (e.g. the operator navigated away while a refresh was still in
  // flight -- the fetch Promise resolves later, after unmount, and must
  // not touch state at that point).
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const refreshQueue = async () => {
    const mySeq = ++refreshSeqRef.current;
    try {
      const [openRes, allRes] = await Promise.all([
        authFetch('/api/v2/incidents?state=open'),
        authFetch('/api/v2/incidents?state=all'),
      ]);
      if (!openRes.ok || !allRes.ok) throw new Error(`HTTP ${openRes.status}/${allRes.status}`);
      const openData = (await openRes.json()) as IncidentSummary[];
      const allData = (await allRes.json()) as IncidentSummary[];

      // Fetch into local variables first; only replace state after the
      // response has fully, successfully resolved. Never clear/replace
      // state mid-flight.
      const nextOpen = Array.isArray(openData) ? openData : [];
      const nextAll = Array.isArray(allData) ? allData : [];
      const nextResolved = nextAll.filter((i) => i.status?.toUpperCase() === 'RESOLVED');

      // Discard this result if a NEWER refresh has already started (and
      // will therefore also finish and apply its own, more current
      // result) -- prevents an older in-flight request that happens to
      // resolve later from clobbering newer state. Also bail out
      // entirely if the component has been unmounted in the meantime.
      if (mySeq !== refreshSeqRef.current || !isMountedRef.current) return;

      setOpenIncidents(nextOpen);
      setResolvedIncidents(nextResolved);
      setQueueError(null);
      setHasLoadedSuccessfully(true);

      // Selection-preservation rules, evaluated against the FRESH data:
      // 1. If the current selection still exists anywhere (open or
      //    resolved), keep it exactly as-is -- even if it moved from open
      //    to resolved, so the workspace can show its new terminal state
      //    instead of flashing empty.
      // 2. If it no longer exists anywhere (e.g. after a Scenario
      //    Cockpit "Reset All"), fall back to the first open incident.
      // 3. If there are truly no incidents at all in the authoritative
      //    response, selection becomes null -- this is the ONLY path
      //    that is allowed to null out the selection.
      const current = activeIncidentIdRef.current;
      const currentStillExists =
        !!current &&
        (nextOpen.some((i) => i.incident_id === current) ||
          nextResolved.some((i) => i.incident_id === current));

      let nextSelected: string | null;
      if (currentStillExists) {
        nextSelected = current;
      } else if (nextOpen.length > 0) {
        nextSelected = nextOpen[0].incident_id;
      } else {
        nextSelected = null;
      }

      if (nextSelected !== current) {
        setActiveIncidentId(nextSelected);
      }

      // Only an authoritative, SUCCESSFUL response is ever persisted --
      // a transient/failed refresh (handled in the catch block below)
      // never overwrites the cache with incomplete data.
      writeDashboardCache({
        openIncidents: nextOpen,
        allIncidents: nextAll,
        selectedIncidentId: nextSelected,
      });
    } catch {
      if (mySeq !== refreshSeqRef.current) return;
      // Deliberately do NOT clear openIncidents/resolvedIncidents or the
      // current selection here -- stale-but-real data is far more useful
      // to an operator than an empty queue that looks like "all clear".
      // A transient/network/auth-verification failure must never be
      // interpreted as "there are zero incidents".
      setQueueError('Lost connection to the incident feed. Showing last known data.');
    }
  };

  // An incident selected from the queue can now come from either the active
  // (open) list or the resolved list — previously this only looked at
  // openIncidents, so clicking a resolved incident would render nothing.
  const selectedIncident = useMemo(
    () =>
      openIncidents.find((i) => i.incident_id === activeIncidentId) ??
      resolvedIncidents.find((i) => i.incident_id === activeIncidentId) ??
      null,
    [openIncidents, resolvedIncidents, activeIncidentId],
  );

  useEffect(() => {
    void refreshQueue();
    const t = window.setInterval(() => void refreshQueue(), 2000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    if (focusIncidentId) {
      setActiveIncidentId(focusIncidentId);
      // Clear the query param once handled so subsequent queue navigation
      // isn't overridden by a stale ?incident= value on every re-render.
      searchParams.delete('incident');
      setSearchParams(searchParams, { replace: true });
    }
  }, [focusIncidentId]);

  const sortedIncidents = useMemo(() => {
    return [...openIncidents].sort((a, b) => {
      const aAttn = needsAttention(a.status) ? 0 : 1;
      const bAttn = needsAttention(b.status) ? 0 : 1;
      return aAttn - bAttn;
    });
  }, [openIncidents]);

  // Only relevant if a consumer needs to distinguish "truly nothing to
  // show yet" from "showing possibly-stale-but-real data while refreshing
  // in the background" -- kept here (even though not currently passed
  // down) so the distinction is explicit and future UI tweaks don't have
  // to re-derive it.
  void hasLoadedSuccessfully;

  return (
    <div className="flex h-full min-h-0 bg-app-bg text-text-primary overflow-hidden selection:bg-primary/30">
      {/*
       * UI MODERNIZATION (spec Section 30 -- Dashboard responsive
       * behavior): IncidentQueue has a fixed w-[300px], and this
       * container had no responsive handling at all -- at a 375px
       * mobile viewport that left only ~75px for the entire main
       * content area, which the spec explicitly calls unacceptable
       * ("Do not squeeze 3 columns... use sequential visual states:
       * Incident Queue -> Incident Detail"). Below md, the queue and
       * detail pane now behave like the IncidentsPage's existing
       * list/detail pattern: show the queue OR the detail, never a
       * squeezed version of both, with the queue reappearing once no
       * incident is selected.
       */}
      <div className={`${activeIncidentId ? 'hidden md:flex' : 'flex'} md:flex-shrink-0`}>
        <IncidentQueue
          openIncidents={sortedIncidents}
          activeIncidentId={activeIncidentId}
          setActiveIncidentId={setActiveIncidentId}
          refreshQueue={refreshQueue}
          connectionError={queueError}
        />
      </div>

      <motion.main
        key={activeIncidentId ?? 'no-incident'}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.2 }}
        className={`flex-1 overflow-y-auto flex-col gap-4 min-w-0 ${activeIncidentId ? 'flex' : 'hidden md:flex'}`}
      >
        <GreetingBar
          activeCount={openIncidents.length}
          approvalCount={openIncidents.filter((i) => needsAttention(i.status)).length}
        />

        <div className="px-5 pb-5 flex flex-col gap-4">
          <IncidentWorkspace
            incidentId={activeIncidentId}
            selectedIncident={selectedIncident}
            onRefreshParent={refreshQueue}
            // Mobile-only "back to queue" -- on desktop (md+) the queue
            // is always visible alongside the detail pane, so a visible
            // back button there would be redundant.
            onBack={() => setActiveIncidentId(null)}
            backButtonMobileOnly
          />
        </div>
      </motion.main>
    </div>
  );
}

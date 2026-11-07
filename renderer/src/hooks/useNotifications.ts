import { useEffect, useState } from 'react';
import { authFetch } from '../contexts/AuthGateContext';

export interface NotificationItem {
    id: string;
    incident_id: string;
    title: string;
    kind:
        | 'sla_risk'
        | 'approval_required'
        | 'safety_review'
        | 'manual_action'
        | 'verification_failed'
        | 'stalled_investigation'
        | 'open_incident';
    detail: string;
    severity: string;
    score: number;
}

interface AttentionItem {
    id: string;
    incident_id?: string | null;
    title: string;
    kind: NotificationItem['kind'] | 'platform_health' | 'alert_intake';
    detail: string;
    severity?: string | null;
    score: number;
}

const SLA_NOTIFICATIONS_KEY = 'nemoguard_sla_notifications_enabled';

export function getSlaNotificationsEnabled(): boolean {
    return localStorage.getItem(SLA_NOTIFICATIONS_KEY) !== '0';
}

export function setSlaNotificationsEnabled(enabled: boolean) {
    localStorage.setItem(SLA_NOTIFICATIONS_KEY, enabled ? '1' : '0');
}

/**
 * The notification bell is intentionally a projection of the same
 * deterministic Needs Attention engine used by Production Overview.
 * That keeps "what needs me" consistent across the shell instead of
 * maintaining a second set of client-only incident rules.
 *
 * Platform/alert-intake items without an incident are left on the
 * Overview/health surfaces for now because the bell's interaction contract
 * is incident navigation. Once shell notifications support generic routes,
 * they can be included without inventing a fake incident target.
 */
export function useNotifications() {
    const [notifications, setNotifications] = useState<NotificationItem[]>([]);

    useEffect(() => {
        let cancelled = false;

        const poll = async () => {
            try {
                const res = await authFetch('/api/v2/operations/snapshot');
                if (!res.ok) return;
                const snapshot = await res.json();
                const attention = Array.isArray(snapshot?.needs_attention)
                    ? (snapshot.needs_attention as AttentionItem[])
                    : [];
                if (cancelled) return;

                const next = attention
                    .filter((item): item is AttentionItem & { incident_id: string } => Boolean(item.incident_id))
                    .filter((item) => item.kind !== 'sla_risk' || getSlaNotificationsEnabled())
                    .filter((item) => item.kind !== 'platform_health' && item.kind !== 'alert_intake')
                    .slice(0, 12)
                    .map((item) => ({
                        id: item.id,
                        incident_id: item.incident_id,
                        title: item.title,
                        kind: item.kind as NotificationItem['kind'],
                        detail: item.detail,
                        severity: item.severity || 'unknown',
                        score: item.score,
                    }));

                setNotifications(next);
            } catch {
                // Keep the last known notification state on a transient
                // network error; Production Overview separately exposes
                // freshness/staleness so the operator is not misled.
            }
        };

        void poll();
        const timer = window.setInterval(poll, 10_000);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, []);

    return notifications;
}

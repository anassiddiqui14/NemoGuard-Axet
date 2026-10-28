import { useEffect, useRef, useState } from 'react';
import { authFetch } from '../contexts/AuthGateContext';

export interface AgentEvent {
    id: string;
    timestamp: string;
    source: string;
    event_type: string;
    message: string;
}

const RECONNECT_DELAY_MS = 2000;

/**
 * Shared authenticated SSE subscription for a single incident.
 *
 * We intentionally use fetch() instead of EventSource because EventSource
 * cannot attach the Authorization header required by aXet/Okta.
 *
 * authFetch() handles both:
 *
 * Local NemoGuard:
 *   /api/v2/... + nemoguard_token
 *
 * aXet.flows:
 *   /backend/api/... + access_token
 *
 * The stream auto-reconnects (with a short delay) whenever the connection
 * ends unexpectedly or fails, for as long as this hook stays mounted on the
 * same incidentId. Because the backend replays the full audit history from
 * the beginning on every new connection, any events missed while the stream
 * was down are recovered automatically on reconnect; the existing event-id
 * de-duplication below prevents them from being rendered twice. `events` is
 * only cleared when `incidentId` itself changes, never across a reconnect.
 */
export function useIncidentEvents(incidentId: string | null) {
    const [events, setEvents] = useState<AgentEvent[]>([]);
    const [status, setStatus] = useState<
        'connecting' | 'connected' | 'disconnected'
    >('disconnected');

    const abortRef = useRef<AbortController | null>(null);

    useEffect(() => {
        // Clear events only because incidentId itself changed (new effect run).
        setEvents([]);

        if (!incidentId) {
            setStatus('disconnected');
            return;
        }

        let stopped = false;
        let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

        const connectOnce = async (): Promise<void> => {
            // Never allow more than one simultaneous stream connection: abort
            // any previous in-flight request before starting a new one.
            if (abortRef.current) {
                abortRef.current.abort();
                abortRef.current = null;
            }

            const controller = new AbortController();
            abortRef.current = controller;

            setStatus('connecting');

            try {
                const response = await authFetch(
                    `/api/v2/incidents/${incidentId}/events/stream`,
                    {
                        method: 'GET',
                        signal: controller.signal,
                        headers: {
                            Accept: 'text/event-stream',
                            'Cache-Control': 'no-cache',
                        },
                    },
                );

                if (!response.ok) {
                    throw new Error(
                        `SSE request failed with HTTP ${response.status}`,
                    );
                }

                if (!response.body) {
                    throw new Error('SSE response has no readable body');
                }

                setStatus('connected');

                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                let buffer = '';

                while (!stopped) {
                    const { value, done } = await reader.read();

                    if (done) {
                        break;
                    }

                    buffer += decoder.decode(value, { stream: true });

                    const frames = buffer.split(/\r?\n\r?\n/);
                    buffer = frames.pop() ?? '';

                    for (const frame of frames) {
                        const lines = frame.split(/\r?\n/);

                        for (const line of lines) {
                            if (!line.startsWith('data:')) {
                                continue;
                            }

                            const payload = line.slice(5).trim();

                            if (!payload) {
                                continue;
                            }

                            try {
                                const data =
                                    JSON.parse(payload) as AgentEvent;

                                setEvents((prev) => {
                                    if (
                                        prev.some(
                                            (evt) => evt.id === data.id,
                                        )
                                    ) {
                                        return prev;
                                    }

                                    return [...prev, data].sort(
                                        (a, b) =>
                                            new Date(
                                                a.timestamp,
                                            ).getTime() -
                                            new Date(
                                                b.timestamp,
                                            ).getTime(),
                                    );
                                });
                            } catch {
                                // Ignore malformed SSE frames.
                            }
                        }
                    }
                }

                // Stream ended (reader.read() returned done) without an explicit
                // abort from our own cleanup — this is an unexpected disconnect,
                // not an intentional stop, so we fall through below to schedule
                // a reconnect.
                if (!stopped) {
                    setStatus('disconnected');
                    scheduleReconnect();
                }
            } catch (error) {
                const wasIntentionalAbort =
                    controller.signal.aborted &&
                    error instanceof DOMException &&
                    error.name === 'AbortError';

                if (stopped || wasIntentionalAbort) {
                    // Either component cleanup/incident change already stopped us,
                    // or this abort was the intentional one we issued ourselves
                    // (e.g. a fresh connectOnce() aborting a stale controller).
                    // Neither case should be treated as a failure requiring a
                    // reconnect from here.
                    return;
                }

                setStatus('disconnected');
                scheduleReconnect();
            }
        };

        const scheduleReconnect = () => {
            if (stopped) {
                return;
            }

            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
            }

            reconnectTimer = setTimeout(() => {
                if (!stopped) {
                    void connectOnce();
                }
            }, RECONNECT_DELAY_MS);
        };

        void connectOnce();

        return () => {
            stopped = true;

            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }

            if (abortRef.current) {
                abortRef.current.abort();
                abortRef.current = null;
            }
        };
    }, [incidentId]);

    return { events, status };
}

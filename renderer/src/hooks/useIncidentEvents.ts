import { useEffect, useRef, useState } from 'react';
import { authFetch } from '../contexts/AuthGateContext';

export interface AgentEvent {
    id: string;
    timestamp: string;
    source: string;
    event_type: string;
    message: string;
}

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
 */
export function useIncidentEvents(incidentId: string | null) {
    const [events, setEvents] = useState<AgentEvent[]>([]);
    const [status, setStatus] = useState<
        'connecting' | 'connected' | 'disconnected'
    >('disconnected');

    const abortRef = useRef<AbortController | null>(null);

    useEffect(() => {
        if (abortRef.current) {
            abortRef.current.abort();
            abortRef.current = null;
        }

        setEvents([]);

        if (!incidentId) {
            setStatus('disconnected');
            return;
        }

        const controller = new AbortController();
        abortRef.current = controller;

        let stopped = false;

        const connect = async () => {
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

                if (!stopped) {
                    setStatus('disconnected');
                }
            } catch (error) {
                if (
                    !stopped &&
                    !(error instanceof DOMException &&
                        error.name === 'AbortError')
                ) {
                    setStatus('disconnected');
                }
            }
        };

        void connect();

        return () => {
            stopped = true;
            controller.abort();

            if (abortRef.current === controller) {
                abortRef.current = null;
            }
        };
    }, [incidentId]);

    return { events, status };
}
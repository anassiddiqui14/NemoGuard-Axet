import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';

interface AuthGateContextValue {
    devLoginEnabled: boolean;
    credentialLoginEnabled: boolean;
    loading: boolean;
    refresh: () => void;
}

const NEMOGUARD_TOKEN_KEY = 'nemoguard_token';
const AXET_TOKEN_KEY = 'access_token';

const AuthGateContext = createContext<AuthGateContextValue>({
    devLoginEnabled: false,
    credentialLoginEnabled: false,
    loading: true,
    refresh: () => { },
});

/**
 * aXet.flows injects AXET_CONFIG when the SPA is served.
 * If flowsBaseUrl exists, we are running inside the Flows runtime.
 */
function getAxetFlowsBaseUrl(): string {
    if (typeof window === 'undefined') {
        return '';
    }

    const value = (window as any).AXET_CONFIG?.flowsBaseUrl;

    return typeof value === 'string'
        ? value.trim().replace(/\/+$/, '')
        : '';
}

export function isAxetFlowsRuntime(): boolean {
    return !!getAxetFlowsBaseUrl();
}

/**
 * NemoGuard internally exposes /api/v2/*.
 *
 * In aXet.flows the public gateway is:
 *
 *   /backend/api/*
 *
 * and the python-gateway rewrites that prefix to:
 *
 *   /api/v2/*
 *
 * Therefore:
 *
 *   /api/v2/incidents
 *
 * becomes:
 *
 *   <flowsBaseUrl>/backend/api/incidents
 */
export function resolveApiUrl(url: string): string {
    const flowsBaseUrl = getAxetFlowsBaseUrl();

    if (!flowsBaseUrl) {
        return url;
    }

    if (url.startsWith('/api/v2')) {
        const suffix = url.slice('/api/v2'.length);
        return `${flowsBaseUrl}/backend/api${suffix}`;
    }

    return url;
}

/**
 * Reads the aXet access_token using the same contract as the aXet seed.
 *
 * The value may be:
 *   - a raw JWT
 *   - JSON containing access_token
 */
function getAxetAccessToken(): string {
    if (typeof window === 'undefined') {
        return '';
    }

    const raw = window.localStorage.getItem(AXET_TOKEN_KEY);

    if (!raw) {
        return '';
    }

    if (raw.trim().startsWith('{')) {
        try {
            const parsed = JSON.parse(raw);
            return parsed?.access_token ?? '';
        } catch {
            return raw;
        }
    }

    const payload = decodeJwtPayload(raw);

    return payload?.access_token ?? raw;
}

/**
 * Returns the token appropriate for the current runtime.
 *
 * aXet.flows:
 *   localStorage.access_token
 *
 * Existing standalone/local NemoGuard:
 *   localStorage.nemoguard_token
 */
export function getCurrentAuthToken(): string {
    if (isAxetFlowsRuntime()) {
        return getAxetAccessToken();
    }

    if (typeof window === 'undefined') {
        return '';
    }

    return window.localStorage.getItem(NEMOGUARD_TOKEN_KEY) || '';
}

export function AuthGateProvider({ children }: { children: React.ReactNode }) {
    const [devLoginEnabled, setDevLoginEnabled] = useState(false);
    const [credentialLoginEnabled, setCredentialLoginEnabled] = useState(false);
    const [loading, setLoading] = useState(true);

    const load = useCallback(() => {
        setLoading(true);

        fetch(resolveApiUrl('/api/v2/auth/config'))
            .then((r) =>
                r.ok
                    ? r.json()
                    : {
                        dev_login_enabled: false,
                        credential_login_enabled: false,
                    }
            )
            .then((data) => {
                setDevLoginEnabled(!!data.dev_login_enabled);
                setCredentialLoginEnabled(!!data.credential_login_enabled);
            })
            .catch(() => {
                setDevLoginEnabled(false);
                setCredentialLoginEnabled(false);
            })
            .finally(() => setLoading(false));
    }, []);

    useEffect(() => {
        load();
    }, [load]);

    return (
        <AuthGateContext.Provider
            value={{
                devLoginEnabled,
                credentialLoginEnabled,
                loading,
                refresh: load,
            }}
        >
            {children}
        </AuthGateContext.Provider>
    );
}

export function useAuthGate() {
    return useContext(AuthGateContext);
}

/**
 * Decode JWT payload for frontend expiry inspection only.
 * Authentication/authorization remains a backend responsibility.
 */
function decodeJwtPayload(token: string): any | null {
    try {
        const parts = token.split('.');

        if (parts.length < 2) {
            return null;
        }

        const payload = parts[1]
            .replace(/-/g, '+')
            .replace(/_/g, '/');

        const padded =
            payload +
            '='.repeat((4 - (payload.length % 4)) % 4);

        return JSON.parse(atob(padded));
    } catch {
        return null;
    }
}

function isTokenValid(token: string): boolean {
    const payload = decodeJwtPayload(token);

    if (!payload || typeof payload.exp !== 'number') {
        return false;
    }

    const nowSeconds = Date.now() / 1000;

    return payload.exp - 30 > nowSeconds;
}

/**
 * Existing NemoGuard development authentication.
 *
 * This is intentionally disabled when running inside aXet.flows because
 * aXet/Okta already owns authentication there.
 */
export async function acquireDemoToken(
    role: string = 'commander'
): Promise<string | null> {
    if (isAxetFlowsRuntime()) {
        return null;
    }

    const existing =
        localStorage.getItem(NEMOGUARD_TOKEN_KEY);

    if (existing && isTokenValid(existing)) {
        return existing;
    }

    if (existing) {
        localStorage.removeItem(NEMOGUARD_TOKEN_KEY);
    }

    try {
        const res = await fetch(
            resolveApiUrl(
                `/api/v2/auth/mock-login?role=${encodeURIComponent(role)}`
            )
        );

        if (res.ok) {
            const data = await res.json();

            localStorage.setItem(
                NEMOGUARD_TOKEN_KEY,
                data.access_token
            );

            return data.access_token;
        }
    } catch {
        // Ignore development mock-login failure.
    }

    return null;
}

/**
 * Central authenticated HTTP wrapper.
 *
 * Local/standalone NemoGuard:
 *   /api/v2/... + nemoguard_token
 *
 * aXet.flows:
 *   <flowsBaseUrl>/backend/api/... + access_token
 */
export async function authFetch(
    url: string,
    init: RequestInit = {}
): Promise<Response> {
    const axetFlows = isAxetFlowsRuntime();
    const resolvedUrl = resolveApiUrl(url);

    const attempt = async (token: string) => {
        const headers = new Headers(init.headers);

        if (token) {
            headers.set('Authorization', `Bearer ${token}`);
        }

        return fetch(resolvedUrl, {
            ...init,
            headers,
        });
    };

    let token = getCurrentAuthToken();

    /**
     * Only NemoGuard's standalone/local mode is allowed to mint a
     * replacement development token.
     *
     * In Flows, token lifecycle belongs to aXet/Okta.
     */
    if (!axetFlows && (!token || !isTokenValid(token))) {
        const refreshed = await acquireDemoToken();

        if (refreshed) {
            token = refreshed;
        }
    }

    let res = await attempt(token);

    /**
     * Existing NemoGuard refresh behaviour remains for local/dev.
     *
     * In aXet.flows we must NOT call mock-login when the corporate
     * token expires or is rejected.
     */
    if (res.status === 401 && !axetFlows) {
        localStorage.removeItem(NEMOGUARD_TOKEN_KEY);

        const refreshed = await acquireDemoToken();

        if (refreshed) {
            res = await attempt(refreshed);
        }
    }

    return res;
}
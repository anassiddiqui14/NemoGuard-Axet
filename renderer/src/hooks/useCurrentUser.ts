import { useEffect, useState } from 'react';
import { authFetch, isAxetFlowsRuntime } from '../contexts/AuthGateContext';

// 'error' (new): the /auth/me call itself failed for a reason that does
// NOT constitute proof the session is invalid -- a transient 5xx/429/
// unexpected-non-2xx response, or a network/fetch exception. Previously
// these were indistinguishable from a genuine 401/403 (both fell into the
// same `if (!response.ok)` branch and were both cached as
// UNAUTHENTICATED_USER), which meant a momentary backend hiccup on the
// very first /auth/me call would make a genuinely authenticated user see
// "Signed out" and have that false negative CACHED, persisting until a
// full page reload. 'error' is strictly a "we don't know yet / verification
// failed transiently" signal -- consumers must never render it as if it
// were a confirmed sign-out.
export type UserStatus = 'loading' | 'authenticated' | 'unauthenticated' | 'error';

export interface CurrentUser {
    email: string;
    roles: string[];
    tenantId: string;
    workspaceId: string;
    displayName: string;
    initials: string;
    status: UserStatus;
}

function decodeJwtPayload(token: string): any | null {
    try {
        const parts = token.split('.');
        if (parts.length < 2) return null;
        const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
        const padded = payload + '='.repeat((4 - (payload.length % 4)) % 4);
        const json = atob(padded);
        return JSON.parse(json);
    } catch {
        return null;
    }
}

function displayNameFromEmail(email: string): string {
    const local = email.split('@')[0] || email;
    return (
        local
            .split(/[._\-]+/)
            .filter(Boolean)
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join(' ') || email
    );
}

function buildUser(payload: any): CurrentUser {
    const email: string = payload?.email || 'unknown@nemoguard';
    const roles: string[] = payload?.roles || ['viewer'];
    const tenantId: string = payload?.tenant_id || 'default_tenant';
    const workspaceId: string = payload?.workspace_id || 'default_workspace';
    // Prefer the account's real, configured display_name (now embedded in
    // the JWT by src/api/auth.py's issue_token_for_user) over deriving one
    // from the email's local-part -- previously the JWT never carried
    // display_name at all, so any admin renamed via the DB or
    // scripts/create_user.py --name had that rename silently ignored here.
    const displayName: string = payload?.display_name || displayNameFromEmail(email);
    const initials =
        displayName
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((p) => p[0]?.toUpperCase())
            .join('') || 'U';

    return { email, roles, tenantId, workspaceId, displayName, initials, status: 'authenticated' };
}

const LOADING_USER: CurrentUser = {
    email: '',
    roles: ['viewer'],
    tenantId: 'default_tenant',
    workspaceId: 'default_workspace',
    displayName: '',
    initials: '',
    status: 'loading',
};

const UNAUTHENTICATED_USER: CurrentUser = {
    email: '',
    roles: ['viewer'],
    tenantId: 'default_tenant',
    workspaceId: 'default_workspace',
    displayName: 'Signed out',
    initials: '—',
    status: 'unauthenticated',
};

// Neutral "we couldn't verify the session right now, but we have no
// reason to believe it's invalid" representation. roles defaults to
// ['viewer'] (fail-closed for any role-gated UI, e.g. useNavItems' Admin
// link) without asserting the user is signed out.
const ERROR_USER: CurrentUser = {
    email: '',
    roles: ['viewer'],
    tenantId: 'default_tenant',
    workspaceId: 'default_workspace',
    displayName: 'Profile unavailable',
    initials: '?',
    status: 'error',
};

// Phase 13: module-level cache shared across every useCurrentUser() call
// within the same page session. The FIRST consumer to resolve populates
// it, and any consumer that mounts/remounts afterward (e.g. GreetingBar
// remounting because the operator navigated away from and back to the
// Dashboard route) can render the already-known user immediately instead
// of flashing back to a loading/unauthenticated placeholder while a
// redundant network round-trip completes.
//
// Only a genuinely confirmed result -- 'authenticated' or
// 'unauthenticated' -- is ever written here. 'loading' and 'error' are
// deliberately NEVER cached: caching 'error' would let a single
// transient hiccup permanently poison every subsequent mount in this
// page session (identical to the bug this fix addresses), and caching
// 'loading' would simply be a no-op placeholder with no value.
let _cachedUser: CurrentUser | null = null;
let _inFlightFetch: Promise<CurrentUser> | null = null;

async function _fetchAxetUser(): Promise<CurrentUser> {
    let response: Response;
    try {
        response = await authFetch('/api/v2/auth/me');
    } catch {
        // Network failure, timeout, DNS error, etc. -- never proof the
        // session is invalid. Surface as 'error', not 'unauthenticated'.
        return ERROR_USER;
    }

    // Only a genuine authentication/authorization rejection from the
    // backend establishes "unauthenticated". Every other non-2xx status
    // (5xx, 429, or any other unexpected code) is a transient
    // verification failure, not evidence the session is invalid, and
    // must not be treated -- or cached -- as a confirmed sign-out.
    if (response.status === 401 || response.status === 403) {
        return UNAUTHENTICATED_USER;
    }
    if (!response.ok) {
        return ERROR_USER;
    }

    try {
        const payload = await response.json();
        return buildUser(payload);
    } catch {
        // 2xx but an unparseable body -- also a transient/unexpected
        // condition, not proof of an invalid session.
        return ERROR_USER;
    }
}

export function useCurrentUser(): CurrentUser {
    const [user, setUser] = useState<CurrentUser>(() => _cachedUser ?? LOADING_USER);

    useEffect(() => {
        if (isAxetFlowsRuntime()) {
            let cancelled = false;

            // If another mount already has a confirmed result cached,
            // reflect it immediately -- no redundant fetch, no flicker.
            if (_cachedUser) {
                setUser(_cachedUser);
                return;
            }

            // Coalesce concurrent mounts (e.g. GreetingBar and UserMenu
            // both mounting around the same time) into a single shared
            // /auth/me request rather than firing one per consumer.
            if (!_inFlightFetch) {
                _inFlightFetch = _fetchAxetUser().finally(() => {
                    _inFlightFetch = null;
                });
            }

            void _inFlightFetch.then((resolved) => {
                // Only a confirmed result is cached (see comment on
                // _cachedUser above) -- a transient 'error' never
                // overwrites a cache slot that might later hold a real
                // authenticated/unauthenticated result, and is never
                // itself treated as if it were confirmed.
                if (resolved.status === 'authenticated' || resolved.status === 'unauthenticated') {
                    _cachedUser = resolved;
                }
                if (!cancelled) {
                    // If THIS mount already had a cached authenticated
                    // user by the time this resolves (e.g. another
                    // concurrent mount's fetch populated the cache first
                    // with a real result while this one was still
                    // in-flight), prefer that confirmed result over a
                    // late-arriving 'error' from this call -- a
                    // transient failure must never downgrade an already-
                    // known-good identity.
                    if (resolved.status === 'error' && _cachedUser) {
                        setUser(_cachedUser);
                    } else {
                        setUser(resolved);
                    }
                }
            });

            return () => {
                cancelled = true;
            };
        }

        const readUser = () => {
            const token = localStorage.getItem('nemoguard_token');

            if (!token) {
                setUser(UNAUTHENTICATED_USER);
                return;
            }

            const payload = decodeJwtPayload(token);
            setUser(payload ? buildUser(payload) : UNAUTHENTICATED_USER);
        };

        readUser();
        window.addEventListener('storage', readUser);

        return () => window.removeEventListener('storage', readUser);
    }, []);

    return user;
}

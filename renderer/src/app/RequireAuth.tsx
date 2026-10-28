import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import {
    useAuthGate,
    acquireDemoToken,
    getCurrentAuthToken,
    isAxetFlowsRuntime,
} from '../contexts/AuthGateContext';
import { BrandMark } from '../design-system/components/BrandMark';

export function RequireAuth({ children }: { children: React.ReactNode }) {
    const { devLoginEnabled, loading: authGateLoading } = useAuthGate();
    const [ready, setReady] = useState(false);
    const [hasToken, setHasToken] = useState<boolean>(() => !!getCurrentAuthToken());

    useEffect(() => {
    if (authGateLoading) return;

    // In aXet Flows, authentication is owned by the platform.
    // The current aXet runtime uses its HttpOnly session/token cookies,
    // so the frontend must not require localStorage.access_token.
    // Protected backend APIs still enforce the authenticated aXet identity.
    if (isAxetFlowsRuntime()) {
        setHasToken(true);
        setReady(true);
        return;
    }

    if (hasToken) {
        setReady(true);
        return;
    }

    if (devLoginEnabled) {
        void acquireDemoToken().then((token) => {
            setHasToken(!!token);
            setReady(true);
        });
    } else {
        setReady(true);
    }
}, [devLoginEnabled, authGateLoading, hasToken]);
    // UI MODERNIZATION (spec Section 75): previously rendered nothing at
    // all during this brief auth-check window, producing a jarring blank
    // flash before the real app shell mounted. Shows the signature brand
    // mark instead -- "idle" state, since nothing agentic is actually
    // happening yet, just a routine token check.
    if (!ready || authGateLoading) {
        return (
            <div className="h-screen w-full flex items-center justify-center bg-app-bg">
                <BrandMark state="idle" size="lg" />
            </div>
        );
    }

    if (!hasToken) {
        return <Navigate to="/login" replace />;
    }

    return <>{children}</>;
}

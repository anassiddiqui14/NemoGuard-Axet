import { useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import { Logomark } from '../LandingPage/Brand';
import { CapabilityPolicyTab } from './CapabilityPolicyTab';
import { KnowledgeReview } from './KnowledgeReview';
import { KnowledgeLibrary } from './KnowledgeLibrary';

/**
 * V22 admin intentionally exposes only controls that have a matching,
 * enforced backend contract in this repository.  Earlier iterations
 * advertised Teams Routes, Incident Memory, False Resolutions, Model
 * Settings and Model Evaluation, but their FastAPI endpoints were not
 * present in the axet-migration codebase.  Showing them as working admin
 * controls would be misleading in a leadership demo and frustrating in
 * normal use, so they remain code-level future surfaces until the server
 * contracts exist.
 */
export function AdminPage() {
    const [reviewRevision, setReviewRevision] = useState(0);
    const [libraryRevision, setLibraryRevision] = useState(0);

    return (
        <div className="v22-canvas h-full overflow-y-auto p-5 sm:p-7">
            <div className="mx-auto max-w-5xl space-y-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <div className="flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.16em] text-[#0C6F58]">
                            <Logomark className="h-4 w-4" /> Governance
                        </div>
                        <h1 className="mt-2 text-[28px] font-bold tracking-[-0.045em] text-[#13231E]">Capability Policy</h1>
                        <p className="mt-1 max-w-2xl text-[12.5px] leading-relaxed text-[#62716B]">
                            The active control surface for what NemoGuard is allowed to execute, recommend, or deny. Every control below is backed by a real API contract.
                        </p>
                    </div>
                    <div className="flex items-center gap-2 rounded-2xl border border-[#0C6F58]/15 bg-[#E1F3EC] px-3 py-2 text-[10.5px] font-bold text-[#0C6F58]">
                        <ShieldCheck size={14} /> Enforced backend policy
                    </div>
                </div>

                <div className="rounded-[24px] border border-[#0F3F32]/10 bg-[#FCFDFB] p-4 shadow-[0_18px_45px_-34px_rgba(7,63,50,.26)] sm:p-5">
                    <CapabilityPolicyTab />
                </div>

                <KnowledgeReview refreshRevision={reviewRevision} onChanged={() => setLibraryRevision((value) => value + 1)} />
                <KnowledgeLibrary refreshRevision={libraryRevision} onChanged={() => setReviewRevision((value) => value + 1)} />
            </div>
        </div>
    );
}

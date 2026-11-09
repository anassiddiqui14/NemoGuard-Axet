import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useNavItems } from '../../hooks/useNavItems';
import { Logomark } from '../../pages/LandingPage/Brand';

const NAV_COLLAPSED_KEY = 'nemoguard_nav_collapsed';

/** Persistent desktop navigation; smaller screens use MobileNavDrawer. */
export function GlobalNavRail() {
    const items = useNavItems();
    const [collapsed, setCollapsed] = useState(() => {
        try {
            return localStorage.getItem(NAV_COLLAPSED_KEY) === '1';
        } catch {
            return false;
        }
    });

    const toggleCollapsed = () => {
        setCollapsed((previous) => {
            const next = !previous;
            try {
                localStorage.setItem(NAV_COLLAPSED_KEY, next ? '1' : '0');
            } catch {
                // Navigation remains usable when browser storage is unavailable.
            }
            return next;
        });
    };

    return (
        <aside
            className={`relative hidden md:flex ${collapsed ? 'w-[72px]' : 'w-[208px] lg:w-[224px]'} flex-col flex-shrink-0 h-full border-r border-white/10 bg-[#073F32] transition-[width] duration-200 ease-out motion-reduce:transition-none`}
        >
            <div className={`h-12 flex items-end pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-white/70 ${collapsed ? 'justify-center' : 'px-5'}`}>
                {collapsed ? <Logomark className="h-5 w-5 text-[#8FE4C9]" /> : 'Workspace'}
            </div>
            <nav id="desktop-primary-navigation" aria-label="Primary navigation" className="flex-1 overflow-y-auto px-2.5 pb-4">
                <ul className="space-y-1">
                    {items.map((item) => (
                        <li key={item.to}>
                            <NavLink
                                to={item.to}
                                end={item.end}
                                title={collapsed ? item.label : undefined}
                                className={({ isActive }) =>
                                    `relative isolate flex min-h-11 w-full items-center gap-3 rounded-xl px-3 py-2.5 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[#8FE4C9] ${collapsed ? 'justify-center' : ''} ${isActive
                                        ? 'text-[#073F32]'
                                        : 'text-white/80 hover:bg-white/10 hover:text-white'
                                    }`
                                }
                            >
                                {({ isActive }) => (
                                    <>
                                        {isActive && (
                                            <motion.span
                                                layoutId="nav-active-rail"
                                                transition={{ type: 'spring', stiffness: 430, damping: 34 }}
                                                className="absolute inset-0 -z-10 rounded-xl bg-[#E1F3EC] shadow-[0_10px_22px_-16px_rgba(0,0,0,0.65)]"
                                            />
                                        )}
                                        <span aria-hidden="true" className="flex-shrink-0">{item.icon}</span>
                                        <span className={collapsed ? 'sr-only' : 'text-[13px] font-medium leading-tight'}>{item.label}</span>
                                    </>
                                )}
                            </NavLink>
                        </li>
                    ))}
                </ul>
            </nav>
            <button
                type="button"
                onClick={toggleCollapsed}
                aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
                aria-expanded={!collapsed}
                aria-controls="desktop-primary-navigation"
                title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
                className="absolute right-0 top-1/2 z-30 flex h-9 w-9 translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-[#C1D8CD] bg-[#FCFDFB] text-[#073F32] shadow-[0_4px_16px_rgba(7,63,50,0.18)] transition-colors hover:bg-[#E1F3EC] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0C6F58]"
            >
                {collapsed ? <ChevronRight aria-hidden="true" size={18} /> : <ChevronLeft aria-hidden="true" size={18} />}
            </button>
        </aside>
    );
}

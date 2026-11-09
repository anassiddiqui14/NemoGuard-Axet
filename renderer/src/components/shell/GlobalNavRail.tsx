import { NavLink } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useNavItems } from '../../hooks/useNavItems';

/** Persistent, labeled desktop navigation; smaller screens use MobileNavDrawer. */
export function GlobalNavRail() {
    const items = useNavItems();

    return (
        <aside className="hidden md:flex w-[208px] lg:w-[224px] flex-col flex-shrink-0 h-full border-r border-white/10 bg-[#073F32]">
            <div className="px-5 pt-5 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-white/70">
                Workspace
            </div>
            <nav aria-label="Primary navigation" className="flex-1 overflow-y-auto px-2.5 pb-4">
                <ul className="space-y-1">
                    {items.map((item) => (
                        <li key={item.to}>
                            <NavLink
                                to={item.to}
                                end={item.end}
                                className={({ isActive }) =>
                                    `relative isolate flex min-h-11 w-full items-center gap-3 rounded-xl px-3 py-2.5 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[#8FE4C9] ${isActive
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
                                        <span className="text-[13px] font-medium leading-tight">{item.label}</span>
                                    </>
                                )}
                            </NavLink>
                        </li>
                    ))}
                </ul>
            </nav>
        </aside>
    );
}

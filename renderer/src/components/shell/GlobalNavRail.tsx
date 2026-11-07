import { NavLink } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useNavItems } from '../../hooks/useNavItems';
import { Tooltip } from '../../design-system/components/Tooltip';

interface Props {
    collapsed: boolean;
}

/**
 * A persistent, collapsible sidebar rail -- always visible on desktop
 * rather than a hidden overlay drawer that required an extra click to open
 * before every navigation. Collapsing (via the toggle button in TopBar)
 * shrinks it down to an icon-only rail instead of hiding it completely, so
 * the current section is always visible at a glance and switching sections
 * never costs more than a single click.
 *
 * Icons are the bespoke NemoGuard glyph set (NemoIcons.tsx) that shares the
 * perimeter-watch frame from the primary brand mark, rather than generic
 * icon-library glyphs.
 */
export function GlobalNavRail({ collapsed }: Props) {
    const items = useNavItems();

    return (
        <motion.aside
            initial={false}
            animate={{ width: collapsed ? 74 : 236 }}
            transition={{ type: 'spring', stiffness: 300, damping: 32 }}
            className="hidden md:flex flex-col flex-shrink-0 h-full overflow-hidden border-r border-white/10 bg-[#073F32]"
        >
            <nav className={`flex-1 py-3 space-y-1 overflow-y-auto ${collapsed ? 'px-2' : 'px-2.5'}`}>
                {items.map((item) => {
                    const link = (
                        <NavLink
                            key={item.to}
                            to={item.to}
                            end={item.end}
                            className={({ isActive }) =>
                                `relative isolate flex items-center gap-3 px-3 py-2.5 rounded-2xl transition-colors w-full ${collapsed ? 'justify-center' : ''} ${isActive
                                    ? 'text-[#073F32]'
                                    : 'text-white/65 hover:bg-white/10 hover:text-white'
                                }`
                            }
                        >
                            {({ isActive }) => (
                                <>
                                    {isActive && (
                                        <motion.div
                                            layoutId="nav-active-rail"
                                            transition={{ type: 'spring', stiffness: 430, damping: 34 }}
                                            className="absolute inset-0 -z-10 rounded-2xl bg-[#E1F3EC] shadow-[0_10px_22px_-16px_rgba(0,0,0,0.65)]"
                                        />
                                    )}
                                    <span className="flex-shrink-0">{item.icon}</span>
                                    {!collapsed && <span className="text-[13px] font-medium whitespace-nowrap">{item.label}</span>}
                                </>
                            )}
                        </NavLink>
                    );
                    // UI MODERNIZATION (Phase 18, spec Section 73): collapsed
                    // rail icons previously relied solely on the native HTML
                    // `title` attribute -- the browser's own slow (~1s delay),
                    // unstyled, cross-browser-inconsistent tooltip. Wrapped in
                    // the themed Tooltip primitive instead, only when
                    // collapsed (expanded state already shows the label
                    // inline, so a tooltip there would be redundant).
                    return collapsed ? (
                        <Tooltip key={item.to} label={item.label} side="right">
                            {link}
                        </Tooltip>
                    ) : (
                        link
                    );
                })}
            </nav>
        </motion.aside>
    );
}

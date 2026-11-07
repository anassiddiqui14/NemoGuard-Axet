import type { HTMLAttributes, ReactNode } from 'react';
import { motion } from 'framer-motion';

export function Surface({
    children,
    className = '',
    interactive = false,
    ...props
}: HTMLAttributes<HTMLElement> & { children: ReactNode; interactive?: boolean }) {
    return (
        <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
            whileHover={interactive ? { y: -2 } : undefined}
            className={`v22-card ${interactive ? 'v22-interactive' : ''} ${className}`}
            {...(props as any)}
        >
            {children}
        </motion.section>
    );
}

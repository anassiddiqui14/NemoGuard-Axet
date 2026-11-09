import type { HTMLAttributes, ReactNode } from 'react';
import { motion } from 'framer-motion';

export function Surface({
    children,
    className = '',
    interactive = false,
    variant = 'default',
    ...props
}: HTMLAttributes<HTMLElement> & { children: ReactNode; interactive?: boolean; variant?: 'default' | 'brand' }) {
    return (
        <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
            whileHover={interactive ? { y: -2 } : undefined}
            className={`v22-card ${variant === 'brand' ? 'v22-card-brand' : ''} ${interactive ? 'v22-interactive' : ''} ${className}`}
            {...(props as any)}
        >
            {children}
        </motion.section>
    );
}

import type { ReactNode } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faCircleCheck, faCircleExclamation, faCircleInfo, faTriangleExclamation, faXmark, type IconDefinition,
} from '@fortawesome/free-solid-svg-icons';
import { IconButton } from './IconButton';
import c from './Feedback.module.css';

export interface BannerProps {
  tone: 'warning' | 'danger' | 'info' | 'success' | 'neutral';
  icon?: IconDefinition;
  title: string;
  message?: ReactNode;
  /** Link or button on the right. */
  action?: ReactNode;
  onDismiss?: () => void;
  className?: string;
}

const DEFAULT_ICON: Record<BannerProps['tone'], IconDefinition> = {
  warning: faTriangleExclamation,
  danger: faCircleExclamation,
  info: faCircleInfo,
  success: faCircleCheck,
  neutral: faCircleInfo,
};

/**
 * Attention item / inline notice (DESIGN.md 7.4): surface card with a 3px tone
 * edge and a tone icon tile. Text never sits on the tone colour itself.
 */
export function Banner({ tone, icon, title, message, action, onDismiss, className }: BannerProps) {
  return (
    <div
      className={[c.banner, className].filter(Boolean).join(' ')}
      data-tone={tone}
      role={tone === 'danger' ? 'alert' : 'status'}
    >
      <span className={c.bannerIcon} aria-hidden="true"><FontAwesomeIcon icon={icon ?? DEFAULT_ICON[tone]} /></span>
      <div className={c.bannerText}>
        <span className={c.bannerTitle}>{title}</span>
        {message && <span className={c.bannerMessage}>{message}</span>}
      </div>
      {action && <div className={c.bannerAction}>{action}</div>}
      {onDismiss && (
        <div className={c.bannerDismiss}>
          <IconButton icon={faXmark} label="Dismiss" size="md" onClick={onDismiss} />
        </div>
      )}
    </div>
  );
}

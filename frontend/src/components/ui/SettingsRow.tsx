import type { ReactNode } from 'react';
import c from './Forms.module.css';

export interface SettingsRowProps {
  label: string;
  description?: ReactNode;
  /** id of the control, so the label is clickable and named. */
  htmlFor?: string;
  control: ReactNode;
  /** "from profile Default", "override". */
  source?: string;
  className?: string;
}

/** Label left, control right (DESIGN.md 7.8); stacks below 768px. */
export function SettingsRow({ label, description, htmlFor, control, source, className }: SettingsRowProps) {
  const descId = htmlFor && description ? `${htmlFor}-desc` : undefined;
  return (
    <div className={[c.row, className].filter(Boolean).join(' ')}>
      <div className={c.text}>
        {htmlFor
          ? <label htmlFor={htmlFor} className={c.label}>{label}</label>
          : <span className={c.label}>{label}</span>}
        {description && <div id={descId} className={c.description}>{description}</div>}
      </div>
      <div className={c.control}>
        {control}
        {source && <span className={c.source}>{source}</span>}
      </div>
    </div>
  );
}

/** Overline group title inside a settings panel ("DOWNLOADS"). */
export function SettingsGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className={c.group}>
      <div className={`ly-overline ${c.groupTitle}`}>{title}</div>
      {children}
    </div>
  );
}

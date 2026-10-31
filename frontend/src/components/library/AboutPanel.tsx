import { useState } from 'react';
import { Panel } from '../ui';
import c from './AboutPanel.module.css';

/** Overviews longer than this get a 5-line clamp and "Read more". */
const LONG_TEXT = 320;

/** "About" side panel for artist and album overviews (DESIGN.md 8.3, 8.5). */
export function AboutPanel({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > LONG_TEXT;
  return (
    <Panel title="About" headingLevel={3}>
      <p className={c.about} data-clamped={long && !open ? true : undefined}>{text}</p>
      {long && (
        <button type="button" className={`ly-link ${c.readMore}`} aria-expanded={open} onClick={() => setOpen(o => !o)}>
          {open ? 'Show less' : 'Read more'}
        </button>
      )}
    </Panel>
  );
}

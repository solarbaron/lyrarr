import { Fragment } from 'react';
import { Kbd, Modal } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { SHORTCUTS, SHORTCUT_SCOPES } from '../../shortcuts';
import c from './ShortcutSheet.module.css';

/** Renders "Ctrl K", "g a", "j / k" as Kbd keys with plain separators. */
function Keys({ keys }: { keys: string }) {
  const tokens = keys.split(' ').filter(Boolean);
  return (
    <span className={c.keys}>
      {tokens.map((t, i) => (
        <Fragment key={i}>
          {t === '/' || t === '–'
            ? <span className={c.sep}>{t === '/' && i > 0 ? 'or' : t}</span>
            : <Kbd>{t}</Kbd>}
        </Fragment>
      ))}
    </span>
  );
}

/** The `?` sheet: every shortcut from shortcuts.ts, grouped by scope (DESIGN.md 6.4). */
export function ShortcutSheet({ opened, onClose }: { opened: boolean; onClose(): void }) {
  const mobile = useMediaQuery('(max-width: 767px)', false, { getInitialValueInEffect: false });
  // "/" (palette) is shown on the Ctrl K row.
  const list = SHORTCUTS.filter(s => s.id !== 'palette-slash');
  return (
    <Modal opened={opened} onClose={onClose} title="Keyboard shortcuts" size={640} fullScreen={mobile}>
      <div className={c.grid}>
        {SHORTCUT_SCOPES.map(({ scope, title }) => {
          const items = list.filter(s => s.scope === scope);
          if (!items.length) return null;
          return (
            <section key={scope} className={c.section}>
              <h3 className={c.title}>{title}</h3>
              <dl className={c.list}>
                {items.map(s => (
                  <div key={s.id} className={c.row}>
                    <dt className={c.label}>{s.label}</dt>
                    <dd className={c.value}>
                      <Keys keys={s.keys} />
                      {s.id === 'palette' && <><span className={c.sep}>or</span><Kbd>/</Kbd></>}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          );
        })}
      </div>
      <p className={c.note}>Single-key shortcuts never fire while you are typing in a field.</p>
    </Modal>
  );
}

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react';
import { Button, Group, Modal, TextInput } from '@mantine/core';
import c from './Dialogs.module.css';

export interface ConfirmOptions {
  title: string;
  /** Restate the object: "Skip lyrics for Noid? …". */
  message: ReactNode;
  confirmLabel: string;
  /** danger: filled danger confirm button. */
  tone?: 'default' | 'danger';
  /** Type this text to enable the confirm button (big destructive batches). */
  requireText?: string;
  cancelLabel?: string;
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

interface Pending extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

/**
 * One confirm dialog for the whole app (DESIGN.md 7.9). Mount once near the root;
 * `useConfirm()` returns an async confirm(): `if (await confirm({...})) mutate()`.
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [opened, setOpened] = useState(false);
  const [typed, setTyped] = useState('');
  const current = useRef<Pending | null>(null);

  const confirm = useCallback<ConfirmFn>(opts => new Promise<boolean>(resolve => {
    current.current?.resolve(false); // a newer confirm replaces an open one
    const next = { ...opts, resolve };
    current.current = next;
    setTyped('');
    setPending(next);
    setOpened(true);
  }), []);

  const finish = (ok: boolean) => {
    current.current?.resolve(ok);
    current.current = null;
    setOpened(false);
  };

  const blocked = !!pending?.requireText && typed.trim() !== pending.requireText;
  const submit = () => {
    if (!blocked) finish(true);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal
        opened={opened}
        onClose={() => finish(false)}
        title={pending?.title}
        size={440}
        onExitTransitionEnd={() => { if (!current.current) setPending(null); }}
      >
        {pending && (
          <form
            onSubmit={e => { e.preventDefault(); submit(); }}
            onKeyDown={e => {
              if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                submit();
              }
            }}
          >
            <div className={c.message}>{pending.message}</div>
            {pending.requireText && (
              <TextInput
                className={c.require}
                label={<>Type <strong className="num">{pending.requireText}</strong> to confirm</>}
                value={typed}
                onChange={e => setTyped(e.currentTarget.value)}
                autoComplete="off"
                data-autofocus
              />
            )}
            <Group justify="flex-end" gap="xs" className={c.footer}>
              {/* Destructive dialogs start on Cancel so a stray Enter can't confirm
                  (Ctrl/Cmd Enter still does); others start on the confirm button. */}
              <Button
                variant="subtle"
                color="gray"
                onClick={() => finish(false)}
                data-autofocus={!pending.requireText && pending.tone === 'danger' ? true : undefined}
              >
                {pending.cancelLabel ?? 'Cancel'}
              </Button>
              <Button
                type="submit"
                variant="filled"
                color={pending.tone === 'danger' ? 'danger' : undefined}
                disabled={blocked}
                data-autofocus={!pending.requireText && pending.tone !== 'danger' ? true : undefined}
              >
                {pending.confirmLabel}
              </Button>
            </Group>
          </form>
        )}
      </Modal>
    </ConfirmContext.Provider>
  );
}

/**
 * Async confirmation. Outside a ConfirmProvider it falls back to the browser's
 * confirm() (keeps pages working before the shell mounts the provider).
 */
export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  return ctx ?? fallbackConfirm;
}

const fallbackConfirm: ConfirmFn = async ({ title, message, requireText }) => {
  const text = typeof message === 'string' ? `${title}\n\n${message}` : title;
  if (requireText) return window.prompt(`${text}\n\nType ${requireText} to confirm`) === requireText;
  return window.confirm(text);
};

import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button, Collapse, Group, UnstyledButton } from '@mantine/core';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faChevronDown, faChevronRight, faCircleExclamation, faRotate } from '@fortawesome/free-solid-svg-icons';
import { Panel } from './ui';
import c from './ErrorBoundary.module.css';

interface Props {
  children: ReactNode;
  /** Changing this value (e.g. the route path) clears a caught error. */
  resetKey?: unknown;
}

interface State {
  error: Error | null;
  resetKey?: unknown;
  showDetails: boolean;
  copied: boolean;
}

// After an upgrade the browser may still run the old index.js, whose lazy page
// chunks (content-hashed filenames) no longer exist on the server.
function isChunkLoadError(error: Error) {
  return /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i
    .test(error?.message || '');
}

/** "Name: message" plus the stack, without repeating the first line (V8 stacks start with it). */
function detailsText(error: Error): string {
  const head = `${error.name}: ${error.message}`;
  if (!error.stack) return head;
  return error.stack.startsWith(head) ? error.stack : `${head}\n\n${error.stack}`;
}

/**
 * Catches render errors in the page area so one broken page can't blank the
 * whole app (DESIGN.md 8.20): the shell stays usable around it.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey, showDetails: false, copied: false };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error, showDetails: false, copied: false };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    // Navigating elsewhere gives the new page a fresh chance to render.
    if (props.resetKey !== state.resetKey) {
      return { error: null, resetKey: props.resetKey };
    }
    return null;
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Page crashed:', error, info.componentStack);
  }

  private copyDetails = async () => {
    const { error } = this.state;
    if (!error) return;
    const text = [
      `${error.name}: ${error.message}`,
      `URL: ${window.location.href}`,
      error.stack || '',
    ].join('\n');
    try {
      await navigator.clipboard.writeText(text);
      this.setState({ copied: true });
    } catch {
      this.setState({ showDetails: true });
    }
  };

  render() {
    const { error, showDetails, copied } = this.state;
    if (!error) return this.props.children;

    const stale = isChunkLoadError(error);
    return (
      <div className={c.wrap}>
        <Panel className={c.panel}>
          <div role="alert" className={c.body}>
            <span className={c.tile} data-tone={stale ? 'info' : 'danger'} aria-hidden="true">
              <FontAwesomeIcon icon={stale ? faRotate : faCircleExclamation} />
            </span>
            <h1 className={c.title}>
              {stale ? 'Lyrarr was updated' : 'Something went wrong on this page'}
            </h1>
            <p className={c.message}>
              {stale
                ? 'This page could not be loaded because a newer version of Lyrarr is running. Reload to continue.'
                : (error.message || 'This page failed to render.')}
            </p>

            {!stale && (
              <div className={c.details}>
                <UnstyledButton
                  className={c.detailsToggle}
                  onClick={() => this.setState({ showDetails: !showDetails })}
                  aria-expanded={showDetails}
                >
                  <FontAwesomeIcon icon={showDetails ? faChevronDown : faChevronRight} />
                  Details
                </UnstyledButton>
                <Collapse in={showDetails}>
                  <pre className={c.pre}>{detailsText(error)}</pre>
                </Collapse>
              </div>
            )}

            <Group gap="xs" mt="lg">
              <Button variant="filled" onClick={() => window.location.reload()}>Reload</Button>
              {!stale && (
                <Button variant="subtle" color="gray" onClick={() => this.setState({ error: null })}>
                  Try again
                </Button>
              )}
              {!stale && (
                <Button variant="subtle" color="gray" onClick={this.copyDetails}>
                  {copied ? 'Copied' : 'Copy details'}
                </Button>
              )}
            </Group>
          </div>
        </Panel>
      </div>
    );
  }
}

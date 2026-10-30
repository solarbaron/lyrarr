import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button, Group } from '@mantine/core';

interface Props {
  children: ReactNode;
  /** Changing this value (e.g. the route path) clears a caught error. */
  resetKey?: unknown;
}

interface State {
  error: Error | null;
  resetKey?: unknown;
}

// After an upgrade the browser may still run the old index.js, whose lazy page
// chunks (content-hashed filenames) no longer exist on the server.
function isChunkLoadError(error: Error) {
  return /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i
    .test(error?.message || '');
}

/** Catches render errors in the page area so one broken page can't blank the whole app. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
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

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const stale = isChunkLoadError(error);
    return (
      <div className="empty-state" role="alert">
        <div className="empty-state-icon">{stale ? '🔄' : '⚠️'}</div>
        <div className="empty-state-title">
          {stale ? 'Lyrarr was updated' : 'Something went wrong'}
        </div>
        <div className="empty-state-message">
          {stale
            ? 'This page could not be loaded because a newer version of Lyrarr is running. Reload to continue.'
            : (error.message || 'This page failed to render.')}
        </div>
        <Group justify="center" mt="md">
          <Button color="violet" onClick={() => window.location.reload()}>Reload</Button>
          {!stale && (
            <Button variant="subtle" color="gray" onClick={() => this.setState({ error: null })}>
              Try again
            </Button>
          )}
        </Group>
      </div>
    );
  }
}

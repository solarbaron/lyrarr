import { useCallback, useEffect, useRef, useState, type SyntheticEvent } from 'react';
import { notifyError } from '../../notify';

export interface AudioPlayer {
  /** Spread onto an <audio> element rendered for the whole lifetime of the caller. */
  audioProps: {
    ref: (el: HTMLAudioElement | null) => void;
    src: string;
    preload: 'none' | 'metadata';
    onTimeUpdate: (e: SyntheticEvent<HTMLAudioElement>) => void;
    onDurationChange: (e: SyntheticEvent<HTMLAudioElement>) => void;
    onLoadedMetadata: (e: SyntheticEvent<HTMLAudioElement>) => void;
    onPlay: () => void;
    onPause: () => void;
    onEnded: () => void;
    onError: () => void;
  };
  playing: boolean;
  /** Current position in seconds. */
  time: number;
  /** Media duration in seconds (falls back to the known track length before metadata loads). */
  duration: number;
  /** The file could not be loaded (missing on disk, unsupported, 404). */
  error: boolean;
  toggle: () => void;
  pause: () => void;
  seek: (seconds: number) => void;
}

/**
 * State for a custom audio transport (play/pause, scrubber, time). The <audio>
 * element pauses and drops its source when the caller unmounts, so closing a
 * modal or moving the Wanted cursor never leaves audio playing.
 */
export function useAudioPlayer(src: string, opts: { preload?: 'none' | 'metadata'; knownDuration?: number } = {}): AudioPlayer {
  const ref = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [mediaDuration, setMediaDuration] = useState(0);
  const [error, setError] = useState(false);

  // A new source starts from scratch.
  useEffect(() => {
    setPlaying(false);
    setTime(0);
    setMediaDuration(0);
    setError(false);
  }, [src]);

  // Pause when the element unmounts (modal closed, cursor moved): React calls a
  // callback ref with null on detach. Only pause: StrictMode re-attaches refs in
  // development, so the element must stay usable.
  const setRef = useCallback((el: HTMLAudioElement | null) => {
    if (!el) ref.current?.pause();
    ref.current = el;
  }, []);

  const onDuration = useCallback((e: SyntheticEvent<HTMLAudioElement>) => {
    const d = e.currentTarget.duration;
    setMediaDuration(Number.isFinite(d) ? d : 0);
  }, []);

  const toggle = useCallback(() => {
    const el = ref.current;
    if (!el || error) return;
    if (el.paused) {
      el.play().catch((err: DOMException) => {
        // AbortError just means a pause/seek interrupted loading.
        if (err?.name === 'AbortError') return;
        if (err?.name === 'NotSupportedError') {
          setError(true);
          return;
        }
        notifyError('Playback failed', err, 'Could not play this track');
      });
    } else {
      el.pause();
    }
  }, [error]);

  const pause = useCallback(() => { ref.current?.pause(); }, []);

  const seek = useCallback((seconds: number) => {
    const el = ref.current;
    if (!el) return;
    el.currentTime = seconds;
    setTime(seconds);
  }, []);

  return {
    audioProps: {
      ref: setRef,
      src,
      preload: opts.preload ?? 'metadata',
      onTimeUpdate: e => setTime(e.currentTarget.currentTime),
      onDurationChange: onDuration,
      onLoadedMetadata: onDuration,
      onPlay: () => setPlaying(true),
      onPause: () => setPlaying(false),
      onEnded: () => setPlaying(false),
      onError: () => { setPlaying(false); setError(true); },
    },
    playing,
    time,
    duration: mediaDuration || opts.knownDuration || 0,
    error,
    toggle,
    pause,
    seek,
  };
}

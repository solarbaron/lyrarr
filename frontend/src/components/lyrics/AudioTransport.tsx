import { Slider } from '@mantine/core';
import { faPause, faPlay } from '@fortawesome/free-solid-svg-icons';
import { duration as fmtDuration } from '../../format';
import { IconButton } from '../ui';
import type { AudioPlayer } from './useAudioPlayer';
import c from './AudioTransport.module.css';

export interface AudioTransportProps {
  player: AudioPlayer;
  /** Accessible name of the scrubber ("Seek"). */
  label?: string;
  /** Kbd hint for the play button tooltip (e.g. "Space"). */
  shortcut?: string;
  className?: string;
}

/**
 * Play/pause + scrubber + mono time (DESIGN.md 8.6 inspector, 8.13 editor). Render
 * the hook's <audio {...player.audioProps} /> next to it. When the file can't be
 * loaded the transport collapses to a muted "Audio file unavailable" line.
 */
export function AudioTransport({ player, label = 'Seek', shortcut, className }: AudioTransportProps) {
  if (player.error) {
    return (
      <div className={[c.transport, c.unavailable, className].filter(Boolean).join(' ')} role="status">
        Audio file unavailable
      </div>
    );
  }
  const max = Math.max(1, player.duration);
  return (
    <div className={[c.transport, className].filter(Boolean).join(' ')}>
      <IconButton
        icon={player.playing ? faPause : faPlay}
        label={player.playing ? 'Pause' : 'Play'}
        shortcut={shortcut}
        variant="light"
        radius="xl"
        size="md"
        onClick={player.toggle}
      />
      <Slider
        className={c.slider}
        value={Math.min(player.time, max)}
        min={0}
        max={max}
        step={0.1}
        label={null}
        size="sm"
        thumbSize={12}
        onChange={player.seek}
        thumbLabel={label}
        aria-label={label}
      />
      <span className={c.time}>
        {fmtDuration(player.time)}
        <span className={c.timeSep}> / </span>
        {player.duration > 0 ? fmtDuration(player.duration) : '–:––'}
      </span>
    </div>
  );
}

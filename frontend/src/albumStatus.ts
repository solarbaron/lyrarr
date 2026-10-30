// Album lyrics_status is derived server-side from the album's tracks:
// complete / partial / missing / unknown (no tracks with files yet).
const LABELS: Record<string, string> = {
  complete: 'Complete',
  available: 'Complete',  // legacy value from older servers
  partial: 'Partial',
  missing: 'Missing',
  unknown: 'Unknown',
};

const COLORS: Record<string, string> = {
  complete: 'green',
  available: 'green',
  partial: 'yellow',
  missing: 'red',
  unknown: 'gray',
};

export const lyricsStatusLabel = (status?: string | null) => LABELS[status || 'unknown'] || status || 'Unknown';

/** Mantine color for an album lyrics status ('unknown' is neutral, not an error). */
export const lyricsStatusColor = (status?: string | null) => COLORS[status || 'unknown'] || 'gray';

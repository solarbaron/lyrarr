/** Display names for provider ids returned by the API ("lrclib" → "LRCLIB"). */
const NAMES: Record<string, string> = {
  lrclib: 'LRCLIB',
  musixmatch: 'Musixmatch',
  genius: 'Genius',
  netease: 'NetEase',
  editor: 'Editor',
  deezer: 'Deezer',
  fanart: 'Fanart.tv',
  itunes: 'iTunes',
  musicbrainz: 'MusicBrainz',
  theaudiodb: 'TheAudioDB',
};

export function providerName(id: string | null | undefined): string {
  if (!id) return 'Unknown';
  const key = id.toLowerCase();
  return NAMES[key] ?? id.charAt(0).toUpperCase() + id.slice(1);
}

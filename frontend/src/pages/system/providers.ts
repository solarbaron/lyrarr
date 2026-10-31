/** Display names for metadata providers (System › Providers, Home attention list). */
export const PROVIDER_LABELS: Record<string, string> = {
  musicbrainz: 'MusicBrainz', fanart: 'fanart.tv', deezer: 'Deezer',
  itunes: 'iTunes', theaudiodb: 'TheAudioDB', lrclib: 'LRCLIB',
  genius: 'Genius', musixmatch: 'Musixmatch', netease: 'NetEase',
};

export function providerLabel(name: string): string {
  return PROVIDER_LABELS[name] || name;
}

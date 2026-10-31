import type { Profile } from '../../types';

export interface ProfileForm {
  name: string;
  download_covers: boolean;
  download_lyrics: boolean;
  cover_providers: string[];
  lyrics_providers: string[];
  prefer_synced_lyrics: boolean;
  lyrics_selection_mode: string;
  auto_detect_language: boolean;
  auto_translate: string;
  translate_target_lang: string;
  translate_only_foreign: boolean;
  score_threshold: number;
  cover_format: string;
  overwrite_existing: boolean;
  embed_cover_art: boolean;
}

export interface ProviderOption {
  value: string;
  label: string;
  description: string;
}

export const ALL_COVER_PROVIDERS: ProviderOption[] = [
  { value: 'musicbrainz', label: 'MusicBrainz (Cover Art Archive)', description: 'Free, no API key' },
  { value: 'deezer', label: 'Deezer', description: 'Free, no API key' },
  { value: 'itunes', label: 'iTunes / Apple Music', description: 'Free, no API key' },
  { value: 'fanart', label: 'fanart.tv', description: 'Requires an API key' },
  { value: 'theaudiodb', label: 'TheAudioDB', description: 'Free test key included' },
];

export const ALL_LYRICS_PROVIDERS: ProviderOption[] = [
  { value: 'lrclib', label: 'LRCLIB', description: 'Free · synced + plain' },
  { value: 'musixmatch', label: 'Musixmatch', description: 'API key · synced + plain' },
  { value: 'netease', label: 'NetEase Cloud Music', description: 'Free · synced + plain' },
  { value: 'genius', label: 'Genius', description: 'API key · plain only' },
];

export const DEFAULT_COVERS = ['musicbrainz', 'deezer', 'itunes', 'fanart', 'theaudiodb'];
export const DEFAULT_LYRICS = ['lrclib', 'musixmatch', 'netease', 'genius'];

export function parseProviders(json: string | string[] | null | undefined, fallback: string[]): string[] {
  if (Array.isArray(json)) return json;
  if (!json) return fallback;
  try { return JSON.parse(json); } catch { return fallback; }
}

export const PROFILE_LANGUAGES = [
  { value: 'en', label: 'English' }, { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' }, { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' }, { value: 'pt', label: 'Portuguese' },
  { value: 'ja', label: 'Japanese' }, { value: 'ko', label: 'Korean' },
  { value: 'zh-cn', label: 'Chinese (Simplified)' }, { value: 'ru', label: 'Russian' },
  { value: 'ar', label: 'Arabic' }, { value: 'hi', label: 'Hindi' },
  { value: 'tr', label: 'Turkish' }, { value: 'nl', label: 'Dutch' },
  { value: 'pl', label: 'Polish' }, { value: 'sv', label: 'Swedish' },
];

export const COVER_FORMATS = [
  { value: 'jpg', label: 'JPEG (.jpg)' },
  { value: 'png', label: 'PNG (.png)' },
  { value: 'webp', label: 'WebP (.webp)' },
];

/** Short labels keep the Select readable in a narrow editor; the hints explain each mode. */
export const SELECTION_MODES = [
  { value: 'best_score', label: 'Best score' },
  { value: 'prefer_synced', label: 'Prefer synced' },
  { value: 'prefer_plain', label: 'Prefer plain text' },
];

export const SELECTION_MODE_HINT: Record<string, string> = {
  best_score: 'Highest match % wins; synced breaks ties',
  prefer_synced: 'Synced always wins, then score',
  prefer_plain: 'Plain text, for players that can’t show synced lyrics',
};

export const SELECTION_MODE_SHORT: Record<string, string> = {
  best_score: 'Best score',
  prefer_synced: 'Prefer synced',
  prefer_plain: 'Prefer plain',
};

export const AUTO_TRANSLATE = [
  { value: 'off', label: 'Off' },
  { value: 'dual', label: 'Dual (original + translation)' },
  { value: 'replace', label: 'Replace (translation only)' },
];

export const DEFAULT_FORM: ProfileForm = {
  name: '',
  download_covers: true,
  download_lyrics: true,
  cover_providers: DEFAULT_COVERS,
  lyrics_providers: DEFAULT_LYRICS,
  prefer_synced_lyrics: true,
  lyrics_selection_mode: 'best_score',
  auto_detect_language: true,
  auto_translate: 'off',
  translate_target_lang: 'en',
  translate_only_foreign: true,
  score_threshold: 0,
  cover_format: 'jpg',
  overwrite_existing: false,
  embed_cover_art: false,
};

export function formFromProfile(profile: Profile): ProfileForm {
  return {
    name: profile.name,
    download_covers: profile.download_covers ?? true,
    download_lyrics: profile.download_lyrics ?? true,
    cover_providers: parseProviders(profile.cover_providers, DEFAULT_COVERS),
    lyrics_providers: parseProviders(profile.lyrics_providers, DEFAULT_LYRICS),
    prefer_synced_lyrics: profile.prefer_synced_lyrics ?? true,
    lyrics_selection_mode: profile.lyrics_selection_mode || 'best_score',
    auto_detect_language: profile.auto_detect_language ?? true,
    auto_translate: profile.auto_translate || 'off',
    translate_target_lang: profile.translate_target_lang || 'en',
    translate_only_foreign: profile.translate_only_foreign ?? true,
    score_threshold: profile.score_threshold ?? 0,
    cover_format: profile.cover_format || 'jpg',
    overwrite_existing: profile.overwrite_existing ?? false,
    embed_cover_art: profile.embed_cover_art ?? false,
  };
}

export function sameForm(a: ProfileForm, b: ProfileForm): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

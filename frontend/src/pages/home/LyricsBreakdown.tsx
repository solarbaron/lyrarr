import { Panel } from '../../components/ui';
import { num, pct } from '../../format';
import { providerLabel } from '../system/providers';
import type { LanguageStats } from './types';
import c from './Home.module.css';

const TOP_LANGUAGES = 8;

interface Bar {
  key: string;
  label: string;
  value: number;
  code?: boolean;
}

/** Share of total; a small non-zero share reads "<1%", never "0%" next to a visible bar. */
function share(value: number, total: number): string {
  const p = pct(value, total);
  return value > 0 && p < 1 ? '<1%' : `${p}%`;
}

function BarList({ title, bars, total, unit }: { title: string; bars: Bar[]; total: number; unit: string }) {
  const max = Math.max(1, ...bars.map(b => b.value));
  return (
    <div>
      <h3 className={`ly-overline ${c.barListTitle}`}>{title}</h3>
      <ul className={c.barList} aria-label={title}>
        {bars.map(bar => (
          <li key={bar.key} className={c.barItem}>
            <span className={c.barLabel} data-code={bar.code || undefined} title={bar.label}>{bar.label}</span>
            <span className={c.barValue}>
              {num(bar.value)} {unit}
              {total > 0 && <span>{share(bar.value, total)}</span>}
            </span>
            <span className={c.barTrack} aria-hidden="true">
              <span className={c.barFill} style={{ width: `${(bar.value / max) * 100}%` }} />
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Languages and providers of the lyrics on disk as single-hue ranked bars
 * (DESIGN.md 8.1). Rendered only once some lyrics exist.
 */
export function LyricsBreakdown({ stats }: { stats: LanguageStats }) {
  const languages = Object.entries(stats.languages ?? {}).sort((a, b) => b[1] - a[1]);
  const top: Bar[] = languages.slice(0, TOP_LANGUAGES).map(([code, value]) => ({ key: code, label: code, value, code: true }));
  const rest = languages.slice(TOP_LANGUAGES).reduce((sum, [, v]) => sum + v, 0);
  if (rest > 0) top.push({ key: '__other', label: 'Other', value: rest });

  const providers: Bar[] = Object.entries(stats.providers ?? {})
    .sort((a, b) => b[1] - a[1])
    .map(([name, value]) => ({ key: name, label: providerLabel(name), value }));
  const providerTotal = providers.reduce((sum, p) => sum + p.value, 0);

  return (
    <Panel
      title="Lyrics breakdown"
      subtitle={`${num(stats.total_available)} tracks with lyrics · ${num(stats.synced)} synced · ${num(stats.plain)} plain`}
    >
      <div className={c.breakdown}>
        {top.length > 0
          ? <BarList title="Languages" bars={top} total={stats.total_available} unit="tracks" />
          : <p className={c.recentSub}>No languages detected yet.</p>}
        {providers.length > 0 && <BarList title="Providers" bars={providers} total={providerTotal} unit="tracks" />}
      </div>
    </Panel>
  );
}

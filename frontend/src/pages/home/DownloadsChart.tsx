import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from 'recharts';
import { faDownload } from '@fortawesome/free-solid-svg-icons';
import { EmptyState, Panel } from '../../components/ui';
import { useTokens } from '../../hooks/useTokens';
import { num } from '../../format';
import type { DownloadDay } from './types';
import c from './Home.module.css';

const TOKENS = ['--ly-chart-1', '--ly-chart-2', '--ly-border', '--ly-muted', '--ly-border-strong'] as const;

const DAY = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' });
const DAY_LONG = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short' });

/** "2026-10-04" → local date (no UTC shift). */
function parseDay(value: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

function shortDay(value: string): string {
  const d = parseDay(value);
  return d ? DAY.format(d) : value;
}

function ChartTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  const d = parseDay(String(label ?? ''));
  return (
    <div className={c.tooltip}>
      <div className={c.tooltipDate}>{d ? DAY_LONG.format(d) : label}</div>
      {payload.map(p => (
        <div key={String(p.dataKey)} className={c.tooltipRow}>
          <span className={c.legendSwatch} data-series={p.dataKey === 'covers' ? '1' : '2'} />
          {p.name}
          <strong>{num(Number(p.value ?? 0))}</strong>
        </div>
      ))}
    </div>
  );
}

/** Downloads over the last 30 days (DESIGN.md 8.1, 5.5). */
export function DownloadsChart({ data }: { data: DownloadDay[] }) {
  const t = useTokens(TOKENS);
  const totals = data.reduce(
    (acc, d) => ({ covers: acc.covers + (d.covers || 0), lyrics: acc.lyrics + (d.lyrics || 0) }),
    { covers: 0, lyrics: 0 },
  );
  const empty = totals.covers + totals.lyrics === 0;

  const legend = !empty && (
    <div className={c.chartLegend} aria-label="Legend">
      <span className={c.legendItem}>
        <span className={c.legendSwatch} data-series="1" aria-hidden="true" />
        Covers <span className={c.legendTotal}>{num(totals.covers)}</span>
      </span>
      <span className={c.legendItem}>
        <span className={c.legendSwatch} data-series="2" aria-hidden="true" />
        Lyrics <span className={c.legendTotal}>{num(totals.lyrics)}</span>
      </span>
    </div>
  );

  return (
    <Panel title="Downloads" subtitle="Last 30 days" className={c.downloads}>
      {empty ? (
        <EmptyState
          size="sm"
          icon={faDownload}
          title="Nothing downloaded in the last 30 days"
          message="Covers and lyrics fetched by the scheduler or by you will chart here."
        />
      ) : (
        <>
        {legend}
        <div
          className={c.chart}
          role="img"
          aria-label={`Downloads in the last 30 days: ${num(totals.covers)} covers, ${num(totals.lyrics)} lyrics`}
        >
          <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 480, height: 220 }}>
            <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={t['--ly-border']} strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="date"
                tickFormatter={shortDay}
                tick={{ fontSize: 11, fill: t['--ly-muted'] }}
                tickLine={false}
                axisLine={{ stroke: t['--ly-border'] }}
                minTickGap={24}
              />
              <YAxis
                allowDecimals={false}
                tick={{ fontSize: 11, fill: t['--ly-muted'] }}
                tickLine={false}
                axisLine={false}
                width={32}
              />
              <Tooltip content={props => <ChartTooltip {...props} />} cursor={{ stroke: t['--ly-border-strong'] }} />
              <Area
                type="monotone"
                dataKey="covers"
                name="Covers"
                stroke={t['--ly-chart-1']}
                strokeWidth={2}
                fill={t['--ly-chart-1']}
                fillOpacity={0.18}
                isAnimationActive={false}
              />
              <Area
                type="monotone"
                dataKey="lyrics"
                name="Lyrics"
                stroke={t['--ly-chart-2']}
                strokeWidth={2}
                fill={t['--ly-chart-2']}
                fillOpacity={0.18}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        </>
      )}
    </Panel>
  );
}

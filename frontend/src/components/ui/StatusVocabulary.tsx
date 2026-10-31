import { STATUS_VOCABULARY } from './status';
import { StatusChip } from './StatusChip';
import { StatusDot } from './StatusDot';
import c from './StatusVocabulary.module.css';

export interface StatusVocabularyProps {
  /** Also show the StatusDot form of each status (default true). */
  withDots?: boolean;
  className?: string;
}

/**
 * Living style guide of every status in the current theme: Settings › Appearance
 * legend and the /__ui gallery (DESIGN.md 8.10).
 */
export function StatusVocabulary({ withDots = true, className }: StatusVocabularyProps) {
  return (
    <div className={[c.grid, className].filter(Boolean).join(' ')}>
      {STATUS_VOCABULARY.map(group => (
        <div key={group.domain} className={c.group}>
          <div className="ly-overline">{group.title}</div>
          <div className={c.chips}>
            {group.items.map((item, i) => (
              <StatusChip key={i} domain={group.domain} status={item.status} synced={item.opts?.synced} fraction={item.opts?.fraction} />
            ))}
          </div>
          {withDots && (
            <div className={c.dots}>
              {group.items.map((item, i) => (
                <StatusDot key={i} domain={group.domain} status={item.status} synced={item.opts?.synced} fraction={item.opts?.fraction} />
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

import { useEffect, useState } from 'react';

/**
 * True only once `flag` has stayed true for `delay` ms (DESIGN.md 7.4): skeletons
 * and spinners for fast loads never flash. Drops back to false immediately.
 */
export function useDelayedFlag(flag: boolean, delay = 150): boolean {
  const [shown, setShown] = useState(flag && delay <= 0);
  useEffect(() => {
    if (!flag) {
      setShown(false);
      return;
    }
    if (delay <= 0) {
      setShown(true);
      return;
    }
    const t = window.setTimeout(() => setShown(true), delay);
    return () => window.clearTimeout(t);
  }, [flag, delay]);
  return flag && shown;
}

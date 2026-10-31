#!/usr/bin/env node
// Event-contract guardrail (PLAN §1.6, §5.1). Fails (exit 1) when:
//   - frontend/src/events.tsx's replay-exclusion set (NO_BUFFER_TYPES) differs from the
//     server's _NO_REPLAY_TYPES in lyrarr/app/event_handler.py;
//   - the audit progress helpers (src/auditProgress.ts) or the Compare diff
//     (src/pages/system/audit/diff.ts) misbehave on the cases below.
//
// Usage: node --experimental-strip-types scripts/check-events.mjs
// (Node >= 22.18 / 23.6 strips TypeScript types natively.)
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const failures = [];
const check = (name, fn) => {
  try {
    fn();
  } catch (err) {
    failures.push(`${name}: ${err.message}`);
  }
};

/* ---------- replay set: frontend == server ---------- */
const stringsIn = (block) => new Set([...block.matchAll(/['"]([a-z_]+)['"]/g)].map(m => m[1]));
check('replay set', () => {
  const fe = readFileSync(resolve(root, 'src/events.tsx'), 'utf8');
  const feBlock = /const NO_BUFFER_TYPES = new Set\(\[([\s\S]*?)\]\)/.exec(fe);
  assert.ok(feBlock, 'NO_BUFFER_TYPES literal not found in src/events.tsx');
  const py = readFileSync(resolve(root, '../lyrarr/app/event_handler.py'), 'utf8');
  const pyBlock = /_NO_REPLAY_TYPES = frozenset\(\{([\s\S]*?)\}\)/.exec(py);
  assert.ok(pyBlock, '_NO_REPLAY_TYPES literal not found in lyrarr/app/event_handler.py');
  const a = [...stringsIn(feBlock[1])].sort();
  const b = [...stringsIn(pyBlock[1])].sort();
  assert.deepEqual(a, b, `frontend ${JSON.stringify(a)} != server ${JSON.stringify(b)}`);
});

/* ---------- audit progress helpers ---------- */
const ap = await import(pathToFileURL(resolve(root, 'src/auditProgress.ts')).href);

check('audit start uses checks, not total', () => {
  const s = ap.auditStarted({ run_id: 7, mode: 'dry_run', checks: 23, total: 25000 });
  assert.equal(s.checks, 23);
  assert.equal(s.total, 0, 'the old track total must not leak into the item total');
  assert.equal(ap.auditStepText(s), 'Starting…');
  assert.deepEqual(ap.auditParts(s), [{ label: 'Checks', unit: 'checks', done: 0, total: 23 }]);
  assert.equal(ap.auditJobLabel('apply'), 'Applying fixes');
  assert.equal(ap.auditJobLabel('dry_run'), 'Checking library');
});

check('audit progress re-labels per check', () => {
  let s = ap.auditStarted({ run_id: 7, mode: 'dry_run', checks: 4 });
  s = ap.auditAdvanced(s, { run_id: 7, check_id: 'files.status_drift', check_title: 'Status drift', check_index: 2, checks: 4, current: 50, total: 100 });
  assert.equal(s.checkTitle, 'Status drift');
  assert.equal(ap.auditStepText(s), 'Check 2 of 4');
  assert.equal(ap.auditFraction(s), (1 + 0.5) / 4);
  const parts = ap.auditParts(s);
  assert.equal(parts.length, 2);
  assert.deepEqual(parts[0], { label: 'Checks', unit: 'checks', done: 1, total: 4 });
  assert.deepEqual(parts[1], { label: 'Status drift', unit: 'items', done: 50, total: 100 });
});

check('progress without a start (page refreshed mid-run) starts fresh', () => {
  const s = ap.auditAdvanced(null, { run_id: 9, mode: 'apply', check_id: 'x', check_title: 'X', check_index: 3, checks: 5, current: 1, total: 2 });
  assert.equal(s.runId, 9);
  assert.equal(s.mode, 'apply');
  assert.equal(s.checkIndex, 3);
});

check('progress of another run replaces the state', () => {
  const a = ap.auditAdvanced(null, { run_id: 1, mode: 'apply', check_index: 3, checks: 5 });
  const b = ap.auditAdvanced(a, { run_id: 2, check_index: 1, checks: 9 });
  assert.equal(b.runId, 2);
  assert.equal(b.mode, 'dry_run');
  assert.equal(b.checks, 9);
});

check('progress in the apply phase (no mode in the payload) is an apply run', () => {
  const s = ap.auditAdvanced(null, { run_id: 4, phase: 'apply', check_id: 'x', check_index: 1, checks: 3 });
  assert.equal(s.mode, 'apply');
  assert.equal(ap.auditJobLabel(s.mode), 'Applying fixes');
  const d = ap.auditAdvanced(null, { run_id: 5, phase: 'detect', check_index: 1, checks: 3 });
  assert.equal(d.mode, 'dry_run');
  // Once known, an apply run stays an apply run.
  assert.equal(ap.auditAdvanced(s, { run_id: 4, phase: 'detect', check_index: 2, checks: 3 }).mode, 'apply');
});

check('a replayed audit end only ends the job of the same run', () => {
  const job = ap.auditStarted({ run_id: 12, mode: 'dry_run', checks: 5 });
  assert.equal(ap.auditEventEndsJob(job, { run_id: 99 }, true), true, 'live events always end the job');
  assert.equal(ap.auditEventEndsJob(null, {}, true), true);
  assert.equal(ap.auditEventEndsJob(job, { run_id: 12 }, false), true, 'run finished while the stream was down');
  assert.equal(ap.auditEventEndsJob(job, { run_id: 11 }, false), false, 'an older run in the replay');
  assert.equal(ap.auditEventEndsJob(null, { run_id: 12 }, false), false, 'no running job');
  assert.equal(ap.auditEventEndsJob(ap.auditStarted({}), { run_id: 12 }, false), false, 'job of unknown run');
  assert.equal(ap.auditEventEndsJob(job, {}, false), false);
});

check('fraction clamps and handles unknown totals', () => {
  const s = ap.auditAdvanced(null, { run_id: 1, check_index: 99, checks: 3, current: 5, total: 0 });
  assert.equal(s.checkIndex, 3);
  assert.equal(ap.auditFraction(s), 2 / 3);
  assert.equal(ap.auditFraction(ap.auditStarted({ checks: 0 })), null);
});

/* ---------- Compare diff ---------- */
const df = await import(pathToFileURL(resolve(root, 'src/pages/system/audit/diff.ts')).href);

check('diff keeps order and marks changes', () => {
  const before = 'first line of the verse\nsecond line\nla la placeholder\n';
  const after = 'first line of the verse\nla la placeholder\nnew closing line\n';
  const d = df.diffLines(before, after);
  assert.deepEqual(d.map(x => `${x.kind}:${x.text}`), [
    'same:first line of the verse', 'removed:second line', 'same:la la placeholder', 'added:new closing line',
  ]);
  assert.deepEqual(df.diffStats(d), { added: 1, removed: 1, same: 2 });
});

check('diff of a reorder (scrambled timing) shows both sides', () => {
  const d = df.diffLines('a line\nb line\nc line', 'c line\na line\nb line');
  const s = df.diffStats(d);
  assert.equal(s.same, 2);
  assert.equal(s.added, 1);
  assert.equal(s.removed, 1);
});

check('diff handles empty sides and CRLF', () => {
  assert.deepEqual(df.diffStats(df.diffLines('', 'one\r\ntwo\r\n')), { added: 2, removed: 0, same: 0 });
  assert.deepEqual(df.diffStats(df.diffLines('one\ntwo', '')), { added: 0, removed: 2, same: 0 });
});

check('synced vs plain compares words, not timestamps', () => {
  const synced = '[ar:Someone]\n[00:01.00]first line of the verse\n[00:05.50]<00:05.50>second line\n';
  assert.equal(df.isTimed(synced), true);
  assert.equal(df.isTimed('plain line'), false);
  assert.equal(df.lyricText(synced), 'first line of the verse\nsecond line\n');
  const s = df.diffStats(df.diffLines(df.lyricText(synced), 'first line of the verse\nsecond line'));
  assert.deepEqual(s, { added: 0, removed: 0, same: 2 });
});

if (failures.length) {
  console.error(`check-events: ${failures.length} failure(s)`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log('check-events: ok');

"""Audit & Repair API (PLAN §1.8). Every unsafe method takes a JSON body (CSRF rule)."""

from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.database import (
    TableAuditIgnores,
    TableAuditIssues,
    TableAuditPins,
    TableAuditRuns,
    TableTracks,
    database,
    delete,
    func,
    or_,
    select,
    update,
)
from lyrarr.utilities.pagination import page_params

api_ns_audit = Namespace('audit', description='Library audit & repair')

_SEVERITIES = ('critical', 'high', 'medium', 'low', 'info')
_STATUSES = ('open', 'fixed', 'failed', 'skipped', 'ignored', 'pending')


def _audit():
    import lyrarr.utilities.audit as audit
    return audit


def _json_body():
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None
    return data


def _run_summary(run):
    d = run.to_dict()
    if run.started_at and run.finished_at:
        d['duration_s'] = round((run.finished_at - run.started_at).total_seconds(), 1)
    else:
        d['duration_s'] = None
    return d


def _int_list(value, name, *, max_len=5000):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > max_len or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in value):
        raise ValueError(f'{name} must be a list of integers')
    return value


def _get_run(run_id):
    return database.execute(select(TableAuditRuns).where(TableAuditRuns.id == run_id)).scalars().first()


@api_ns_audit.route('/audit/checks')
class AuditChecks(Resource):
    def get(self):
        """Every check with its last results."""
        audit = _audit()
        last = {}
        latest = database.execute(select(TableAuditRuns.id).where(TableAuditRuns.status.in_(('done', 'cancelled')),
                                                                 TableAuditRuns.parent_run_id.is_(None))
                                  .order_by(TableAuditRuns.id.desc()).limit(20)).scalars().all()
        for run_id in latest:
            run = _get_run(run_id)
            entries = run.to_dict().get('checks') or []
            counts = dict(database.execute(
                select(TableAuditIssues.check_id, func.count()).where(TableAuditIssues.run_id == run_id)
                .group_by(TableAuditIssues.check_id)).all())
            by_status = {}
            for cid, status, n in database.execute(
                    select(TableAuditIssues.check_id, TableAuditIssues.status, func.count())
                    .where(TableAuditIssues.run_id == run_id)
                    .group_by(TableAuditIssues.check_id, TableAuditIssues.status)).all():
                by_status.setdefault(cid, {})[status] = n
            for e in entries:
                cid = e.get('check_id')
                if cid in last or cid not in audit.REGISTRY:
                    continue
                st = by_status.get(cid, {})
                last[cid] = {'run_id': run_id, 'found': counts.get(cid, 0), 'open': st.get('open', 0),
                             'fixed': st.get('fixed', 0), 'pending': st.get('pending', 0),
                             'status': e.get('status'), 'note': e.get('note')}
        checks = []
        from lyrarr.utilities.audit.registry import ordered_checks
        for check in ordered_checks():
            d = check.spec.to_dict()
            d['last'] = last.get(check.spec.id)
            checks.append(d)
        return {'checks': checks, 'running': audit.running_info()}


@api_ns_audit.route('/audit/runs')
class AuditRuns(Resource):
    def get(self):
        """Recent runs (newest first)."""
        limit = max(1, min(request.args.get('limit', 10, type=int) or 10, 100))
        rows = database.execute(select(TableAuditRuns).order_by(TableAuditRuns.id.desc()).limit(limit)).scalars().all()
        return {'data': [_run_summary(r) for r in rows]}

    def post(self):
        """Start a run: {mode, checks?, include?, scope?, network_limit?, resume_run_id?} → 202 {run_id}."""
        data = _json_body()
        if data is None:
            return {'message': 'Body must be a JSON object'}, 400
        mode = data.get('mode', 'dry_run')
        if mode not in ('dry_run', 'apply'):
            return {'message': "mode must be 'dry_run' or 'apply'"}, 400
        checks = data.get('checks')
        if checks is not None and (not isinstance(checks, list) or not all(isinstance(c, str) for c in checks)):
            return {'message': 'checks must be a list of check ids'}, 400
        include = data.get('include')
        if include is not None and not isinstance(include, dict):
            return {'message': 'include must be an object'}, 400
        scope = data.get('scope') or {}
        if not isinstance(scope, dict):
            return {'message': 'scope must be an object'}, 400
        try:
            scope = {'artistIds': _int_list(scope.get('artistIds'), 'scope.artistIds'),
                     'albumIds': _int_list(scope.get('albumIds'), 'scope.albumIds')}
        except ValueError as e:
            return {'message': str(e)}, 400
        network_limit = data.get('network_limit')
        if network_limit is not None and (not isinstance(network_limit, int) or not 1 <= network_limit <= 5000):
            return {'message': 'network_limit must be 1..5000'}, 400
        resume = data.get('resume_run_id')
        if resume is not None and not isinstance(resume, int):
            return {'message': 'resume_run_id must be an integer'}, 400
        try:
            run_id = _audit().start_run(mode, checks, trigger='user', include=include, scope=scope,
                                        network_limit=network_limit, resume_run_id=resume)
        except ValueError as e:
            return {'message': str(e)}, 400
        if run_id is None:
            return {'message': 'A library check is already running', 'code': 'busy'}, 409
        return {'run_id': run_id}, 202


@api_ns_audit.route('/audit/runs/latest')
class AuditRunLatest(Resource):
    def get(self):
        mode = request.args.get('mode', '', type=str)
        q = select(TableAuditRuns)
        if mode:
            q = q.where(TableAuditRuns.mode == mode)
        run = database.execute(q.order_by(TableAuditRuns.id.desc()).limit(1)).scalars().first()
        if run is None:
            return {'message': 'No audit run yet'}, 404
        return _run_summary(run)


@api_ns_audit.route('/audit/runs/<int:run_id>')
class AuditRun(Resource):
    def get(self, run_id):
        run = _get_run(run_id)
        if run is None:
            return {'message': 'Run not found'}, 404
        return _run_summary(run)


@api_ns_audit.route('/audit/runs/<int:run_id>/issues')
class AuditRunIssues(Resource):
    def get(self, run_id):
        from lyrarr.utilities.audit.runner import issue_to_dict
        if _get_run(run_id) is None:
            return {'message': 'Run not found'}, 404
        page, page_size = page_params(50)
        I = TableAuditIssues  # noqa: E741
        conds = [I.run_id == run_id]
        check = request.args.get('check', '', type=str)
        if check:
            from lyrarr.utilities.audit.registry import resolve_check_id
            conds.append(I.check_id == (resolve_check_id(check) or check))
        status = request.args.get('status', '', type=str)
        if status:
            wanted = [s for s in status.split(',') if s in _STATUSES]
            if not wanted:
                return {'message': f'Unknown status {status!r}'}, 400
            conds.append(I.status.in_(wanted))
        severity = request.args.get('severity', '', type=str)
        if severity:
            wanted = [s for s in severity.split(',') if s in _SEVERITIES]
            if not wanted:
                return {'message': f'Unknown severity {severity!r}'}, 400
            conds.append(I.severity.in_(wanted))
        search = request.args.get('search', '', type=str)
        if search:
            like = f'%{search}%'
            conds.append(or_(I.title.ilike(like), I.path.ilike(like)))
        total = database.execute(select(func.count()).select_from(I).where(*conds)).scalar()
        rows = database.execute(select(I).where(*conds).order_by(I.id).offset((page - 1) * page_size)
                                .limit(page_size)).scalars().all()
        ignored = set(database.execute(select(TableAuditIgnores.check_id, TableAuditIgnores.entity_key)).all())
        return {'data': [issue_to_dict(r, ignored) for r in rows], 'total': total, 'page': page,
                'pageSize': page_size}


@api_ns_audit.route('/audit/runs/<int:run_id>/apply')
class AuditRunApply(Resource):
    def post(self, run_id):
        """Apply issues of a run: {checks?:[id], issueIds?:[int], actions?:{issueId: action}} → 202 {run_id}."""
        data = _json_body()
        if data is None:
            return {'message': 'Body must be a JSON object'}, 400
        if _get_run(run_id) is None:
            return {'message': 'Run not found'}, 404
        checks = data.get('checks')
        if checks is not None and (not isinstance(checks, list) or not all(isinstance(c, str) for c in checks)):
            return {'message': 'checks must be a list of check ids'}, 400
        try:
            issue_ids = _int_list(data.get('issueIds'), 'issueIds')
        except ValueError as e:
            return {'message': str(e)}, 400
        actions = data.get('actions') or {}
        if not isinstance(actions, dict):
            return {'message': 'actions must be an object'}, 400
        try:
            actions = {int(k): str(v) for k, v in actions.items()}
        except (TypeError, ValueError):
            return {'message': 'actions keys must be issue ids'}, 400
        try:
            new_id = _audit().start_run('apply', checks, trigger='user', parent_run_id=run_id, issue_ids=issue_ids,
                                        actions=actions)
        except ValueError as e:
            return {'message': str(e)}, 400
        if new_id is None:
            return {'message': 'A library check is already running', 'code': 'busy'}, 409
        return {'run_id': new_id}, 202


@api_ns_audit.route('/audit/runs/<int:run_id>/cancel')
class AuditRunCancel(Resource):
    def post(self, run_id):
        if _json_body() is None:
            return {'message': 'Body must be a JSON object'}, 400
        if _get_run(run_id) is None:
            return {'message': 'Run not found'}, 404
        return {'cancelled': bool(_audit().cancel_run(run_id))}


@api_ns_audit.route('/audit/ignore')
class AuditIgnore(Resource):
    def post(self):
        from datetime import datetime

        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        data = _json_body()
        if data is None:
            return {'message': 'Body must be a JSON object'}, 400
        try:
            ids = _int_list(data.get('issueIds'), 'issueIds')
        except ValueError as e:
            return {'message': str(e)}, 400
        if not ids:
            return {'message': 'issueIds required'}, 400
        rows = database.execute(select(TableAuditIssues).where(TableAuditIssues.id.in_(ids))).scalars().all()
        for r in rows:
            database.execute(sqlite_insert(TableAuditIgnores).values(
                check_id=r.check_id, entity_key=r.entity_key, created_at=datetime.now())
                .on_conflict_do_nothing(index_elements=['check_id', 'entity_key']))
        changed = [r.id for r in rows if r.status in ('open', 'failed')]
        if changed:
            database.execute(update(TableAuditIssues).where(TableAuditIssues.id.in_(changed))
                             .values(status='ignored', resolved_at=datetime.now()))
            database.execute(delete(TableAuditPins).where(TableAuditPins.issue_id.in_(changed)))
        return {'ignored': len(rows)}


@api_ns_audit.route('/audit/unignore')
class AuditUnignore(Resource):
    def post(self):
        data = _json_body()
        if data is None:
            return {'message': 'Body must be a JSON object'}, 400
        check_id, key = data.get('checkId'), data.get('entityKey')
        if not isinstance(check_id, str) or not isinstance(key, str):
            return {'message': 'checkId and entityKey required'}, 400
        from lyrarr.utilities.audit.registry import resolve_check_id
        check_id = resolve_check_id(check_id) or check_id
        n = database.execute(delete(TableAuditIgnores).where(TableAuditIgnores.check_id == check_id,
                                                             TableAuditIgnores.entity_key == key)).rowcount
        database.execute(update(TableAuditIssues).where(TableAuditIssues.check_id == check_id,
                                                        TableAuditIssues.entity_key == key,
                                                        TableAuditIssues.status == 'ignored')
                         .values(status='open', resolved_at=None))
        return {'unignored': n}


def instrumental_counts():
    """(instrumental_review, likely_instrumental, instrumental_cleared) — shared with /wanted/stats."""
    from lyrarr.api.wanted import instrumental_stats
    s = instrumental_stats()
    return s['instrumental_review'], s['likely_instrumental'], s['instrumental_cleared']


@api_ns_audit.route('/audit/summary')
class AuditSummary(Resource):
    def get(self):
        R, I = TableAuditRuns, TableAuditIssues  # noqa: E741
        # The newest COMPLETE top-level run. A cancelled run checked only part of
        # the library (its "nothing open" means nothing), and a child apply run
        # holds no issues of its own (they stay in the parent run, live).
        latest = database.execute(select(R).where(R.status == 'done', R.parent_run_id.is_(None))
                                  .order_by(R.id.desc()).limit(1)).scalars().first()
        out = {'latest': None, 'bootstrap': None}
        if latest is not None:
            sev = dict(database.execute(select(I.severity, func.count()).where(I.run_id == latest.id,
                                                                                I.status == 'open')
                                        .group_by(I.severity)).all())
            fix = dict(database.execute(select(I.fix_mode, func.count()).where(I.run_id == latest.id,
                                                                                I.status == 'open')
                                        .group_by(I.fix_mode)).all())
            out['latest'] = {'run_id': latest.id, 'mode': latest.mode, 'trigger': latest.trigger,
                             'finished_at': latest.finished_at.isoformat() if latest.finished_at else None,
                             'open_by_severity': sev, 'fixable_auto': fix.get('auto', 0),
                             'review': fix.get('review', 0)}
        boot = database.execute(select(R).where(R.trigger == 'bootstrap').order_by(R.id.desc()).limit(1)
                                ).scalars().first()
        if boot is not None:
            totals = boot.to_dict().get('totals') or {}
            review = database.execute(select(func.count()).select_from(I).where(
                I.run_id == boot.id, I.status == 'open', I.fix_mode == 'review')).scalar()
            cleared = database.execute(select(func.count()).select_from(TableTracks).where(
                TableTracks.instrumental_prev.is_not(None),
                TableTracks.instrumental_prev.like('%"actor": "bootstrap"%'))).scalar()
            out['bootstrap'] = {'run_id': boot.id, 'status': boot.status,
                                'finished_at': boot.finished_at.isoformat() if boot.finished_at else None,
                                'changed': totals.get('fixed', 0), 'review': review,
                                'instrumental_cleared': cleared}
        review, likely, cleared = instrumental_counts()
        out['instrumental_review'] = review
        out['likely_instrumental'] = likely
        out['instrumental_cleared'] = cleared
        unreachable = []
        try:
            from lyrarr.metadata.lyrics_store import library_reachability
            unreachable = [s.root for s in library_reachability().unreachable if s.checked]
        except Exception:
            unreachable = []
        out['unreachable_roots'] = unreachable
        out['running'] = _audit().running_info()
        return out

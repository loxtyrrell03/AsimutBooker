"""JSON command bridge to the installed Booker's validated action surface.

No web server, copied state, credentials, or replacement booking engine.
Batch selections live for this process only and are consumed by the host.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

REPO = Path(r'C:\Users\Lox\Desktop\repo\AsimutBooker')
EXTRA_WRITES = {'save_phone_preferences'}


class Bridge:
    def __init__(self, surface, specs, mutating, *, extras=None):
        self.surface = surface
        self.specs = {spec['name']: spec for spec in specs}
        self.mutating = set(mutating) | EXTRA_WRITES
        self.extras = extras or {}

    def execute(self, job, *, read_only=False, progress=None):
        if not isinstance(job, dict) or set(job) - {'user_request', 'steps'}:
            raise ValueError('Supply user_request and steps only')
        steps, request = job.get('steps'), job.get('user_request', '')
        if not isinstance(request, str):
            raise ValueError('user_request must be the actual user message')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
            raise ValueError('Supply 1-32 steps')
        known = set(self.specs) | set(self.extras)
        names = set()
        # Validate the entire envelope before the first side effect.
        for step in steps:
            if not isinstance(step, dict) or set(step) - {'tool', 'arguments', 'name', 'selection_from'}:
                raise ValueError('Unknown batch step fields')
            tool, arguments = step.get('tool'), step.get('arguments', {})
            if not isinstance(tool, str) or tool not in known:
                raise ValueError(f'Unknown tool: {tool}')
            if not isinstance(arguments, dict) or 'request_quote' in arguments:
                raise ValueError('Arguments must be an object; request_quote is bound by the bridge')
            if tool in self.mutating and (read_only or not request.strip()):
                raise ValueError('This action needs the actual user request and cannot run in read-only mode')
            selection = step.get('selection_from')
            if selection is not None and (not isinstance(selection, str) or selection not in names
                    or tool not in {'edit_reservation_time', 'cancel_reservations'}
                    or 'selection_id' in arguments):
                raise ValueError('selection_from must name an earlier find_reservations step')
            name = step.get('name')
            if name is not None:
                if not isinstance(name, str) or not name or name in names or tool != 'find_reservations':
                    raise ValueError('Only find_reservations may name a unique selection')
                names.add(name)
        self.surface.begin_turn()
        selected, outcomes = {}, []
        for index, step in enumerate(steps):
            tool = step['tool']
            arguments = dict(step.get('arguments', {}))
            try:
                if step.get('selection_from'):
                    arguments['selection_id'] = selected[step['selection_from']]
                if tool in self.extras:
                    data = self.extras[tool](arguments)
                else:
                    if tool in self.mutating:
                        arguments['request_quote'] = request
                    data = self.surface.dispatch(tool, arguments, user_request=request, progress=progress)
                outcomes.append({'step': index, 'tool': tool, 'data': data})
                if step.get('name'):
                    selection = data.get('selection_id')
                    if data.get('refresh_required') or not selection:
                        raise ValueError('No fresh exact selection; inspect the result before another request')
                    selected[step['name']] = selection
                if (data.get('refresh_required') or data.get('reconciliation_required') or data.get('stopped_early')
                        or data.get('status') in {'unconfirmed', 'not_applied', 'verified_saved_refresh_required'}
                        or (tool == 'run_booker' and not data.get('verified_actions'))
                        or data.get('command', {}).get('exit_code', 0) != 0):
                    return {'dispatch_completed': False, 'stopped_at': index, 'results': outcomes,
                            'error': 'The host requires review or read-only reconciliation; later steps were not dispatched'}
            except Exception as exc:
                return {'dispatch_completed': False, 'stopped_at': index, 'results': outcomes,
                        'error': str(exc), 'note': 'Do not replay mutations after an uncertain outcome; inspect receipts and agenda first'}
        return {'dispatch_completed': True, 'results': outcomes,
                'note': 'Dispatch completion is not reservation proof. Inspect each host result and fresh agenda.'}


def load_bridge():
    if not (REPO / 'assistant_tools.py').is_file():
        raise RuntimeError(f'Canonical Booker runtime is missing: {REPO}')
    sys.path.insert(0, str(REPO))
    from assistant_tools import BookerToolSurface, dynamic_tool_specs, MUTATING_TOOLS, ASSISTANT_MUTATION_LOCK_FILE
    from app_settings import InterProcessFileLock, load_settings
    from booking_blackouts import load_rebooking_blackouts
    from manual_booking_overrides import KEY, validate_records
    from phone_preferences import read_phone_preferences, save_phone_preferences
    from runtime_guard import configure_utf8_stdio

    configure_utf8_stdio()

    def read_preferences(arguments):
        if arguments:
            raise ValueError('read_phone_preferences takes no arguments')
        return read_phone_preferences()

    def save_preferences(arguments):
        with InterProcessFileLock(ASSISTANT_MUTATION_LOCK_FILE, timeout=1):
            return save_phone_preferences(arguments)

    def protections(arguments):
        if arguments:
            raise ValueError('get_manual_protections takes no arguments')
        settings = load_settings()
        return {'manual_booking_overrides': validate_records(settings.get(KEY, {})),
                'rebooking_blackouts': [w.to_dict() for w in load_rebooking_blackouts(settings)]}

    return Bridge(BookerToolSurface(), dynamic_tool_specs(), MUTATING_TOOLS, extras={
        'read_phone_preferences': read_preferences, 'save_phone_preferences': save_preferences,
        'get_manual_protections': protections})


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only', action='store_true', help='Reject all mutating steps before dispatch')
    commands = parser.add_subparsers(dest='command', required=True)
    tools = commands.add_parser('tools', help='Read live tool schemas')
    tools.add_argument('--name')
    call = commands.add_parser('call', help='Dispatch one host action')
    call.add_argument('tool')
    call.add_argument('--args-file')
    call.add_argument('--request-file', help='UTF-8 text containing the actual user request authorizing changes')
    batch = commands.add_parser('batch', help='Execute a JSON batch in one current-turn selection context')
    batch.add_argument('--file', required=True)
    args = parser.parse_args(argv)
    try:
        bridge = load_bridge()
        if args.command == 'tools':
            extras = [
                {'name': 'read_phone_preferences', 'arguments': {}, 'description': 'Read normalized settings and current revision'},
                {'name': 'get_manual_protections', 'arguments': {}, 'description': 'Read exact edited-booking pins and protected intervals'},
                {'name': 'save_phone_preferences', 'arguments': {'revision': 'from read_phone_preferences', 'changes': {}},
                 'description': 'Save a scoped revision-checked patch, including booking_rules. See references/settings.md.'},
            ]
            specs = list(bridge.specs.values()) + extras
            if args.name:
                specs = [spec for spec in specs if spec['name'] == args.name]
                if not specs:
                    raise ValueError('Unknown tool name')
            result = {'runtime': str(REPO), 'tools': specs}
        else:
            job = read_json(args.file) if args.command == 'batch' else {
                'user_request': Path(args.request_file).read_text(encoding='utf-8-sig') if args.request_file else '',
                'steps': [{'tool': args.tool, 'arguments': read_json(args.args_file) if args.args_file else {}}]}
            def progress(title, detail):
                print(json.dumps({'progress': title, 'detail': detail}, ensure_ascii=False), file=sys.stderr, flush=True)
            with redirect_stdout(sys.stderr):
                result = bridge.execute(job, read_only=args.read_only, progress=progress)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('dispatch_completed', True) else 2
    except Exception as exc:
        print(json.dumps({'dispatch_completed': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

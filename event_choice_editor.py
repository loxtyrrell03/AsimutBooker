"""Desktop event-choice drafts using the same scoped service as the phone.

This module does not read live settings until explicitly asked to save. A failed
or concurrent save retains the opening revision and the user's exact selection.
"""
from copy import deepcopy


class EventChoiceDraft:
    def __init__(self, document):
        self.document = deepcopy(document)
        self.rows = {row['key']: row for row in self.document['events']}
        self.values = {key: not row['ignored'] for key, row in self.rows.items()}
        self.exact_bindings = set()
        self.uncertain = False

    @property
    def dirty(self):
        return bool(self.exact_bindings) or any(value != (not self.rows[key]['ignored']) for key, value in self.values.items())

    def bind_exact(self, key):
        if self.uncertain:
            raise ValueError('Reload current choices before changing an uncertain save.')
        row = self.rows.get(key, {})
        if not row.get('eligible') or not row.get('ignored') or row.get('choice_basis') not in {'v2', 'legacy'}:
            raise ValueError('Only a resolved older choice can be bound to this exact event.')
        self.exact_bindings.add(key)

    def keep_protected(self, key):
        if self.uncertain:
            raise ValueError('Reload current choices before changing an uncertain save.')
        row = self.rows.get(key, {})
        if not row.get('eligible') or not row.get('unresolved_choice') or row.get('choice_basis') == 'exact':
            raise ValueError('Only an unresolved choice needs an explicit exact protection.')
        self.values[key] = True
        self.exact_bindings.add(key)

    def select(self, selections):
        if set(selections) - set(self.rows):
            raise ValueError('An event changed. Reload the current choices.')
        if any(type(value) is not bool for value in selections.values()):
            raise ValueError('Event choices must be enabled or disabled.')
        if self.uncertain and any(value != self.values[key] for key, value in selections.items()):
            raise ValueError('Reload current choices before changing an uncertain save.')
        if any(not self.rows[key].get('eligible') and value != self.values[key]
               for key, value in selections.items()):
            raise ValueError('This event cannot be changed here.')
        self.values.update(selections)

    def request(self):
        if self.uncertain:
            raise ValueError('The previous save needs review. Reload current choices; no save will be repeated.')
        if self.document.get('stale'):
            raise ValueError('Refresh the agenda before changing event choices.')
        changes = {key: not value for key, value in self.values.items()
                   if self.rows[key].get('eligible') and
                   (key in self.exact_bindings or value != (not self.rows[key]['ignored']))}
        return dict(revision=self.document['revision'], changes=changes)

    def save(self, root):
        from phone_system import run_local_action, SystemConflict
        from runtime_guard import SingleInstanceAlreadyRunning
        request = self.request()
        if not request['changes']:
            return {'message': 'Event choices are unchanged.'}
        try:
            return run_local_action('events_save', request, root)
        except (SystemConflict, SingleInstanceAlreadyRunning):
            # The canonical service guarantees these are pre-write rejections.
            raise
        except Exception:
            # Atomic settings may have succeeded before plan invalidation failed.
            # A second click must never silently dispatch the same save again.
            self.uncertain = True
            raise

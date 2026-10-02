/** Edit only exact eligible rows and keep only actual changes from the opening view. */
export function editEventChoices(events, previous, keys, ignored) {
  const changes = { ...previous };
  const selected = new Set(keys);
  for (const event of events ?? []) {
    if (event.eligible !== true || !selected.has(event.key)) continue;
    if (ignored === event.ignored) delete changes[event.key];
    else changes[event.key] = ignored;
  }
  return changes;
}

/** Never silently drop an unsupported/missing choice and save a partial draft. */
export function validEventChoices(events, changes) {
  const rows = new Map((events ?? []).map(event => [event.key, event]));
  return Object.entries(changes).every(([key, ignored]) => {
    const event = rows.get(key);
    return event?.eligible === true && typeof ignored === 'boolean' && (ignored !== event.ignored ||
      (ignored && ['v2', 'legacy'].includes(event.choice_basis)) ||
      (!ignored && event.unresolved_choice === true && event.choice_basis !== 'exact'));
  });
}

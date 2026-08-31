/**
 * Return every selected session in display order: primary first, then the
 * planner's additional non-overlapping sessions.
 *
 * @template T
 * @param {{primary: T | null, additional?: T[]}} day
 * @returns {T[]}
 */
export function selectedPlanSessions(day) {
  return day.primary ? [day.primary, ...(day.additional ?? [])] : [];
}

/**
 * @param {{primary: {potential_minutes?: number} | null, additional?: {potential_minutes?: number}[]}} day
 */
export function selectedPlanMinutes(day) {
  return selectedPlanSessions(day).reduce(
    (total, candidate) => total + Math.max(0, Number(candidate.potential_minutes) || 0),
    0,
  );
}

/** Full-day closure mark; labels and booking controls remain above it. */
export function ClosedDayCross() {
  return <svg className="closure-cross" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" focusable="false">
    <path d="M 0 0 L 100 100 M 100 0 L 0 100" fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
  </svg>;
}

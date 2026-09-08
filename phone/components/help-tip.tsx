'use client';
import { useEffect, useId, useRef, useState } from 'react';

export function HelpTip({ children, label }: { children: React.ReactNode; label: string }) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 8, top: 8 });
  const root = useRef<HTMLSpanElement>(null);
  const id = useId();
  const pointerFocus = useRef(false);
  const touchInteraction = useRef(false);
  useEffect(() => {
    if (!open) return;
    const place = () => {
      const box = root.current?.getBoundingClientRect();
      if (box) setPosition({ left: Math.max(8, Math.min(box.left, window.innerWidth - 288)), top: Math.max(8, Math.min(box.bottom + 8, window.innerHeight - 150)) });
    };
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    place(); document.addEventListener('pointerdown', outside); document.addEventListener('keydown', key);
    window.addEventListener('resize', place); window.addEventListener('scroll', place, true);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', key); window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
  }, [open]);
  return <span ref={root} className="help-tip" onPointerEnter={event => { touchInteraction.current = event.pointerType !== 'mouse'; }} onMouseEnter={() => { if (!touchInteraction.current && window.matchMedia('(hover:hover)').matches) setOpen(true); }} onMouseLeave={() => { if (!touchInteraction.current && window.matchMedia('(hover:hover)').matches) setOpen(false); }}>
    <button type="button" aria-label={`Help: ${label}`} aria-expanded={open} aria-describedby={open ? id : undefined} onPointerDown={() => { pointerFocus.current = true; }} onKeyDown={() => { pointerFocus.current = false; }} onClick={() => setOpen(value => !value)} onFocus={event => { if (!pointerFocus.current && event.currentTarget.matches(':focus-visible')) setOpen(true); }} onBlur={event => { pointerFocus.current = false; if (!root.current?.contains(event.relatedTarget as Node)) setOpen(false); }}>?</button>
    {open && <span id={id} role="tooltip" className="help-popover" style={position}>{children}<button type="button" aria-label="Close help" onClick={() => setOpen(false)}>×</button></span>}
  </span>;
}

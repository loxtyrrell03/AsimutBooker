'use client';

import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Clock3, DoorOpen, MessageCircle, RefreshCw } from 'lucide-react';
import type { AgendaEvent, BookerSnapshot } from '../app/page';
import { durationMinutes, hoursLabel, todaySummary } from '../lib/today_state';

function dateName(date: string) {
  return new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' }).format(new Date(`${date}T12:00:00`));
}

export function TodayView({ booker, refreshing, onRefresh, onWeek, onAsk, onDetails, preview }: {
  booker: BookerSnapshot; refreshing: boolean; onRefresh: () => void;
  onWeek: () => void; onAsk: (prompt: string) => void; onDetails: (event: AgendaEvent) => void; preview: boolean;
}) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const timer = window.setInterval(() => setNow(new Date()), 30_000); return () => window.clearInterval(timer); }, []);
  const view = todaySummary(booker.agenda.events, preview ? new Date(booker.generated_at) : now, booker.timezone);
  const stale = booker.agenda.stale;
  const practice = booker.preferences.practice_plan;
  return <section className="today-view" aria-labelledby="today-title">
    <div className="today-heading"><div><p>{dateName(view.today)}</p><h1 id="today-title">Today</h1></div>
      <button className="quiet-icon" aria-label="Refresh bookings" onClick={onRefresh} disabled={refreshing}><RefreshCw className={refreshing ? 'spin-slow' : ''} /></button></div>
    {preview && <p className="preview-notice">Design preview · sample bookings</p>}
    {stale && <output className="quiet-notice">Showing your last checked bookings. {refreshing ? 'Updating…' : 'Refresh to check for changes.'}</output>}
    {booker.status.pending_mutations > 0 && <p className="quiet-notice" role="alert">A booking result needs checking. Open Settings to review it.</p>}
    <div className="today-top-grid">
      <article className="next-booking">
        <div className="next-eyebrow"><span>{view.inProgress ? 'HAPPENING NOW' : 'UP NEXT'}</span>{view.next && <span className="booked-pill">{stale ? 'Last checked' : 'Booked'}</span>}</div>
        {booker.agenda.available && view.next ? <>
          <h2>Room {view.next.room}</h2>
          {view.next.date !== view.today && <p className="next-date">{dateName(view.next.date)}</p>}
          <p className="next-time">{view.next.start_time}–{view.next.end_time}</p>
          <p className="next-location"><DoorOpen aria-hidden="true" /> {hoursLabel(durationMinutes(view.next))} of practice</p>
          <p className="reconfirm-note">Reconfirm on college Wi-Fi when available.</p>
          <button className="quiet-primary" onClick={() => onDetails(view.next!)}>View booking</button>
        </> : <><h2>{booker.agenda.available ? 'Make room for practice.' : 'Let’s check your bookings.'}</h2>
          <p className="empty-next">{booker.agenda.available ? 'No upcoming practice booking in your last checked agenda.' : 'Your agenda is unavailable. Refresh to see your next session.'}</p>
          <button className="quiet-primary" onClick={booker.agenda.available ? () => onAsk('Help me find a practice room. Ask which date and time I want.') : onRefresh} disabled={refreshing}>{booker.agenda.available ? 'Find a room' : 'Refresh bookings'}</button></>}
      </article>
      <article className="week-glance"><h2>This week</h2><p className="week-total">{booker.agenda.available ? hoursLabel(view.weekMinutes) : '—'}</p><p>{stale ? 'booked in your last checked agenda' : 'booked in your checked agenda'}</p>
        {practice.enabled && practice.default_hours !== null && <p className="daily-goal">Daily goal <strong>{practice.default_hours} hours</strong></p>}
        <button className="quiet-link" onClick={onWeek}>See my week <ArrowRight /></button></article>
    </div>
    <section className="also-today"><div className="section-heading"><h2>Also today</h2><button className="quiet-link" onClick={onWeek}>My Week <ArrowRight /></button></div>
      {!booker.agenda.available ? <p className="quiet-muted">Refresh to check the rest of your day.</p> : view.alsoToday.length ? view.alsoToday.map((event, index) => <button className="today-event" key={`${event.room}-${event.start_time}-${index}`} onClick={() => event.is_reservation ? onDetails(event) : onWeek()}>
        <span className="today-event-time"><strong>{event.start_time}</strong><span>{event.end_time}</span></span><span className="today-event-copy"><strong>{event.is_reservation ? `Room ${event.room}` : event.title}</strong><span>{event.is_reservation ? 'Booked practice' : event.room}</span></span><ArrowRight /></button>) : <p className="quiet-muted">{stale ? 'No other sessions in the last checked agenda.' : 'Nothing else coming up in your checked agenda today.'}</p>}
    </section>
    <button className="quiet-primary find-room" onClick={() => onAsk('Help me find a practice room. Ask which date and time I want.')}>Find a room</button>
    <button className="ask-card" onClick={() => onAsk('')}><MessageCircle /><span><strong>Need to change your plans?</strong><span>Ask Assistant</span></span><ArrowRight /></button>
    <output className="today-freshness">{refreshing ? 'Checking your bookings…' : booker.agenda.observed_at ? `Last checked ${new Intl.DateTimeFormat('en-GB', {day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:booker.timezone}).format(new Date(booker.agenda.observed_at))}` : 'Not checked yet'}</output>
  </section>;
}

export function BookingDetails({ event, stale, onClose, onAsk }: {
  event: AgendaEvent; stale: boolean; onClose: () => void; onAsk: (prompt: string) => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  const identity = `${event.room} on ${event.date} from ${event.start_time} to ${event.end_time}`;
  return <section className="booking-details" aria-labelledby="booking-detail-title">
    <button className="quiet-link detail-back" onClick={onClose}><ArrowLeft /> Back</button>
    <h1 id="booking-detail-title" tabIndex={-1} ref={heading}>Your booking</h1>
    <span className="booked-pill">{stale ? 'Last checked booking' : 'Booked'}</span>
    <h2>Room {event.room}</h2><p>{dateName(event.date)}</p><p className="detail-time">{event.start_time}–{event.end_time}</p>
    <div className="detail-row"><Clock3 /><span><strong>{hoursLabel(durationMinutes(event))}</strong><span>Time reserved for practice</span></span></div>
    <div className="quiet-info"><strong>Before you practise</strong><p>Reconfirm in Asimut on college Wi-Fi when reconfirmation becomes available.</p></div>
    <a className="quiet-primary" href="https://rwcmd.asimut.net/" target="_blank" rel="noreferrer">Open in Asimut</a>
    <button className="quiet-secondary" onClick={() => onAsk(`I would like to change my booking in ${identity}. Ask what I want to change, then check the live agenda.`)}>Ask to change booking</button>
    <button className="quiet-danger" onClick={() => onAsk(`Cancel my reservation in ${identity}. Check the live agenda and exact booking before acting.`)}>Ask to cancel booking</button>
    <p className="quiet-muted">{stale ? 'This booking may have changed. ' : ''}The assistant checks the current booking before making changes.</p>
  </section>;
}

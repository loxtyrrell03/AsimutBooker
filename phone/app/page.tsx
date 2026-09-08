'use client';

import {
  AlertTriangle,
  Bot,
  CalendarDays,
  Check,
  ChevronDown,
  CircleStop,
  Clock3,
  HeartPulse,
  Home,
  MessageCircle,
  MessageSquarePlus,
  RefreshCw,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  WifiOff,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  compactProgressText,
  deliveryDisposition,
  isFreshSequence,
  nextReconnectDelay,
  reconcileStreamPosition,
  updateProgressNarrative,
  upsertReasoningPart,
} from '@/lib/phone_state';
import { selectedPlanMinutes, selectedPlanSessions } from '@/lib/plan_state';
import { PhoneCalendar } from '@/components/phone-calendar';
import { PracticeSettings } from '@/components/practice-settings';
import { BookingDetails, TodayView } from '@/components/quiet-focus';
import { requestJson } from '@/lib/api';

const PRIVATE_ORIGIN = process.env.NEXT_PUBLIC_ASIMUT_PHONE_ORIGIN || '';
const subscribeBrowserSnapshot = () => () => undefined;

type Tab = 'today' | 'assistant' | 'schedule' | 'calendar' | 'status';
type ConnectionState = 'connecting' | 'online' | 'offline';

type ChatMessage = {
  role: 'user' | 'assistant';
  text: string;
  created_at?: string;
  optimistic?: boolean;
};

type PendingDelivery = {
  id: string;
  text: string;
};

export type AgendaEvent = {
  event_id?: number | null;
  date: string;
  start_time: string;
  end_time: string;
  title: string;
  is_reservation: boolean;
  room: string;
};

type PlanCandidate = {
  room: string;
  date: string;
  start_time: string;
  end_time: string;
  state: string;
  reason: string;
  confirmed_minutes: number;
  potential_minutes: number;
  unlock_at: string;
};

type PlanDay = {
  date: string;
  status: string;
  target_minutes: number;
  existing_minutes: number;
  reason: string;
  primary: PlanCandidate | null;
  additional: PlanCandidate[];
  backups: PlanCandidate[];
};

type HealthItem = {
  key: string;
  label: string;
  state: 'ok' | 'warning' | 'error' | 'unknown';
  headline: string;
  detail: string;
  observed_at: string;
};

export type BookerSnapshot = {
  version: number;
  generated_at: string;
  timezone: string;
  status: {
    state: 'ready' | 'stale' | 'attention' | 'blocked';
    label: string;
    pending_mutations: number;
  };
  agenda: {
    available: boolean;
    stale: boolean;
    observed_at: string;
    freshness_reason: string;
    events: AgendaEvent[];
    next_event: AgendaEvent | null;
    closed_dates?: string[];
  };
  plan: {
    available: boolean;
    stale: boolean;
    generated_at: string;
    summary: string;
    days: PlanDay[];
  };
  preferences: {
    practice_plan: {
      enabled: boolean;
      default_hours: number | null;
      date_overrides: Record<string, number>;
    };
    time_preferences: {
      enabled: boolean;
      start_time: string;
      end_time: string;
      strict_mode: boolean;
    };
    future_intentions: Array<{
      title: string;
      intent_summary: string;
      start_date: string;
      end_date: string;
    }>;
    rebooking_blackouts: Array<{
      date: string;
      start_time: string;
      end_time: string;
    }>;
  };
  health: { collected_at: string; items: HealthItem[] };
  unavailable_sections: string[];
};

type CancellationProgress = {
  request_id: string; reservation: AgendaEvent; active: boolean; text: string; cancelled?: boolean;
};

type Bootstrap = {
  cancellation?: CancellationProgress | null;
  model: string;
  busy: boolean;
  messages: ChatMessage[];
  event_cursor: number;
  stream_generation: string;
  active_client_message_id: string | null;
  unresolved_reserved_count: number;
  booker: BookerSnapshot;
};

type PublicEvent = {
  cancellation?: CancellationProgress;
  seq: number;
  at: string;
  kind: string;
  status?: string;
  title?: string;
  text?: string;
  part?: number;
  replace?: boolean;
  terminal?: boolean;
  client_message_id?: string;
  stream_generation: string;
};

type ToolUpdate = {
  title: string;
  text: string;
  status: string;
};

type ReasoningPart = {
  index: number;
  text: string;
};

const demoBooker: BookerSnapshot = {
  version: 1,
  generated_at: '2026-09-08T09:41:00+01:00',
  timezone: 'Europe/London',
  status: { state: 'ready', label: 'Booker ready', pending_mutations: 0 },
  agenda: {
    available: true,
    stale: false,
    observed_at: '2026-09-08T08:41:00Z',
    freshness_reason: '',
    next_event: {
      date: '2026-09-08',
      start_time: '11:00',
      end_time: '11:30',
      title: 'Reservation',
      is_reservation: true,
      room: 'B0.29',
    },
    events: [
      {
        date: '2026-09-08',
        start_time: '11:00',
        end_time: '11:30',
        title: 'Reservation',
        is_reservation: true,
        room: 'B0.29',
      },
      {
        date: '2026-09-08',
        start_time: '16:00',
        end_time: '17:30',
        title: 'Reservation',
        is_reservation: true,
        room: 'B0.14',
      },
    ],
  },
  plan: {
    available: true,
    stale: false,
    generated_at: '2026-08-31T10:45:00Z',
    summary: 'Best visible opportunity is waiting for its booking edge',
    days: [
      {
        date: '2026-09-07',
        status: 'waiting',
        target_minutes: 120,
        existing_minutes: 0,
        reason: 'Waiting for the strongest visible room',
        primary: {
          room: 'Weston Gallery',
          date: '2026-09-07',
          start_time: '12:30',
          end_time: '14:30',
          state: 'waiting',
          reason: 'Best visible opportunity',
          confirmed_minutes: 0,
          potential_minutes: 120,
          unlock_at: '2026-08-31T12:00:00Z',
        },
        additional: [],
        backups: [],
      },
    ],
  },
  preferences: {
    practice_plan: { enabled: true, default_hours: 2, date_overrides: {} },
    time_preferences: {
      enabled: true,
      start_time: '12:30',
      end_time: '21:00',
      strict_mode: true,
    },
    future_intentions: [],
    rebooking_blackouts: [],
  },
  health: {
    collected_at: '2026-09-08T09:41:00+01:00',
    items: [
      {
        key: 'last_success',
        label: 'Last successful run',
        state: 'ok',
        headline: 'Last run completed successfully',
        detail: 'Verified Booker history is available.',
        observed_at: '2026-08-30T17:05:55+01:00',
      },
      {
        key: 'pending_mutations',
        label: 'Pending mutations',
        state: 'ok',
        headline: 'No mutations need reconciliation',
        detail: 'The strict mutation journal has no pending receipts.',
        observed_at: '',
      },
    ],
  },
  unavailable_sections: [],
};

const demoMessages: ChatMessage[] = [
  {
    role: 'assistant',
    text: 'Good morning. I can see your practice plan, current reservations, room plan and Booker health. Ask me a question or tell me what you want changed.',
  },
];

const starters = [
  'What do I have tomorrow?',
  'Find me more time this weekend',
  'Is the automatic booker healthy?',
  'Explain why the next booking is waiting',
];

function dateLabel(value: string, long = false) {
  const parsed = new Date(`${value}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    weekday: long ? 'long' : 'short',
    day: 'numeric',
    month: 'short',
  }).format(parsed);
}

function timeAgo(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'time unknown';
  const minutes = Math.max(0, Math.round((Date.now() - parsed.getTime()) / 60000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return dateLabel(parsed.toISOString().slice(0, 10));
}

function messageParagraphs(text: string) {
  return text
    .split(/\n\s*\n/)
    .filter(Boolean)
    .map((paragraph, index) => (
      <p key={`${index}-${paragraph.slice(0, 20)}`}>
        {paragraph.split('\n').map((line, lineIndex) => (
          <span key={`${lineIndex}-${line.slice(0, 12)}`}>
            {lineIndex > 0 && <br />}
            {line}
          </span>
        ))}
      </p>
    ));
}

function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <span />
      <span />
      <span />
    </div>
  );
}

function RemoteGate() {
  return (
    <main className="gate-shell">
      <div className="gate-card">
        <BrandMark />
        <Badge className="gate-badge" variant="outline">
          Private companion
        </Badge>
        <h1>Asimut Assistant</h1>
        <p>
          Your Booker stays on your own PC. Open the private tailnet app to see
          your schedule and use the assistant.
        </p>
        <a className="gate-button" href={PRIVATE_ORIGIN || undefined}>
          <Home />
          Open my Booker
        </a>
        <p className="gate-note">{PRIVATE_ORIGIN ? 'Tailscale must be connected on this phone.' : 'Open the private address shown by your PC’s phone setup.'}</p>
      </div>
    </main>
  );
}

function StatusIcon({ state }: { state: BookerSnapshot['status']['state'] }) {
  if (state === 'blocked' || state === 'attention') return <AlertTriangle />;
  if (state === 'stale') return <RefreshCw />;
  return <ShieldCheck />;
}

function AppHeader({
  booker,
  connection,
  onNewChat,
  newChatDisabled,
  tab,
}: {
  booker: BookerSnapshot | null;
  connection: ConnectionState;
  onNewChat: () => void;
  newChatDisabled: boolean;
  tab: Tab;
}) {
  const state = booker?.status.state ?? 'stale';
  const label =
    connection === 'offline'
      ? 'Connection interrupted'
      : connection === 'connecting'
        ? 'Connecting to Booker…'
        : booker?.status.label ?? 'Booker status unavailable';
  return (
    <header className="top-bar">
      <BrandMark />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-[15px] font-semibold tracking-[-0.01em]">
            Asimut Booker
          </h1>
        </div>
        <p className={`app-status state-${connection === 'offline' ? 'offline' : state}`}>
          <span className="status-dot" aria-hidden="true" />
          {label}
        </p>
      </div>
      {tab === 'assistant' && <Button
        aria-label="Start a new chat"
        className="round-control"
        disabled={newChatDisabled}
        onClick={onNewChat}
        size="icon-lg"
        variant="ghost"
      >
        <MessageSquarePlus />
      </Button>}
    </header>
  );
}

function ConnectionBanner({
  connection,
  error,
  onRetry,
}: {
  connection: ConnectionState;
  error: string;
  onRetry: () => void;
}) {
  if (connection === 'online') return null;
  return (
    <div className={`connection-banner ${connection}`} role={error ? 'alert' : 'status'}>
      {connection === 'offline' ? <WifiOff /> : <RefreshCw className="spin-slow" />}
      <div>
        <strong>{connection === 'offline' ? 'Couldn’t connect to Booker' : 'Connecting'}</strong>
        <span>{error || 'Opening your private Booker session…'}</span>
      </div>
      {connection === 'offline' && (
        <Button onClick={onRetry} size="sm" variant="outline">
          Retry
        </Button>
      )}
    </div>
  );
}

function ContextPeek({ booker, onOpenSchedule }: { booker: BookerSnapshot; onOpenSchedule: () => void }) {
  const next = booker.agenda.available ? booker.agenda.next_event : null;
  const plan = booker.plan.available
    ? booker.plan.days.find((day) => day.primary)?.primary ?? null
    : null;
  return (
    <section className="context-peek" aria-label="Current Booker context">
      <button className="peek-row" onClick={onOpenSchedule} type="button">
        <div className="peek-icon confirmed"><CalendarDays /></div>
        <div className="peek-copy">
          <span>Next booked</span>
          <strong>
            {next
              ? `${dateLabel(next.date)} · ${next.start_time} · ${next.room}${booker.agenda.stale ? ' · last checked' : ''}`
              : !booker.agenda.available
                ? 'Agenda needs refresh'
                : 'No upcoming reservation'}
          </strong>
        </div>
        <ChevronDown />
      </button>
      <button className="peek-row" onClick={onOpenSchedule} type="button">
        <div className="peek-icon potential"><Clock3 /></div>
        <div className="peek-copy">
          <span>Automatic plan</span>
          <strong>
            {plan
              ? `${dateLabel(plan.date)} · ${plan.start_time} · ${plan.room}${booker.plan.stale ? ' · last checked' : ''}`
              : !booker.plan.available
                ? 'Plan needs refresh'
                : booker.plan.summary}
          </strong>
        </div>
        <ChevronDown />
      </button>
    </section>
  );
}

function ProgressCard({
  reasoningParts,
  narrative,
  tools,
  busy,
}: {
  reasoningParts: ReasoningPart[];
  narrative: string;
  tools: ToolUpdate[];
  busy: boolean;
}) {
  const [expanded, setExpanded] = useState(busy);
  const wasBusyRef = useRef(busy);
  const bodyRef = useRef<HTMLDivElement>(null);
  const summaries = reasoningParts
    .map((part) => compactProgressText(part.text, 420))
    .filter(Boolean);
  const latestSummary = summaries.at(-1) || '';
  const cleanNarrative = compactProgressText(narrative, 520);
  const visibleTools = tools.slice(-6);
  const latest = visibleTools.at(-1);
  const latestDetail = compactProgressText(
    latest?.text || cleanNarrative || latestSummary || 'Checking your practice plans',
    180,
  );

  useEffect(() => {
    if (busy && !wasBusyRef.current) setExpanded(true);
    if (!busy && wasBusyRef.current) setExpanded(false);
    wasBusyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    if (!expanded || !bodyRef.current) return;
    const body = bodyRef.current;
    const frame = window.requestAnimationFrame(() => {
      body.scrollTop = body.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [cleanNarrative, expanded, summaries, visibleTools]);

  if (!cleanNarrative && !summaries.length && !visibleTools.length && !busy) return null;
  return (
    <details
      className="progress-card"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
      open={expanded}
    >
      <summary>
        <span className={busy ? 'thinking-pulse' : 'finished-dot'} aria-hidden="true" />
        <span>
          <strong>{latest?.title || (busy ? 'Working on your request' : 'Work completed')}</strong>
          <small aria-live="polite">{latestDetail}</small>
        </span>
        <ChevronDown aria-hidden="true" />
      </summary>
      <div className="progress-body" ref={bodyRef}>
        {(cleanNarrative || summaries.length > 0) && (
          <div className="thinking-summary">
            <strong>Thinking</strong>
            {cleanNarrative && <p>{cleanNarrative}</p>}
            {summaries.slice(-3).map((summary, index) => (
              <p key={`${index}-${summary.slice(0, 28)}`}>{summary}</p>
            ))}
          </div>
        )}
        {visibleTools.map((tool, index) => (
          <div className={`work-step ${tool.status}`} key={`${tool.title}-${index}`}>
            {tool.status === 'completed' || tool.status === 'success' ? <Check /> : <span />}
            <div>
              <strong>{tool.title}</strong>
              {tool.text && <small>{compactProgressText(tool.text, 260)}</small>}
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}

function Transcript({
  messages,
  streamingText,
  reasoningParts,
  narrative,
  tools,
  busy,
}: {
  messages: ChatMessage[];
  streamingText: string;
  reasoningParts: ReasoningPart[];
  narrative: string;
  tools: ToolUpdate[];
  busy: boolean;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
  }, [messages, streamingText, busy]);

  return (
    <section className="transcript" aria-label="Conversation">
      {messages.length === 0 && (
        <div className="welcome-message">
          <div className="assistant-avatar"><Sparkles /></div>
          <div>
            <h2>What would you like to do?</h2>
            <p>
              Find a room, change your practice plans, or see what’s coming up.
            </p>
          </div>
        </div>
      )}
      {messages.map((message, index) => (
        <article
          className={`message ${message.role}-message ${message.optimistic ? 'optimistic' : ''}`}
          key={`${message.created_at ?? 'local'}-${index}-${message.text.slice(0, 24)}`}
        >
          {message.role === 'assistant' && (
            <div className="assistant-avatar" aria-hidden="true"><Sparkles /></div>
          )}
          <div className="message-content">{messageParagraphs(message.text)}</div>
        </article>
      ))}
      {(busy || narrative || reasoningParts.length > 0 || tools.length > 0) && (
        <article className="message assistant-message activity-message">
          <div className="assistant-avatar active" aria-hidden="true"><Sparkles /></div>
          <div className="message-content">
            <ProgressCard
              busy={busy}
              narrative={narrative}
              reasoningParts={reasoningParts}
              tools={tools}
            />
          </div>
        </article>
      )}
      {streamingText && (
        <article className="message assistant-message" aria-live="polite">
          <div className="assistant-avatar" aria-hidden="true"><Bot /></div>
          <div className="message-content streaming-answer">
            {messageParagraphs(streamingText)}
            {busy && <span className="streaming-caret" aria-hidden="true" />}
          </div>
        </article>
      )}
      <div ref={endRef} />
    </section>
  );
}

function StarterPrompts({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="suggestion-strip" aria-label="Suggested questions">
      {starters.map((prompt) => (
        <button className="suggestion" key={prompt} onClick={() => onPick(prompt)} type="button">
          {prompt}
        </button>
      ))}
    </div>
  );
}

function ChatComposer({
  draft,
  setDraft,
  busy,
  enabled,
  onSend,
  onStop,
  inputRef,
}: {
  draft: string;
  setDraft: (value: string) => void;
  busy: boolean;
  enabled: boolean;
  onSend: () => void;
  onStop: () => void;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
  const submit = (event: React.SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault();
    onSend();
  };
  return (
    <div className="composer-wrap">
      <form className="composer" onSubmit={submit}>
        <Textarea
          aria-label="Message Asimut Assistant"
          className="composer-input"
          disabled={!enabled}
          maxLength={10000}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onSend();
            }
          }}
          placeholder={enabled ? 'Ask about your week or change a booking…' : 'Reconnect to your PC to chat'}
          ref={inputRef}
          rows={1}
          value={draft}
        />
        <div className="composer-actions">
          <p>{busy ? 'The assistant is working' : 'Ask or tell it what to do'}</p>
          {busy ? (
            <Button
              aria-label="Stop assistant"
              className="composer-button stop-button"
              onClick={onStop}
              size="icon-lg"
              type="button"
              variant="outline"
            >
              <CircleStop />
            </Button>
          ) : (
            <Button
              aria-label="Send message"
              className="composer-button send-button"
              disabled={!enabled || !draft.trim()}
              size="icon-lg"
              type="submit"
            >
              <Send />
            </Button>
          )}
        </div>
      </form>
      <p className="safety-note">
        Your bookings are checked before changes are made.
      </p>
    </div>
  );
}

function ScheduleView({
  booker,
  onCancelBooking,
  cancelling,
  onRefresh,
  refreshing,
}: {
  booker: BookerSnapshot;
  onCancelBooking: (event: AgendaEvent) => void;
  cancelling: boolean;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const groups = useMemo(() => {
    const result = new Map<string, AgendaEvent[]>();
    for (const date of booker.agenda.closed_dates ?? []) result.set(date, []);
    for (const event of booker.agenda.events) {
      result.set(event.date, [...(result.get(event.date) ?? []), event]);
    }
    return [...result.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [booker.agenda.events, booker.agenda.closed_dates]);
  const reservations = useMemo(
    () => booker.agenda.events.filter((event) => event.is_reservation),
    [booker.agenda.events],
  );
  const bookedMinutes = useMemo(() => reservations.reduce((total, event) => {
    const [startHour, startMinute] = event.start_time.split(':').map(Number);
    const [endHour, endMinute] = event.end_time.split(':').map(Number);
    const duration = (endHour * 60 + endMinute) - (startHour * 60 + startMinute);
    return total + (Number.isFinite(duration) && duration > 0 ? duration : 0);
  }, 0), [reservations]);
  const plannedSessions = useMemo(
    () => booker.plan.days.flatMap((day) => selectedPlanSessions(day)),
    [booker.plan.days],
  );
  return (
    <section className="view-page schedule-view" aria-labelledby="schedule-title">
      <div className="view-heading">
        <div>
          <span className="eyebrow">Booked and planned</span>
          <h2 id="schedule-title">My Week</h2>
          <p>
            Live agenda checked {timeAgo(booker.agenda.observed_at)}
            {booker.agenda.stale ? ' · updating recommended' : ''}
          </p>
        </div>
        <Button aria-label="Refresh schedule" disabled={refreshing} onClick={onRefresh} size="icon-lg" variant="outline">
          <RefreshCw className={refreshing ? 'spin-slow' : ''} />
        </Button>
      </div>

      {booker.status.pending_mutations > 0 && (
        <div className="attention-card" role="alert">
          <AlertTriangle />
          <div>
            <strong>Booking changes are paused</strong>
            <p>We need to check the last booking result before making another change.</p>
          </div>
        </div>
      )}

      <div className="schedule-metrics" aria-label="Schedule summary">
        <div>
          <span>Reservations</span>
          <strong>{reservations.length}</strong>
        </div>
        <div>
          <span>Booked time</span>
          <strong>{Math.round(bookedMinutes / 6) / 10}h</strong>
        </div>
        <div>
          <span>Planned sessions</span>
          <strong>{plannedSessions.length}</strong>
        </div>
      </div>

      {refreshing && (
        <output className="refresh-state-line">
          <RefreshCw className="spin-slow" />
          Checking Asimut for current bookings and a fresh plan…
        </output>
      )}

      {(!booker.agenda.available || booker.agenda.stale) && (
        <div className="attention-card" role={booker.agenda.available ? 'status' : 'alert'}>
          {booker.agenda.available ? <RefreshCw /> : <AlertTriangle />}
          <div>
            <strong>{booker.agenda.available ? 'Showing the last checked agenda' : 'Agenda is unavailable'}</strong>
            <p>
              {booker.agenda.available
                ? 'The list stays visible while a live refresh catches up. Cancellation still revalidates against Asimut.'
                : 'Refresh the live agenda before relying on the schedule.'}
            </p>
          </div>
        </div>
      )}

      <div className="schedule-legend" aria-label="Schedule legend">
        <span><i className="confirmed-key" /> Booked</span>
        <span><i className="potential-key" /> Planned · not booked yet</span>
        <span className="closed-key">Practice rooms closed</span>
      </div>

      {!booker.agenda.available ? (
        <div className="empty-card">
          <AlertTriangle />
          <strong>Current bookings could not be verified</strong>
          <p>Refresh the live agenda or ask the assistant to check it.</p>
        </div>
      ) : groups.length === 0 ? (
        <div className="empty-card">
          <CalendarDays />
          <strong>No agenda events in the last live check</strong>
          <p>{refreshing ? 'Checking Asimut now…' : 'Tap refresh to check Asimut again.'}</p>
        </div>
      ) : (
        <div className="day-list">
          {groups.map(([date, events]) => (
            <section className={`day-section${booker.agenda.closed_dates?.includes(date) ? ' rooms-closed' : ''}`} key={date}>
              <h3>{dateLabel(date, true)}</h3>
              {booker.agenda.closed_dates?.includes(date) && <p className="closure-label">Practice rooms closed</p>}
              {events.map((event, index) => (
                <article className="agenda-card" key={`${event.start_time}-${event.room}-${index}`}>
                  <div className="event-time">
                    <strong>{event.start_time}</strong>
                    <span>{event.end_time}</span>
                  </div>
                  <div className="event-copy">
                    <Badge variant={event.is_reservation ? 'default' : 'outline'}>
                      {event.is_reservation ? 'Reservation' : 'College event'}
                    </Badge>
                    <h4>{event.room}</h4>
                    {!event.is_reservation && <p>{event.title}</p>}
                    {event.is_reservation && (
                      <button disabled={cancelling || !event.event_id} onClick={() => onCancelBooking(event)} type="button">
                        Cancel booking
                      </button>
                    )}
                  </div>
                </article>
              ))}
            </section>
          ))}
        </div>
      )}

      <section className="plan-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Automatic Booker</span>
            <h3>Planned practice</h3>
          </div>
          <Badge variant="outline">
            {refreshing ? 'Updating' : booker.plan.stale ? 'Last checked plan' : 'Not booked yet'}
          </Badge>
        </div>
        <p className="plan-summary">{booker.plan.summary}</p>
        {booker.plan.stale && booker.plan.available && (
          <output className="attention-card">
            <RefreshCw className={refreshing ? 'spin-slow' : ''} />
            <div>
              <strong>Showing the last generated plan</strong>
              <p>
                Generated {timeAgo(booker.plan.generated_at)}. Potential blocks remain visible
                for context and are refreshed live before the Booker acts.
              </p>
            </div>
          </output>
        )}
        {!booker.plan.available ? (
          <div className="empty-inline">No generated plan is available yet. Tap refresh to build one.</div>
        ) : booker.plan.days.length === 0 ? (
          <div className="empty-inline">No current potential blocks.</div>
        ) : (
          booker.plan.days.map((day) => {
            const sessions = selectedPlanSessions(day);
            const plannedMinutes = selectedPlanMinutes(day);
            return (
              <article className="plan-day" key={day.date}>
                <div className="plan-date">
                  <strong>{dateLabel(day.date, true)}</strong>
                  <span>
                    {Math.round(day.existing_minutes / 60 * 10) / 10}h booked ·{' '}
                    {Math.round(day.target_minutes / 60 * 10) / 10}h target
                    {plannedMinutes > 0
                      ? ` · ${Math.round(plannedMinutes / 60 * 10) / 10}h across ${sessions.length} planned ${sessions.length === 1 ? 'session' : 'sessions'}`
                      : ''}
                  </span>
                </div>
                {sessions.length ? (
                  <div className="potential-list">
                    {sessions.map((candidate, index) => (
                      <div className="potential-card" key={`${candidate.room}-${candidate.start_time}-${candidate.end_time}`}>
                        <div><Clock3 /></div>
                        <div>
                          <Badge variant="outline">
                            {sessions.length > 1 ? `Session ${index + 1} · ` : ''}Not booked yet
                          </Badge>
                          <h4>{candidate.start_time}–{candidate.end_time}</h4>
                          <p>{candidate.room}</p>
                          <small>{candidate.reason || day.reason}</small>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="day-reason">{day.reason}</p>
                )}
              </article>
            );
          })
        )}
      </section>
    </section>
  );
}

function StatusView({
  booker,
  standalone,
  onRefresh,
  refreshing,
  csrf,
  editable,
  onSaved,
}: {
  booker: BookerSnapshot;
  standalone: boolean;
  onRefresh: () => void;
  refreshing: boolean;
  csrf: string;
  editable: boolean;
  onSaved: () => void;
}) {
  const practice = booker.preferences.practice_plan;
  const time = booker.preferences.time_preferences;
  return (
    <section className="view-page status-view" aria-labelledby="status-title">
      <div className="view-heading">
        <div>
          <h2 id="status-title">Settings</h2>
          <p>Checked {timeAgo(booker.health.collected_at)}</p>
        </div>
        <Button aria-label="Refresh status" disabled={refreshing} onClick={onRefresh} size="icon-lg" variant="outline">
          <RefreshCw className={refreshing ? 'spin-slow' : ''} />
        </Button>
      </div>

      <article className={`overview-card state-${booker.status.state}`}>
        <div className="overview-icon"><StatusIcon state={booker.status.state} /></div>
        <div>
          <h3>{booker.status.label}</h3>
          <p>
            {booker.status.pending_mutations > 0
              ? 'A recent booking needs checking. See System details below.'
              : booker.agenda.stale ? 'Refresh to check your latest bookings.'
              : 'Your practice preferences are shared with the PC app.'}
          </p>
        </div>
      </article>

      {booker.unavailable_sections.length > 0 && (
        <div className="attention-card" role="alert">
          <AlertTriangle />
          <div>
            <strong>Some Booker data is unavailable</strong>
            <p>Refresh or ask the assistant to check: {booker.unavailable_sections.join(', ')}.</p>
          </div>
        </div>
      )}

      {!standalone && (
        <article className="install-card">
          <div className="install-icon"><Home /></div>
          <div>
            <strong>Add Asimut to your Home Screen</strong>
            <p>In Safari, tap Share, then “Add to Home Screen” for the full app view.</p>
          </div>
        </article>
      )}

      {!booker.unavailable_sections.includes('preferences') && <section className="preference-card">
        <PracticeSettings csrf={csrf} enabled={editable} onSaved={onSaved}
          targetLabel={practice.enabled && practice.default_hours ? `${practice.default_hours} hours` : 'Off'}
          timeLabel={time.enabled ? `${time.start_time}–${time.end_time}` : 'Any time'} />
        {booker.preferences.future_intentions.length > 0 && (
          <div className="intent-list" aria-label="Saved future practice intentions">
            {booker.preferences.future_intentions.map((intention) => (
              <article key={`${intention.start_date}-${intention.end_date}-${intention.title}`}>
                <strong>{intention.title}</strong>
                <span>{intention.start_date} to {intention.end_date}</span>
                {intention.intent_summary && <p>{intention.intent_summary}</p>}
              </article>
            ))}
          </div>
        )}
        {booker.preferences.rebooking_blackouts.length > 0 && (
          <div className="intent-list" aria-label="Cancelled times kept free">
            {booker.preferences.rebooking_blackouts.map((blackout) => (
              <article key={`${blackout.date}-${blackout.start_time}-${blackout.end_time}`}>
                <strong>Cancelled time kept free</strong>
                <span>{dateLabel(blackout.date, true)} · {blackout.start_time}–{blackout.end_time}</span>
                <p>The automatic Booker will not replace a booking in this window unless you change it.</p>
              </article>
            ))}
          </div>
        )}
      </section>}

      <details className="health-list" aria-label="Detailed Booker health">
        <summary className="settings-details-title">System details <ChevronDown /></summary>
        <div className="section-heading">
          <div>
            <h3>System health</h3>
          </div>
          <HeartPulse />
        </div>
        {booker.health.items.map((item) => (
          <details className={`health-row state-${item.state}`} key={item.key}>
            <summary>
              <span className="health-dot" aria-hidden="true" />
              <span><strong>{item.label}</strong><small>{item.headline}</small></span>
              <ChevronDown />
            </summary>
            <p>{item.detail}</p>
          </details>
        ))}
      </details>
    </section>
  );
}

function BottomNavigation({ tab, onChange }: { tab: Tab; onChange: (tab: Tab) => void }) {
  const items: Array<{ id: Tab; label: string; icon: typeof MessageCircle }> = [
    { id: 'today', label: 'Today', icon: Home },
    { id: 'schedule', label: 'My Week', icon: CalendarDays },
    { id: 'calendar', label: 'Calendar', icon: CalendarDays },
    { id: 'assistant', label: 'Assistant', icon: MessageCircle },
    { id: 'status', label: 'Settings', icon: Settings2 },
  ];
  return (
    <nav className="bottom-nav" aria-label="Main navigation">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <button
            aria-current={tab === item.id ? 'page' : undefined}
            className={tab === item.id ? 'active' : ''}
            key={item.id}
            onClick={() => onChange(item.id)}
            type="button"
          >
            <Icon />
            <span>{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

export default function HomePage() {
  const [tab, setTab] = useState<Tab>('today');
  const [cancelling, setCancelling] = useState(false);
  const cancellingRef = useRef(false);
  const [cancellationStatus, setCancellationStatus] = useState('');
  const [cancellationBooking, setCancellationBooking] = useState<AgendaEvent | null>(null);
  const [selectedBooking, setSelectedBooking] = useState<AgendaEvent | null>(null);
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [csrf, setCsrf] = useState('');
  const [booker, setBooker] = useState<BookerSnapshot | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streamingText, setStreamingText] = useState('');
  const [reasoningParts, setReasoningParts] = useState<ReasoningPart[]>([]);
  const [progressNarrative, setProgressNarrative] = useState('');
  const [tools, setTools] = useState<ToolUpdate[]>([]);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [uncertainOutcome, setUncertainOutcome] = useState('');
  const [acknowledgingUncertain, setAcknowledgingUncertain] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const liveScheduleRunningRef = useRef(false);
  const liveScheduleAttemptRef = useRef(0);
  const preview = useMemo(
    () => typeof window !== 'undefined' && window.location.hostname === 'localhost' && window.location.port === '3000',
    [],
  );
  const standalone = useSyncExternalStore(
    subscribeBrowserSnapshot,
    () => window.matchMedia('(display-mode: standalone)').matches
      || Boolean((navigator as Navigator & { standalone?: boolean }).standalone),
    () => false,
  );
  const privateSurface = useSyncExternalStore<boolean | null>(
    subscribeBrowserSnapshot,
    () => window.location.origin === PRIVATE_ORIGIN || preview,
    () => null,
  );
  const [pendingDelivery, setPendingDelivery] = useState<PendingDelivery | null>(null);
  const cursorRef = useRef(0);
  const streamGenerationRef = useRef<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const streamingRef = useRef('');
  const pendingDeliveryRef = useRef<PendingDelivery | null>(null);
  const streamConfirmedDeliveryIdsRef = useRef(new Set<string>());
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const connectingRef = useRef(false);
  const sendingRef = useRef(false);
  const [stopping, setStopping] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const connectRef = useRef<() => Promise<boolean>>(async () => false);
  const scheduleReconnectRef = useRef<() => void>(() => undefined);

  const settlePendingDelivery = useCallback((clientMessageId?: string) => {
    const pending = pendingDeliveryRef.current;
    if (!pending || !clientMessageId || pending.id !== clientMessageId) return false;
    streamConfirmedDeliveryIdsRef.current.add(pending.id);
    pendingDeliveryRef.current = null;
    setPendingDelivery(null);
    setDraft((current) => current.trim() === pending.text ? '' : current);
    return true;
  }, []);

  const applyCancellation = useCallback((operation: CancellationProgress) => {
    cancellingRef.current = operation.active;
    setCancelling(operation.active);
    setCancellationStatus(operation.text);
    setCancellationBooking(operation.reservation);
    if (operation.cancelled) setSelectedBooking(null);
  }, []);

  const applyBootstrap = useCallback((payload: Bootstrap) => {
    // A slower HTTP response must not undo a newer streamed turn/reset.
    if (payload.stream_generation === streamGenerationRef.current && payload.event_cursor < cursorRef.current) return;
    const position = reconcileStreamPosition(
      streamGenerationRef.current,
      cursorRef.current,
      payload.stream_generation,
      payload.event_cursor,
    );
    streamGenerationRef.current = position.generation;
    cursorRef.current = position.cursor;
    setBooker(payload.booker);
    if (payload.cancellation) applyCancellation(payload.cancellation);
    setMessages(payload.messages);
    setBusy(payload.busy);
    settlePendingDelivery(payload.active_client_message_id ?? undefined);
    if (!payload.busy) {
      streamingRef.current = '';
      setStreamingText('');
    }
    const unresolved = payload.unresolved_reserved_count || 0;
    if (unresolved && !payload.cancellation?.active) {
      cancellingRef.current = false;
      setCancelling(false);
    }
    setUncertainOutcome(unresolved > 0
      ? `${unresolved} earlier command${unresolved === 1 ? ' has' : 's have'} an uncertain outcome after an interruption. Review Booker status before continuing.`
      : '');
  }, [applyCancellation, settlePendingDelivery]);

  const refreshSnapshot = useCallback(async () => {
    if (preview) return;
    if (!csrf) return;
    setRefreshing(true);
    try {
      const { response, data } = await requestJson<Bootstrap>('/api/v1/refresh', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: '{}',
      });
      if (!response.ok) throw new Error('Booker refresh was not available');
      applyBootstrap(data);
      setConnection('online');
      setError('');
    } catch {
      setConnection('offline');
      setError('Reconnect to Tailscale and make sure the Booker PC is awake.');
    } finally {
      setRefreshing(false);
    }
  }, [applyBootstrap, csrf, preview]);

  const refreshLiveSchedule = useCallback(async (force = false) => {
    if (preview || !csrf || liveScheduleRunningRef.current || cancellingRef.current) return;
    if (busy) {
      if (force) setError('Wait for the assistant to finish, then refresh your bookings.');
      return;
    }
    const now = Date.now();
    if (!force && now - liveScheduleAttemptRef.current < 45_000) return;
    liveScheduleAttemptRef.current = now;
    liveScheduleRunningRef.current = true;
    setRefreshing(true);
    try {
      const { response, data } = await requestJson<Bootstrap>('/api/v1/live-refresh', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ scope: 'plan', force }),
      }, 16 * 60_000);
      if (response.status === 409) {
        await refreshSnapshot();
        setError('The Booker is already checking Asimut. Try refresh again when it finishes.');
        return;
      }
      if (!response.ok) throw new Error('live_refresh_failed');
      applyBootstrap(data);
      setConnection('online');
      setError('');
    } catch {
      setError('Live Asimut refresh did not finish. The last checked schedule remains visible.');
    } finally {
      liveScheduleRunningRef.current = false;
      setRefreshing(false);
    }
  }, [applyBootstrap, busy, csrf, preview, refreshSnapshot]);

  const handleEvent = useCallback((event: PublicEvent) => {
    if (event.stream_generation !== streamGenerationRef.current) {
      sourceRef.current?.close();
      sourceRef.current = null;
      setConnection('connecting');
      setError('The Booker service restarted. Re-establishing live updates…');
      scheduleReconnectRef.current();
      return;
    }
    if (!isFreshSequence(cursorRef.current, event.seq)) return;
    cursorRef.current = Math.max(cursorRef.current, event.seq || 0);
    if (event.kind === 'cancellation.progress' && event.cancellation) {
      applyCancellation(event.cancellation);
      if (!event.cancellation.active) void refreshSnapshot();
      return;
    }
    if (event.kind === 'turn.accepted' || event.kind === 'turn.started') {
      setBusy(true);
      if (settlePendingDelivery(event.client_message_id)) {
        setMessages((current) => current.map((message) => (
          message.optimistic ? { ...message, optimistic: false } : message
        )));
      }
      streamingRef.current = '';
      setStreamingText('');
      setReasoningParts([]);
      setProgressNarrative('');
      setTools([]);
      return;
    }
    if (event.kind === 'reasoning.delta') {
      setReasoningParts((current) => upsertReasoningPart(current, event.part, event.text));
      return;
    }
    if (event.kind === 'progress.delta') {
      setProgressNarrative((current) => updateProgressNarrative(
        current,
        event.text,
        Boolean(event.replace),
      ));
      return;
    }
    if (event.kind === 'tool.status' || event.kind === 'activity') {
      const update = {
        title: event.title || 'Checking Booker data',
        text: event.text || '',
        status: event.status || 'in_progress',
      };
      setTools((current) => {
        const previous = current.at(-1);
        if (previous?.title === update.title) return [...current.slice(0, -1), update];
        return [...current.slice(-7), update];
      });
      return;
    }
    if (event.kind === 'assistant.delta') {
      setStreamingText((current) => {
        const next = current + (event.text ?? '');
        streamingRef.current = next;
        return next;
      });
      return;
    }
    if (event.kind === 'turn.completed') {
      setBusy(false);
      if (settlePendingDelivery(event.client_message_id)) {
        setMessages((current) => current.map((message) => (
          message.optimistic ? { ...message, optimistic: false } : message
        )));
      }
      const completedText = streamingRef.current;
      streamingRef.current = '';
      setStreamingText('');
      if (completedText) {
        setMessages((current) => [...current, { role: 'assistant', text: completedText }]);
      }
      window.setTimeout(() => void refreshSnapshot(), 120);
      return;
    }
    if (event.kind === 'chat.reset') {
      setBusy(true);
      setMessages([]);
      streamingRef.current = '';
      setStreamingText('');
      setReasoningParts([]);
      setProgressNarrative('');
      setTools([]);
      void refreshSnapshot();
      return;
    }
    if (event.kind === 'snapshot.required') {
      void refreshSnapshot();
      return;
    }
    if (event.kind === 'session.status') {
      if (event.status === 'ready') setConnection('online');
      if (['failed', 'offline', 'disconnected'].includes(event.status ?? '')) {
        setConnection('offline');
      }
      if (event.terminal) {
        setBusy(false);
        settlePendingDelivery(event.client_message_id);
      }
      return;
    }
    if (event.kind === 'session.busy') {
      setBusy(event.status !== 'ready');
      return;
    }
    if (event.kind === 'error') {
      setError(event.text || 'The assistant could not continue.');
      if (event.terminal) {
        setBusy(false);
        settlePendingDelivery(event.client_message_id);
        window.setTimeout(() => void refreshSnapshot(), 120);
      }
    }
  }, [applyCancellation, refreshSnapshot, settlePendingDelivery]);

  const openEventStream = useCallback(() => {
    if (preview || !csrf) return;
    const generation = streamGenerationRef.current;
    if (!generation) return;
    sourceRef.current?.close();
    const parameters = new URLSearchParams({
      after: String(cursorRef.current),
      generation,
    });
    const source = new EventSource(`/api/v1/assistant/events?${parameters.toString()}`, {
      withCredentials: true,
    });
    sourceRef.current = source;
    source.addEventListener('update', (raw) => {
      try {
        handleEvent(JSON.parse((raw as MessageEvent).data) as PublicEvent);
      } catch {
        setError('A live update could not be read. Refreshing the current state.');
        void refreshSnapshot();
      }
    });
    source.onopen = () => {
      reconnectAttemptsRef.current = 0;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      setConnection('online');
      setError('');
    };
    source.onerror = () => {
      // Rotate the exact-origin session before reopening the resumable stream.
      // This handles both transient transport loss and bounded session expiry.
      source.close();
      if (sourceRef.current === source) sourceRef.current = null;
      setConnection('connecting');
      setError('Live updates paused. Reconnecting…');
      scheduleReconnectRef.current();
    };
  }, [csrf, handleEvent, preview, refreshSnapshot]);

  const connect = useCallback(async (): Promise<boolean> => {
    if (preview) {
      setCsrf('preview');
      setBooker(demoBooker);
      setMessages(demoMessages);
      setConnection('online');
      return true;
    }
    if (connectingRef.current) return false;
    connectingRef.current = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    setConnection('connecting');
    setError('');
    try {
      const response = await fetch('/api/v1/session', {
        signal: controller.signal,
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client: 'asimut-phone-v1' }),
      });
      if (!response.ok) {
        throw new Error(response.status === 401 || response.status === 403
          ? 'Your PC responded, but private access was rejected. Check that Tailscale uses your allowed account.'
          : 'Your PC responded, but Booker could not open a session. Try again.');
      }
      const payload = await response.json() as { csrf_token: string; bootstrap: Bootstrap };
      setCsrf(payload.csrf_token);
      applyBootstrap(payload.bootstrap);
      setConnection('online');
      setError('');
      return true;
    } catch (failure) {
      setConnection('offline');
      setError(failure instanceof Error && failure.message.startsWith('Your PC responded')
        ? failure.message : 'Check Tailscale and that the Booker PC is awake. If its private address changed, open the new address from your PC.');
      return false;
    } finally {
      window.clearTimeout(timeout);
      connectingRef.current = false;
    }
  }, [applyBootstrap, preview]);

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimerRef.current !== null) return;
    const attempt = reconnectAttemptsRef.current;
    const delay = nextReconnectDelay(attempt);
    if (delay === null) {
      setConnection('offline');
      setError(current => current || 'Live updates stopped after three retries. Tap Retry to reconnect.');
      return;
    }
    reconnectAttemptsRef.current += 1;
    reconnectTimerRef.current = window.setTimeout(async () => {
      reconnectTimerRef.current = null;
      if (!(await connectRef.current())) scheduleReconnectRef.current();
    }, delay);
  }, []);

  useEffect(() => {
    connectRef.current = connect;
    scheduleReconnectRef.current = scheduleReconnect;
  }, [connect, scheduleReconnect]);

  const retryConnection = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    reconnectAttemptsRef.current = 0;
    void connect().then(ok => { if (!ok) scheduleReconnectRef.current(); });
  }, [connect]);

  useEffect(() => {
    const privateHost = window.location.origin === PRIVATE_ORIGIN || preview;
    if (privateHost) queueMicrotask(() => void connect().then(ok => { if (!ok) scheduleReconnectRef.current(); }));
    if ('serviceWorker' in navigator && window.isSecureContext) {
      navigator.serviceWorker.register('/sw.js').catch(() => undefined);
    }
    return () => {
      sourceRef.current?.close();
      if (reconnectTimerRef.current !== null) window.clearTimeout(reconnectTimerRef.current);
    };
  }, [connect, preview]);

  useEffect(() => {
    if (!privateSurface || preview) return;
    const resume = () => {
      if (document.visibilityState === 'visible' && connection === 'offline') retryConnection();
      if ('serviceWorker' in navigator) void navigator.serviceWorker.getRegistration().then(reg => reg?.update()).catch(() => undefined);
    };
    window.addEventListener('online', resume);
    document.addEventListener('visibilitychange', resume);
    return () => { window.removeEventListener('online', resume); document.removeEventListener('visibilitychange', resume); };
  }, [connection, preview, privateSurface, retryConnection]);

  useEffect(() => {
    if (csrf && !preview) openEventStream();
    return () => sourceRef.current?.close();
  }, [csrf, openEventStream, preview]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [tab]);

  useEffect(() => {
    if ((tab !== 'schedule' && tab !== 'today' && tab !== 'calendar') || connection !== 'online' || preview || !csrf || busy) return;
    const initial = window.setTimeout(() => void refreshLiveSchedule(false), 0);
    const timer = window.setInterval(() => void refreshLiveSchedule(false), 5 * 60_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [busy, connection, csrf, preview, refreshLiveSchedule, tab]);

  const send = useCallback(async (retryPending = false) => {
    const pending = pendingDeliveryRef.current;
    const text = retryPending && pending ? pending.text : draft.trim();
    if (!text || busy || sendingRef.current || uncertainOutcome || connection !== 'online') return;
    if (preview) {
      setMessages((current) => [...current, { role: 'user', text }]);
      setDraft('');
      setReasoningParts([{ index: 0, text: 'Reading the current phone preview.' }]);
      setProgressNarrative('');
      setTools([{ title: 'Preview mode', text: 'Live Booker actions are available on the private phone URL.', status: 'completed' }]);
      setStreamingText('This preview shows the finished phone experience. Open the private Booker URL to use live schedule data and actions.');
      return;
    }
    if (pending && pending.text !== text) {
      setError('Use Retry previous message to check its delivery before sending your new draft.');
      return;
    }
    const delivery = pending ?? { id: crypto.randomUUID(), text };
    const optimistic: ChatMessage = { role: 'user', text, optimistic: true };
    if (!pending) {
      setMessages((current) => [...current, optimistic]);
      pendingDeliveryRef.current = delivery;
      streamConfirmedDeliveryIdsRef.current.delete(delivery.id);
      setPendingDelivery(delivery);
    }
    setBusy(true);
    sendingRef.current = true;
    setError('');
    try {
      const { response, data: payload } = await requestJson<{
        error?: string; message?: string; duplicate?: boolean; outcome_uncertain?: boolean;
      }>('/api/v1/assistant/messages', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ client_message_id: delivery.id, text: delivery.text }),
      });
      if (!response.ok) {
        const disposition = deliveryDisposition(
          response.status,
          streamConfirmedDeliveryIdsRef.current.has(delivery.id),
          payload.error,
        );
        if (disposition === 'confirmed') {
          streamConfirmedDeliveryIdsRef.current.delete(delivery.id);
          return;
        }
        if (pendingDeliveryRef.current?.id !== delivery.id) return;
        if (disposition === 'rejected') {
          pendingDeliveryRef.current = null;
          setPendingDelivery(null);
          setBusy(false);
          setError(payload.message || 'The message was not accepted.');
          setMessages((current) => current.filter((message) => !(
            message.optimistic && message.text === delivery.text
          )));
          return;
        }
        throw new Error('ambiguous_delivery');
      }
      if (streamConfirmedDeliveryIdsRef.current.delete(delivery.id)) return;
      if (pendingDeliveryRef.current?.id !== delivery.id) return;
      pendingDeliveryRef.current = null;
      setPendingDelivery(null);
      setDraft(current => current.trim() === delivery.text ? '' : current);
      setMessages((current) => current.map((message) => (
        message.optimistic ? { ...message, optimistic: false } : message
      )));
      if (payload.outcome_uncertain) {
        setBusy(false);
        setUncertainOutcome(
          'This exact message was not replayed because its earlier outcome is uncertain. Review Booker status before continuing.',
        );
        await refreshSnapshot();
        return;
      }
      if (payload.duplicate) {
        setBusy(false);
        await refreshSnapshot();
      }
    } catch {
      if (streamConfirmedDeliveryIdsRef.current.delete(delivery.id)) return;
      if (pendingDeliveryRef.current?.id !== delivery.id) return;
      pendingDeliveryRef.current = delivery;
      setPendingDelivery(delivery);
      setBusy(false);
      setError('Delivery is uncertain. Use Retry previous message to check it safely.');
    } finally {
      sendingRef.current = false;
    }
  }, [busy, connection, csrf, draft, preview, refreshSnapshot, uncertainOutcome]);

  const stop = useCallback(async () => {
    if (stopping) return;
    if (preview) {
      setBusy(false);
      setStreamingText('');
      return;
    }
    setStopping(true);
    try {
      const { response, data } = await requestJson<{ stopping: boolean }>('/api/v1/assistant/stop', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: '{}',
      });
      if (!response.ok) throw new Error('stop_rejected');
      if (!data.stopping) await refreshSnapshot();
    } catch {
      setError('The stop request was not confirmed. The assistant may still be working. Reconnect or try Stop again.');
    } finally {
      setStopping(false);
    }
  }, [csrf, preview, refreshSnapshot, stopping]);

  const newChat = useCallback(async () => {
    if (busy || pendingDeliveryRef.current || uncertainOutcome || connection !== 'online') return;
    if (preview) {
      setMessages([]);
      setStreamingText('');
      setReasoningParts([]);
      setProgressNarrative('');
      setTools([]);
      return;
    }
    setBusy(true);
    try {
      const { response } = await requestJson('/api/v1/assistant/new-chat', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: '{}',
      });
      if (!response.ok) throw new Error();
      // The reset event owns clearing the transcript. A late HTTP reply must
      // not erase a message sent after the stream already reported ready.
      await refreshSnapshot();
    } catch {
      await refreshSnapshot();
      setError('A new chat could not be started yet.');
    }
  }, [busy, connection, csrf, preview, refreshSnapshot, uncertainOutcome]);

  const acknowledgeUncertain = useCallback(async () => {
    if (acknowledgingUncertain) return;
    if (preview) {
      setUncertainOutcome('');
      return;
    }
    setAcknowledgingUncertain(true);
    setError('');
    try {
      const { response, data: payload } = await requestJson<{ bootstrap: Bootstrap }>('/api/v1/assistant/uncertain/acknowledge', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ reviewed: true }),
      });
      if (!response.ok) throw new Error();
      applyBootstrap(payload.bootstrap);
      if (payload.bootstrap.unresolved_reserved_count !== 0) throw new Error();
      pendingDeliveryRef.current = null;
      setPendingDelivery(null);
      setUncertainOutcome('');
    } catch {
      setError('The review could not be recorded safely. Commands remain blocked.');
    } finally {
      setAcknowledgingUncertain(false);
    }
  }, [acknowledgingUncertain, applyBootstrap, csrf, preview]);

  const choosePrompt = (prompt: string) => {
    setSelectedBooking(null);
    setTab('assistant');
    setDraft(current => current.trim() ? current : prompt);
    window.setTimeout(() => inputRef.current?.focus(), 50);
  };

  const cancelBooking = async (event: AgendaEvent) => {
    if (cancellingRef.current || !csrf || preview) return;
    cancellingRef.current = true;
    setCancelling(true);
    setCancellationBooking(event);
    setCancellationStatus('Starting cancellation…');
    try {
      const { response, data } = await requestJson<{ accepted?: boolean; message?: string }>(
        '/api/v1/reservations/cancel', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
          body: JSON.stringify({ request_id: crypto.randomUUID(), reservation: {
            event_id: event.event_id, date: event.date, room: event.room,
            start_time: event.start_time, end_time: event.end_time,
          } }),
        });
      if (!response.ok || !data.accepted) {
        cancellingRef.current = false;
        setCancelling(false);
        setCancellationStatus(data.message || 'Cancellation could not start. Check the schedule.');
      }
      await refreshSnapshot();
    } catch {
      setCancellationStatus('Reconnecting to cancellation progress…');
      // The server owns the accepted job. Reconnect only reads its progress;
      // it never resubmits the click or treats a lost response as a failure.
      await refreshSnapshot();
    }
  };

  if (privateSurface === false) return <RemoteGate />;
  if (privateSurface === null) return <main className="gate-shell" aria-label="Opening Asimut Assistant" />;

  return (
    <main className={`app-shell tab-${tab}`}>
      {tab !== 'today' && <AppHeader tab={tab} booker={booker} connection={connection} newChatDisabled={busy || pendingDelivery !== null || Boolean(uncertainOutcome) || connection !== 'online'} onNewChat={newChat} />}
      {cancellationStatus && <output className="cancellation-progress" aria-live="polite" aria-busy={cancelling}>
        {cancelling && <RefreshCw className="spin-slow" aria-hidden="true" />}
        <div><strong>{cancelling ? 'Cancelling booking' : 'Cancellation update'}</strong>
          {cancellationBooking && <span>{cancellationBooking.room} · {cancellationBooking.start_time}–{cancellationBooking.end_time}</span>}
          <p>{cancellationStatus}</p></div>
        {!cancelling && <button type="button" aria-label="Dismiss cancellation update" onClick={() => setCancellationStatus('')}><X /></button>}
      </output>}
      <ConnectionBanner connection={connection} error={error} onRetry={retryConnection} />
      {uncertainOutcome && (
        <div className="uncertain-outcome" role="alert">
          <AlertTriangle />
          <div>
            <strong>Previous command needs review</strong>
            <p>{uncertainOutcome}</p>
            <div className="uncertain-actions">
              <button onClick={() => setTab('status')} type="button">Review status</button>
              <button disabled={acknowledgingUncertain} onClick={() => void acknowledgeUncertain()} type="button">
                {acknowledgingUncertain ? 'Recording review…' : 'Continue carefully'}
              </button>
            </div>
          </div>
        </div>
      )}
      {error && connection === 'online' && (
        <div className="inline-error" role="alert">
          <AlertTriangle />
          <span>{error}</span>
          <button aria-label="Dismiss error" onClick={() => setError('')} type="button"><X /></button>
        </div>
      )}

      {tab === 'today' && booker && (selectedBooking ? <BookingDetails event={selectedBooking} stale={booker.agenda.stale || !booker.agenda.events.some(event => event.date === selectedBooking.date && event.room === selectedBooking.room && event.start_time === selectedBooking.start_time && event.end_time === selectedBooking.end_time && event.is_reservation)} onClose={() => setSelectedBooking(null)} onAsk={choosePrompt} onCancel={() => void cancelBooking(selectedBooking)} cancelling={cancelling} /> :
        <TodayView booker={booker} refreshing={refreshing} onRefresh={() => void refreshLiveSchedule(true)} onWeek={() => setTab('schedule')} onAsk={choosePrompt} onDetails={setSelectedBooking} preview={preview} />)}
      {tab === 'assistant' && (
        <div className="assistant-view">
          {booker && <ContextPeek booker={booker} onOpenSchedule={() => setTab('schedule')} />}
          {pendingDelivery && !busy && <output className="quiet-notice">
            <span>Check delivery of: {pendingDelivery.text}</span>
            <button type="button" className="quiet-secondary" disabled={connection !== 'online' || Boolean(uncertainOutcome)} onClick={() => void send(true)}>Retry previous message</button>
          </output>}
          {stopping && <output className="quiet-notice">Requesting stop…</output>}
          <Transcript
            busy={busy}
            messages={messages}
            narrative={progressNarrative}
            reasoningParts={reasoningParts}
            streamingText={streamingText}
            tools={tools}
          />
          {!busy && messages.length < 4 && <StarterPrompts onPick={choosePrompt} />}
          <ChatComposer
            busy={busy}
            draft={draft}
            enabled={connection === 'online' && !uncertainOutcome}
            inputRef={inputRef}
            onSend={() => void send()}
            onStop={() => void stop()}
            setDraft={setDraft}
          />
        </div>
      )}
      {tab === 'schedule' && booker && (
        <ScheduleView booker={booker} onCancelBooking={event => void cancelBooking(event)} cancelling={cancelling || busy || Boolean(uncertainOutcome)} onRefresh={() => void refreshLiveSchedule(true)} refreshing={refreshing} />
      )}
      {booker && <div hidden={tab !== 'calendar'}><PhoneCalendar booker={booker} csrf={csrf} active={tab === 'calendar'} editable={connection === 'online' && !busy && !cancelling && !uncertainOutcome && !preview} onSaved={() => void refreshSnapshot()} onRefresh={() => void refreshLiveSchedule(true)} refreshing={refreshing} onCancel={event => void cancelBooking(event)} cancelling={cancelling || busy || Boolean(uncertainOutcome)} /></div>}
      {booker && <div hidden={tab !== 'status'}>
        <StatusView booker={booker} onRefresh={() => void refreshSnapshot()} refreshing={refreshing} standalone={standalone} csrf={csrf} editable={connection === 'online' && !busy && !preview} onSaved={() => void refreshSnapshot()} />
      </div>}
      {tab !== 'assistant' && !booker && (
        <div className="loading-view"><RefreshCw className="spin-slow" /><p>Loading Booker state…</p></div>
      )}
      <BottomNavigation onChange={(next) => { setSelectedBooking(null); setTab(next); }} tab={tab} />
    </main>
  );
}

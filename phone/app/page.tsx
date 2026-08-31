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
  cancellationInstruction,
  compactProgressText,
  deliveryDisposition,
  isFreshSequence,
  nextReconnectDelay,
  reconcileStreamPosition,
  updateProgressNarrative,
  upsertReasoningPart,
} from '@/lib/phone_state';
import { selectedPlanMinutes, selectedPlanSessions } from '@/lib/plan_state';

const PRIVATE_ORIGIN = 'https://lox-pc.tail89d19b.ts.net:10443';
const subscribeBrowserSnapshot = () => () => undefined;

type Tab = 'assistant' | 'schedule' | 'status';
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

type AgendaEvent = {
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

type BookerSnapshot = {
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

type Bootstrap = {
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
  generated_at: '2026-08-31T11:43:00+01:00',
  timezone: 'Europe/London',
  status: { state: 'ready', label: 'Booker ready', pending_mutations: 0 },
  agenda: {
    available: true,
    stale: false,
    observed_at: '2026-08-31T10:43:30Z',
    freshness_reason: '',
    next_event: {
      date: '2026-09-01',
      start_time: '11:00',
      end_time: '11:30',
      title: 'Reservation',
      is_reservation: true,
      room: 'B1.09',
    },
    events: [
      {
        date: '2026-09-01',
        start_time: '11:00',
        end_time: '11:30',
        title: 'Reservation',
        is_reservation: true,
        room: 'B1.09',
      },
      {
        date: '2026-09-01',
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
    collected_at: '2026-08-31T11:43:00+01:00',
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
        <a className="gate-button" href={PRIVATE_ORIGIN}>
          <Home />
          Open my Booker
        </a>
        <p className="gate-note">Tailscale must be connected on this phone.</p>
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
}: {
  booker: BookerSnapshot | null;
  connection: ConnectionState;
  onNewChat: () => void;
  newChatDisabled: boolean;
}) {
  const state = booker?.status.state ?? 'stale';
  const label =
    connection === 'offline'
      ? 'Computer offline'
      : connection === 'connecting'
        ? 'Connecting to Booker…'
        : booker?.status.label ?? 'Booker status unavailable';
  return (
    <header className="top-bar">
      <BrandMark />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-[15px] font-semibold tracking-[-0.01em]">
            Asimut Assistant
          </h1>
          <Badge className="model-badge" variant="outline">
            Terra · medium
          </Badge>
        </div>
        <p className={`app-status state-${connection === 'offline' ? 'offline' : state}`}>
          <span className="status-dot" aria-hidden="true" />
          {label}
        </p>
      </div>
      <Button
        aria-label="Start a new chat"
        className="round-control"
        disabled={newChatDisabled}
        onClick={onNewChat}
        size="icon-lg"
        variant="ghost"
      >
        <MessageSquarePlus />
      </Button>
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
  if (connection === 'online' && !error) return null;
  return (
    <div className={`connection-banner ${connection}`} role={error ? 'alert' : 'status'}>
      {connection === 'offline' ? <WifiOff /> : <RefreshCw className="spin-slow" />}
      <div>
        <strong>{connection === 'offline' ? 'Your PC is not reachable' : 'Connecting'}</strong>
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
  const next = booker.agenda.available && !booker.agenda.stale
    ? booker.agenda.next_event
    : null;
  const plan = booker.plan.available && !booker.plan.stale
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
              ? `${dateLabel(next.date)} · ${next.start_time} · ${next.room}`
              : !booker.agenda.available || booker.agenda.stale
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
              ? `${dateLabel(plan.date)} · ${plan.start_time} · ${plan.room}`
              : !booker.plan.available || booker.plan.stale
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
    latest?.text || cleanNarrative || latestSummary || 'Checking Booker context',
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
              Ask about your schedule, change a practice plan, run one bounded
              booking action, or cancel an exact reservation in natural language.
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
        Clear instructions only. Booker checks live identity and persistence before success.
      </p>
    </div>
  );
}

function ScheduleView({
  booker,
  onAskToCancel,
  onRefresh,
  refreshing,
}: {
  booker: BookerSnapshot;
  onAskToCancel: (event: AgendaEvent) => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const groups = useMemo(() => {
    const result = new Map<string, AgendaEvent[]>();
    for (const event of booker.agenda.events) {
      result.set(event.date, [...(result.get(event.date) ?? []), event]);
    }
    return [...result.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [booker.agenda.events]);
  return (
    <section className="view-page schedule-view" aria-labelledby="schedule-title">
      <div className="view-heading">
        <div>
          <span className="eyebrow">Booked and planned</span>
          <h2 id="schedule-title">Your schedule</h2>
          <p>
            Updated {timeAgo(booker.agenda.observed_at)}
            {booker.agenda.stale ? ' · needs refresh' : ''}
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
            <p>A pending result must be reconciled before another mutation.</p>
          </div>
        </div>
      )}

      {(!booker.agenda.available || booker.agenda.stale) && (
        <div className="attention-card" role="alert">
          <AlertTriangle />
          <div>
            <strong>{booker.agenda.available ? 'Agenda may be out of date' : 'Agenda is unavailable'}</strong>
            <p>Refresh before relying on the list or requesting a cancellation.</p>
          </div>
        </div>
      )}

      <div className="schedule-legend" aria-label="Schedule legend">
        <span><i className="confirmed-key" /> Booked</span>
        <span><i className="potential-key" /> Potential—not booked</span>
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
          <strong>No current agenda events</strong>
          <p>Ask the assistant to refresh the live agenda.</p>
        </div>
      ) : (
        <div className="day-list">
          {groups.map(([date, events]) => (
            <section className="day-section" key={date}>
              <h3>{dateLabel(date, true)}</h3>
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
                    {event.is_reservation && !booker.agenda.stale && (
                      <button onClick={() => onAskToCancel(event)} type="button">
                        Ask assistant to cancel
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
            <h3>Potential plan</h3>
          </div>
          <Badge variant="outline">Not booked yet</Badge>
        </div>
        <p className="plan-summary">{booker.plan.summary}</p>
        {!booker.plan.available || booker.plan.stale ? (
          <output className="attention-card">
            <AlertTriangle />
            <div>
              <strong>Potential plan hidden</strong>
              <p>Refresh Booker data before relying on possible future blocks.</p>
            </div>
          </output>
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
                            {sessions.length > 1 ? `Session ${index + 1} · ` : ''}{candidate.state}
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
}: {
  booker: BookerSnapshot;
  standalone: boolean;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const practice = booker.preferences.practice_plan;
  const time = booker.preferences.time_preferences;
  return (
    <section className="view-page status-view" aria-labelledby="status-title">
      <div className="view-heading">
        <div>
          <span className="eyebrow">Automation and preferences</span>
          <h2 id="status-title">Booker status</h2>
          <p>Checked {timeAgo(booker.health.collected_at)}</p>
        </div>
        <Button aria-label="Refresh status" disabled={refreshing} onClick={onRefresh} size="icon-lg" variant="outline">
          <RefreshCw className={refreshing ? 'spin-slow' : ''} />
        </Button>
      </div>

      <article className={`overview-card state-${booker.status.state}`}>
        <div className="overview-icon"><StatusIcon state={booker.status.state} /></div>
        <div>
          <Badge variant="outline">{booker.status.state.replace('_', ' ')}</Badge>
          <h3>{booker.status.label}</h3>
          <p>
            {booker.plan.available && !booker.plan.stale
              ? booker.plan.summary
              : 'Current automatic plan needs a refresh.'}
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
        <div className="section-heading">
          <div>
            <span className="eyebrow">Current intent</span>
            <h3>Practice settings</h3>
          </div>
          <Settings2 />
        </div>
        <div className="metric-grid">
          <div>
            <span>Daily target</span>
            <strong>{practice.enabled && practice.default_hours ? `${practice.default_hours} hours` : 'Off'}</strong>
          </div>
          <div>
            <span>Preferred time</span>
            <strong>{time.enabled ? `${time.start_time}–${time.end_time}` : 'Any time'}</strong>
          </div>
        </div>
        <p>
          Tell the assistant about a busy week, a future goal, preferred rooms,
          or a date you want switched off. It will resolve exact dated settings.
        </p>
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

      <section className="health-list" aria-label="Detailed Booker health">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Evidence</span>
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
      </section>
    </section>
  );
}

function BottomNavigation({ tab, onChange }: { tab: Tab; onChange: (tab: Tab) => void }) {
  const items: Array<{ id: Tab; label: string; icon: typeof MessageCircle }> = [
    { id: 'assistant', label: 'Assistant', icon: MessageCircle },
    { id: 'schedule', label: 'Schedule', icon: CalendarDays },
    { id: 'status', label: 'Status', icon: HeartPulse },
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
  const [tab, setTab] = useState<Tab>('assistant');
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

  const applyBootstrap = useCallback((payload: Bootstrap) => {
    const position = reconcileStreamPosition(
      streamGenerationRef.current,
      cursorRef.current,
      payload.stream_generation,
      payload.event_cursor,
    );
    streamGenerationRef.current = position.generation;
    cursorRef.current = position.cursor;
    setBooker(payload.booker);
    setMessages(payload.messages);
    setBusy(payload.busy);
    settlePendingDelivery(payload.active_client_message_id ?? undefined);
    if (!payload.busy) {
      streamingRef.current = '';
      setStreamingText('');
    }
    const unresolved = payload.unresolved_reserved_count || 0;
    setUncertainOutcome(unresolved > 0
      ? `${unresolved} earlier command${unresolved === 1 ? ' has' : 's have'} an uncertain outcome after an interruption. Review Booker status before continuing.`
      : '');
  }, [settlePendingDelivery]);

  const refreshSnapshot = useCallback(async () => {
    if (preview) return;
    if (!csrf) return;
    setRefreshing(true);
    try {
      const response = await fetch('/api/v1/refresh', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: '{}',
      });
      if (!response.ok) throw new Error('Booker refresh was not available');
      applyBootstrap(await response.json() as Bootstrap);
      setConnection('online');
      setError('');
    } catch {
      setConnection('offline');
      setError('Reconnect to Tailscale and make sure the Booker PC is awake.');
    } finally {
      setRefreshing(false);
    }
  }, [applyBootstrap, csrf, preview]);

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
  }, [refreshSnapshot, settlePendingDelivery]);

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
    setConnection('connecting');
    setError('');
    try {
      const response = await fetch('/api/v1/session', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client: 'asimut-phone-v1' }),
      });
      if (!response.ok) throw new Error('Private session was rejected');
      const payload = await response.json() as { csrf_token: string; bootstrap: Bootstrap };
      setCsrf(payload.csrf_token);
      applyBootstrap(payload.bootstrap);
      setConnection('online');
      setError('');
      return true;
    } catch {
      setConnection('offline');
      setError('Reconnect to Tailscale and make sure the Booker PC is awake.');
      return false;
    }
  }, [applyBootstrap, preview]);

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimerRef.current !== null) return;
    const attempt = reconnectAttemptsRef.current;
    const delay = nextReconnectDelay(attempt);
    if (delay === null) {
      setConnection('offline');
      setError('Live updates stopped after three retries. Tap Retry when your PC is reachable.');
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
    void connect();
  }, [connect]);

  useEffect(() => {
    const privateHost = window.location.origin === PRIVATE_ORIGIN || preview;
    if (privateHost) queueMicrotask(() => void connect());
    if ('serviceWorker' in navigator && window.isSecureContext) {
      navigator.serviceWorker.register('/sw.js').catch(() => undefined);
    }
    return () => {
      sourceRef.current?.close();
      if (reconnectTimerRef.current !== null) window.clearTimeout(reconnectTimerRef.current);
    };
  }, [connect, preview]);

  useEffect(() => {
    if (csrf && !preview) openEventStream();
    return () => sourceRef.current?.close();
  }, [csrf, openEventStream, preview]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [tab]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || busy || connection !== 'online') return;
    if (preview) {
      setMessages((current) => [...current, { role: 'user', text }]);
      setDraft('');
      setReasoningParts([{ index: 0, text: 'Reading the current phone preview.' }]);
      setProgressNarrative('');
      setTools([{ title: 'Preview mode', text: 'Live Booker actions are available on the private phone URL.', status: 'completed' }]);
      setStreamingText('This preview shows the finished phone experience. Open the private Booker URL to use live schedule data and actions.');
      return;
    }
    if (pendingDelivery && pendingDelivery.text !== text) {
      setError('A previous message has uncertain delivery. Retry that exact message first.');
      return;
    }
    const delivery = pendingDelivery ?? { id: crypto.randomUUID(), text };
    const optimistic: ChatMessage = { role: 'user', text, optimistic: true };
    if (!pendingDelivery) {
      setMessages((current) => [...current, optimistic]);
      pendingDeliveryRef.current = delivery;
      streamConfirmedDeliveryIdsRef.current.delete(delivery.id);
      setPendingDelivery(delivery);
    }
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/v1/assistant/messages', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ client_message_id: delivery.id, text: delivery.text }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as {
          error?: string;
          message?: string;
        };
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
      const payload = await response.json() as {
        duplicate?: boolean;
        outcome_uncertain?: boolean;
      };
      if (streamConfirmedDeliveryIdsRef.current.delete(delivery.id)) return;
      if (pendingDeliveryRef.current?.id !== delivery.id) return;
      pendingDeliveryRef.current = null;
      setPendingDelivery(null);
      setDraft('');
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
      setError('Delivery is uncertain. Tap Send to safely retry this exact message.');
    }
  }, [busy, connection, csrf, draft, pendingDelivery, preview, refreshSnapshot]);

  const stop = useCallback(async () => {
    if (preview) {
      setBusy(false);
      setStreamingText('');
      return;
    }
    try {
      await fetch('/api/v1/assistant/stop', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: '{}',
      });
    } catch {
      setError('The stop request could not reach your PC.');
    }
  }, [csrf, preview]);

  const newChat = useCallback(async () => {
    if (busy || uncertainOutcome) return;
    if (preview) {
      setMessages([]);
      setStreamingText('');
      setReasoningParts([]);
      setProgressNarrative('');
      setTools([]);
      return;
    }
    try {
      const response = await fetch('/api/v1/assistant/new-chat', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: '{}',
      });
      if (!response.ok) throw new Error();
      setBusy(true);
      pendingDeliveryRef.current = null;
      setPendingDelivery(null);
      setMessages([]);
      setStreamingText('');
      setReasoningParts([]);
      setProgressNarrative('');
      setTools([]);
    } catch {
      setError('A new chat could not be started yet.');
    }
  }, [busy, csrf, preview, uncertainOutcome]);

  const acknowledgeUncertain = useCallback(async () => {
    if (acknowledgingUncertain) return;
    if (preview) {
      setUncertainOutcome('');
      return;
    }
    setAcknowledgingUncertain(true);
    setError('');
    try {
      const response = await fetch('/api/v1/assistant/uncertain/acknowledge', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-Asimut-CSRF': csrf },
        body: JSON.stringify({ reviewed: true }),
      });
      if (!response.ok) throw new Error();
      const payload = await response.json() as { bootstrap: Bootstrap };
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
    setDraft(prompt);
    inputRef.current?.focus();
  };

  const askToCancel = (event: AgendaEvent) => {
    const prompt = cancellationInstruction(event);
    setTab('assistant');
    setDraft(prompt);
    window.setTimeout(() => inputRef.current?.focus(), 50);
  };

  if (privateSurface === false) return <RemoteGate />;
  if (privateSurface === null) return <main className="gate-shell" aria-label="Opening Asimut Assistant" />;

  return (
    <main className={`app-shell tab-${tab}`}>
      <AppHeader booker={booker} connection={connection} newChatDisabled={busy || pendingDelivery !== null || Boolean(uncertainOutcome) || connection !== 'online'} onNewChat={newChat} />
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

      {tab === 'assistant' && (
        <div className="assistant-view">
          {booker && <ContextPeek booker={booker} onOpenSchedule={() => setTab('schedule')} />}
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
        <ScheduleView booker={booker} onAskToCancel={askToCancel} onRefresh={() => void refreshSnapshot()} refreshing={refreshing} />
      )}
      {tab === 'status' && booker && (
        <StatusView booker={booker} onRefresh={() => void refreshSnapshot()} refreshing={refreshing} standalone={standalone} />
      )}
      {tab !== 'assistant' && !booker && (
        <div className="loading-view"><RefreshCw className="spin-slow" /><p>Loading Booker state…</p></div>
      )}
      <BottomNavigation onChange={setTab} tab={tab} />
    </main>
  );
}

"""Booking run orchestration.

The coordinator is deliberately conservative:

* every decision starts from a freshly validated agenda/overview;
* extensions are reconciled and processed before new bookings;
* an intent is persisted before a browser write;
* uncertain writes abort further mutations until the agenda is reconciled;
* successful writes are recorded only from the verified remote event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .adapters import (
    configured_room_names,
    context_with_event,
    context_with_target_end,
    context_without_event,
    domain_room_policies,
    missing_configured_rooms,
    policy_context,
    rules_with_server_peak_limit,
    snapshot_from_overview,
)
from .agenda import AgendaEvent, AgendaObservation
from .config import AppConfig
from .database import (
    Database,
    ExtensionRecord,
    IntentRecord,
    RecordNotFoundError,
)
from .errors import (
    AmbiguousBookingResult,
    AuthenticationRequired,
    BookingRejected,
    PageContractError,
)
from .gateway import AsimutGateway, BookingMutation
from .intervals import Interval, floor_to_increment
from .models import AvailabilityState, BookingCandidate, BookingIntent, RoomPolicy
from .overview import OverviewObservation, RoomOverview
from .planner import (
    PlannerOptions,
    bookable_candidates,
    generate_candidates,
    order_snipe_candidates,
    upcoming_snipe_candidates,
)
from .policies import (
    BookingRules,
    PolicyContext,
    evaluate_intent,
    horizon_release_at,
    released_end_minute,
)

LOG = logging.getLogger(__name__)
Emitter = Callable[[str, str, dict[str, object]], None]


@dataclass(slots=True)
class RunSummary:
    run_id: str
    trigger: str
    dry_run: bool
    status: str = "running"
    bookings_created: int = 0
    extensions_advanced: int = 0
    extensions_completed: int = 0
    candidates_seen: int = 0
    bookable_seen: int = 0
    snipes_seen: int = 0
    configured_rooms_missing: set[str] = field(default_factory=set)
    booked: list[dict[str, object]] = field(default_factory=list)
    extended: list[dict[str, object]] = field(default_factory=list)
    rejected: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "trigger": self.trigger,
            "dry_run": self.dry_run,
            "status": self.status,
            "bookings_created": self.bookings_created,
            "extensions_advanced": self.extensions_advanced,
            "extensions_completed": self.extensions_completed,
            "candidates_seen": self.candidates_seen,
            "bookable_seen": self.bookable_seen,
            "snipes_seen": self.snipes_seen,
            "configured_rooms_missing": sorted(self.configured_rooms_missing),
            "booked": list(self.booked),
            "extended": list(self.extended),
            "rejected": list(self.rejected),
            "notes": list(self.notes),
        }


class BookingCoordinator:
    """Execute one bounded run against a single gateway and database."""

    def __init__(
        self,
        config: AppConfig,
        database: Database,
        gateway: AsimutGateway,
        *,
        emit: Emitter | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.database = database
        self.gateway = gateway
        self.zone = ZoneInfo(config.browser.timezone)
        self.emit = emit or self._log_emit
        self.clock = clock or (lambda: datetime.now(self.zone))
        self.rules = config.to_domain_rules()
        self.room_policies = domain_room_policies(config)
        self.policy_by_room = {item.room_id: item for item in self.room_policies}

    @staticmethod
    def _log_emit(level: str, message: str, detail: dict[str, object]) -> None:
        numeric = getattr(logging, level.upper(), logging.INFO)
        LOG.log(numeric, "%s %s", message, detail if detail else "")

    def _event(
        self,
        level: str,
        message: str,
        **detail: object,
    ) -> None:
        self.emit(level, message, detail)

    def run(
        self,
        *,
        trigger: str = "manual",
        dry_run: bool = False,
        extensions_only: bool = False,
        extension_ids: frozenset[str] | None = None,
    ) -> RunSummary:
        run = self.database.start_run(trigger=trigger, dry_run=dry_run)
        summary = RunSummary(run.id, trigger, dry_run)
        self._event("info", "run_started", run_id=run.id, dry_run=dry_run)

        try:
            self.gateway.verify_authenticated()
            self.database.set_health(
                "authentication",
                "healthy",
                message="Signed-in Asimut agenda verified",
            )
            now = self.clock()
            today = now.astimezone(self.zone).date()
            agenda = self.gateway.observe_agenda(
                today,
                today + timedelta(days=self.config.preferences.max_horizon_days + 1),
            )
            self._record_agenda(run.id, agenda, now)
            context = policy_context(agenda, now=now, rules=self.rules)
            self._reconcile_uncertain_intents(agenda, run.id, summary)

            context = self._process_extensions(
                run_id=run.id,
                agenda=agenda,
                context=context,
                summary=summary,
                dry_run=dry_run,
                extension_ids=extension_ids,
            )
            if not extensions_only:
                server_context = context
                context = self._reserve_pending_extension_targets(context)
                observations, candidates, rules_by_day = self._scan_candidates(
                    run_id=run.id,
                    agenda=agenda,
                    context=context,
                    server_context=server_context,
                    summary=summary,
                )
                self._process_new_bookings(
                    run_id=run.id,
                    observations=observations,
                    candidates=candidates,
                    rules_by_day=rules_by_day,
                    context=context,
                    summary=summary,
                    dry_run=dry_run,
                )

            if dry_run:
                status = "no_op"
                summary.notes.append("Dry run: no Asimut writes were attempted.")
            elif summary.bookings_created or summary.extensions_advanced:
                status = "partial" if summary.rejected else "succeeded"
            elif summary.rejected:
                status = "partial"
            else:
                status = "no_op"
            summary.status = status
            self.database.finish_run(
                run.id,
                status=status,
                bookings_created=summary.bookings_created,
                extensions_completed=summary.extensions_completed,
                summary=summary.as_dict(),
            )
            self.database.set_health(
                "last_run",
                "healthy" if status != "partial" else "degraded",
                message=f"Run {status}",
                details=summary.as_dict(),
            )
            self._event("info", "run_finished", **summary.as_dict())
            return summary
        except Exception as exc:
            summary.status = "failed"
            code = type(exc).__name__
            try:
                self.database.finish_run(
                    run.id,
                    status="failed",
                    bookings_created=summary.bookings_created,
                    extensions_completed=summary.extensions_completed,
                    error_code=code,
                    error_message=str(exc),
                    summary=summary.as_dict(),
                )
                self.database.set_health(
                    "last_run",
                    "unhealthy",
                    message=str(exc),
                    details={"error_code": code, "run_id": run.id},
                )
            except Exception:
                LOG.exception("Could not persist failed run state")
            self._event(
                "error",
                "run_failed",
                run_id=run.id,
                error_code=code,
                error=str(exc),
            )
            raise

    def _record_agenda(
        self,
        run_id: str,
        agenda: AgendaObservation,
        observed_at: datetime,
    ) -> None:
        record = self.database.record_observation(
            kind="agenda",
            status="valid",
            run_id=run_id,
            observation_key=f"{run_id}:agenda",
            observed_at=observed_at,
            page_url=self.gateway.settings.agenda_url,
            requested_date=observed_at.astimezone(self.zone).date(),
            observed_date=agenda.first_date,
            contract_version=agenda.contract_version,
            payload={
                "first_date": agenda.first_date,
                "last_date": agenda.last_date,
                "day_count": agenda.day_count,
                "event_count": len(agenda.events),
                "rolling_quota_remaining": agenda.rolling_quota_remaining,
            },
        )
        for item in agenda.events:
            self._upsert_agenda_event(item, record.id, observed_at)

    def _upsert_agenda_event(
        self,
        item: AgendaEvent,
        observation_id: str | None,
        observed_at: datetime,
    ) -> str:
        key = f"asimut:{item.event_id}"
        self.database.upsert_event(
            event_key=key,
            external_event_id=item.event_id,
            event_date=item.event_date,
            room_id=item.room_name,
            start_minute=item.start_minute,
            end_minute=item.end_minute,
            event_type="reservation" if item.is_reservation else "calendar",
            status=item.status,
            title=item.title,
            is_cancelled=item.is_cancelled,
            source_observation_id=observation_id,
            observed_at=observed_at,
            payload={
                "location_id": item.location_id,
                "arrangement_href": item.arrangement_href,
            },
        )
        return key

    def _record_overview(
        self,
        run_id: str,
        observation: OverviewObservation,
        observed_at: datetime,
    ) -> None:
        self.database.record_observation(
            kind="overview",
            status="valid",
            run_id=run_id,
            observation_key=(
                f"{run_id}:overview:{observation.observed_date.isoformat()}:"
                f"{observed_at.isoformat(timespec='microseconds')}"
            ),
            observed_at=observed_at,
            page_url=self.gateway.settings.overview_url,
            requested_date=observation.observed_date,
            observed_date=observation.observed_date,
            contract_version=observation.contract_version,
            payload={
                "room_count": len(observation.rooms),
                "rolling_quota_remaining": observation.rolling_quota_remaining,
                "peak_quota_remaining": observation.peak_quota_remaining,
                "grid_start": observation.calibration.origin_minute,
                "grid_end": observation.calibration.end_minute,
            },
        )

    def _reconcile_uncertain_intents(
        self,
        agenda: AgendaObservation,
        run_id: str,
        summary: RunSummary,
    ) -> None:
        uncertain: list[IntentRecord] = []
        for status in ("submitting", "ambiguous"):
            uncertain.extend(self.database.list_intents(status=status))
        for intent in uncertain:
            matches = [
                item
                for item in agenda.find_exact(
                    event_date=date.fromisoformat(intent.requested_date),
                    room_name=intent.room_id,
                    start_minute=intent.start_minute,
                    end_minute=intent.end_minute,
                )
                if item.is_reservation
            ]
            if len(matches) == 1:
                item = matches[0]
                event_key = self._upsert_agenda_event(item, None, self.clock())
                self.database.update_intent_status(
                    intent.id,
                    "verified",
                    event_key=event_key,
                    verified_at=self.clock(),
                )
                if (
                    intent.target_end_minute is not None
                    and intent.target_end_minute > item.end_minute
                ):
                    self.database.upsert_extension(
                        intent_id=intent.id,
                        event_key=event_key,
                        current_end_minute=item.end_minute,
                        target_end_minute=intent.target_end_minute,
                        status="pending",
                    )
                summary.notes.append(
                    f"Reconciled uncertain intent {intent.id} as event {item.event_id}."
                )
            elif intent.status == "submitting":
                self.database.update_intent_status(
                    intent.id,
                    "ambiguous",
                    error_code="RemoteResultUnknown",
                    error_message=(
                        "No exact agenda match was found after an interrupted "
                        "submission; automatic retry is disabled."
                    ),
                )

    def _scan_candidates(
        self,
        *,
        run_id: str,
        agenda: AgendaObservation,
        context: PolicyContext,
        server_context: PolicyContext | None = None,
        summary: RunSummary,
    ) -> tuple[
        dict[date, OverviewObservation],
        list[BookingCandidate],
        dict[date, BookingRules],
    ]:
        observations: dict[date, OverviewObservation] = {}
        rules_by_day: dict[date, BookingRules] = {}
        candidates: list[BookingCandidate] = []
        now = self.clock()
        today = now.astimezone(self.zone).date()
        configured = configured_room_names(self.config)

        for offset in range(self.config.preferences.max_horizon_days + 1):
            target = today + timedelta(days=offset)
            if not self.config.is_date_enabled(target):
                continue
            observed_at = self.clock()
            overview = self.gateway.observe_overview(target)
            self._record_overview(run_id, overview, observed_at)
            observations[target] = overview
            missing = missing_configured_rooms(overview, configured)
            summary.configured_rooms_missing.update(missing)

            active_rules = rules_with_server_peak_limit(
                self.rules,
                server_context if server_context is not None else context,
                target_date=target,
                agenda=agenda,
            )
            rules_by_day[target] = active_rules
            windows = tuple(
                Interval(item.start_minute, item.end_minute)
                for item in self.config.preferred_windows_for(target)
            )
            snapshot = snapshot_from_overview(overview, observed_at=observed_at)
            candidates.extend(
                generate_candidates(
                    snapshot,
                    self.room_policies,
                    now,
                    rules=active_rules,
                    context=context,
                    options=PlannerOptions(
                        desired_duration=self.config.rules.default_duration_minutes,
                        preference_windows=windows,
                        strict_preferences=self.config.preferences.strict,
                        include_blocked=True,
                    ),
                )
            )

        summary.candidates_seen = len(candidates)
        if summary.configured_rooms_missing:
            observed_any = configured.difference(summary.configured_rooms_missing)
            severity = "degraded" if observed_any else "unhealthy"
            self.database.set_health(
                "room_inventory",
                severity,
                message="Some configured rooms are absent from the live overview",
                details={
                    "missing": sorted(summary.configured_rooms_missing),
                    "observed_configured_count": len(observed_any),
                },
            )
        else:
            self.database.set_health(
                "room_inventory",
                "healthy",
                message="Every configured room was observed by stable identity",
                details={"configured_count": len(configured)},
            )
        return observations, candidates, rules_by_day

    def _process_new_bookings(
        self,
        *,
        run_id: str,
        observations: dict[date, OverviewObservation],
        candidates: list[BookingCandidate],
        rules_by_day: dict[date, BookingRules],
        context: PolicyContext,
        summary: RunSummary,
        dry_run: bool,
    ) -> PolicyContext:
        now = self.clock()
        snipes = upcoming_snipe_candidates(
            candidates,
            now,
            within=timedelta(seconds=self.config.schedule.lead_seconds),
        )
        bookable = _bookable_candidates_at(candidates, now)
        summary.snipes_seen = len(snipes)
        summary.bookable_seen = len(bookable)

        # An imminent release gets first use of the browser so pre-fill is not
        # delayed by opportunistic bookings.  Natural keys remove duplicates.
        ordered: list[BookingCandidate] = []
        seen: set[tuple[str, str, int]] = set()
        for item in [*order_snipe_candidates(snipes), *bookable]:
            if item.intent.key in seen:
                continue
            seen.add(item.intent.key)
            ordered.append(item)

        if dry_run:
            selected, _ = _simulate_dry_run_candidates(
                ordered,
                context=context,
                policy_by_room=self.policy_by_room,
                rules_by_day=rules_by_day,
                now=now,
                maximum=self.config.preferences.max_bookings_per_run,
            )
            for candidate in selected:
                summary.notes.append(
                    "Would book "
                    f"{candidate.date} {candidate.room_id} "
                    f"{_time(candidate.interval.start)}-"
                    f"{_time(candidate.interval.end)}"
                    + (
                        f" and extend to {_time(candidate.target_interval.end)}"
                        if candidate.target_interval.end > candidate.interval.end
                        else ""
                    )
                )
            return context

        for candidate in ordered:
            if summary.bookings_created >= self.config.preferences.max_bookings_per_run:
                break
            active_rules = rules_by_day[candidate.date]
            fresh_now = self.clock()
            future_release = candidate.release_at > fresh_now
            target_decision = evaluate_intent(
                BookingIntent(
                    date=candidate.date,
                    room_id=candidate.room_id,
                    interval=candidate.target_interval,
                    target_end=candidate.target_interval.end,
                ),
                self.policy_by_room[candidate.room_id],
                fresh_now,
                rules=active_rules,
                context=context,
                check_horizon=False,
            )
            if not target_decision.allowed:
                # Do not create a short horizon-edge fragment whose intended
                # tail has become impossible after an earlier mutation.
                continue
            decision = evaluate_intent(
                candidate.intent,
                self.policy_by_room[candidate.room_id],
                fresh_now,
                rules=active_rules,
                context=context,
                check_horizon=not future_release,
            )
            if not decision.allowed:
                # OUTSIDE_HORIZON is intentionally ignored only for a queued
                # pre-release form; every other rule remains a hard block.
                if not (
                    future_release and decision.state is AvailabilityState.FREE_OUTSIDE_HORIZON
                ):
                    continue

            result = self._submit_candidate(
                run_id=run_id,
                candidate=candidate,
                not_before=candidate.release_at if future_release else None,
                context=context,
                active_rules=active_rules,
                now=fresh_now,
                summary=summary,
            )
            if result is not None:
                context = context_with_event(context, result.event)
                if candidate.target_interval.end > result.event.end_minute:
                    context = context_with_target_end(
                        context,
                        result.event,
                        candidate.target_interval.end,
                    )
        return context

    def _reserve_pending_extension_targets(
        self,
        context: PolicyContext,
    ) -> PolicyContext:
        """Protect pending extension tails from later booking plans."""

        pending: dict[str, ExtensionRecord] = {}
        for status in ("pending", "retryable"):
            for extension in self.database.list_extensions(status=status):
                pending[extension.id] = extension

        result = context
        for extension in pending.values():
            if extension.event_key is None:
                continue
            try:
                event_record = self.database.get_event(extension.event_key)
            except RecordNotFoundError:
                continue
            event_id = event_record.external_event_id
            if event_id is None:
                continue
            matches = [
                event
                for event in result.events
                if event.event_id == event_id and event.is_reservation and not event.cancelled
            ]
            if len(matches) != 1:
                continue
            current = matches[0]
            if extension.target_end_minute <= current.interval.end:
                continue
            agenda_event = AgendaEvent(
                event_id=event_id,
                event_date=current.date,
                start_minute=current.interval.start,
                end_minute=current.interval.end,
                title="Reservation",
                room_name=current.room_id,
                location_id=None,
                status="provisional",
                arrangement_href=None,
            )
            result = context_with_target_end(
                result,
                agenda_event,
                extension.target_end_minute,
            )
        return result

    def _intent_for_candidate(
        self,
        run_id: str,
        candidate: BookingCandidate,
    ) -> tuple[IntentRecord, BookingIntent] | None:
        key = f"create|{candidate.date.isoformat()}|{candidate.room_id}|{candidate.interval.start}"
        try:
            existing = self.database.get_intent_by_key(key)
        except RecordNotFoundError:
            existing = None
        if existing is None:
            record = self.database.create_intent(
                kind="create",
                requested_date=candidate.date,
                room_id=candidate.room_id,
                start_minute=candidate.interval.start,
                end_minute=candidate.interval.end,
                target_end_minute=candidate.target_interval.end,
                intent_key=key,
                run_id=run_id,
                status="planned",
                payload={
                    "release_at": candidate.release_at,
                    "preferred": candidate.preferred,
                    "room_priority": candidate.room_priority,
                },
            )
            return record, candidate.intent

        if existing.status in {
            "verified",
            "submitting",
            "ambiguous",
            "cancelled",
        }:
            return None
        # A previously rejected minimum interval remains the canonical
        # idempotent request even if more of its target is now released.
        intent = BookingIntent(
            date=date.fromisoformat(existing.requested_date),
            room_id=existing.room_id,
            interval=Interval(existing.start_minute, existing.end_minute),
            target_end=existing.target_end_minute or existing.end_minute,
        )
        return existing, intent

    def _submit_candidate(
        self,
        *,
        run_id: str,
        candidate: BookingCandidate,
        not_before: datetime | None,
        context: PolicyContext,
        active_rules: BookingRules,
        now: datetime,
        summary: RunSummary,
    ) -> BookingMutation | None:
        resolved = self._intent_for_candidate(run_id, candidate)
        if resolved is None:
            return None
        record, intent = resolved
        target_decision = evaluate_intent(
            BookingIntent(
                date=intent.date,
                room_id=intent.room_id,
                interval=intent.target_interval,
                target_end=intent.target_interval.end,
            ),
            self.policy_by_room[intent.room_id],
            now,
            rules=active_rules,
            context=context,
            check_horizon=False,
        )
        if not target_decision.allowed:
            self.database.update_intent_status(
                record.id,
                "retryable",
                error_code="PolicyRevalidationBlocked",
                error_message=", ".join(reason.value for reason in target_decision.reasons),
            )
            return None
        actual_release = horizon_release_at(
            intent.date,
            intent.interval.end,
            self.policy_by_room[intent.room_id].horizon_days,
        )
        if actual_release > self.clock():
            not_before = actual_release
        self.database.update_intent_status(record.id, "revalidating")
        self.database.update_intent_status(record.id, "ready")

        def mark_submitting() -> None:
            self.database.update_intent_status(
                record.id,
                "submitting",
                expected_status="ready",
                increment_attempt=True,
            )

        try:
            mutation = self.gateway.create_booking(
                target_date=intent.date,
                room_name=intent.room_id,
                start_minute=intent.interval.start,
                end_minute=intent.interval.end,
                not_before=not_before,
                before_submit=mark_submitting,
            )
        except BookingRejected as exc:
            self.database.update_intent_status(
                record.id,
                "rejected",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            summary.rejected.append(
                {
                    "action": "create",
                    "date": intent.date.isoformat(),
                    "room": intent.room_id,
                    "start": intent.interval.start,
                    "reason": str(exc),
                }
            )
            self._event(
                "warning",
                "booking_rejected",
                room=intent.room_id,
                date=intent.date.isoformat(),
                reason=str(exc),
            )
            return None
        except AmbiguousBookingResult as exc:
            self.database.update_intent_status(
                record.id,
                "ambiguous",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        except (PageContractError, AuthenticationRequired) as exc:
            # Gateway guarantees post-click uncertainty is promoted to
            # AmbiguousBookingResult.  These failures therefore happened
            # before a write and are safe to retry in a future run.
            self.database.update_intent_status(
                record.id,
                "retryable",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        except Exception as exc:
            # The gateway promotes every failure after Save is attempted to an
            # ambiguity.  Any other exception is pre-write and retryable.
            self.database.update_intent_status(
                record.id,
                "retryable",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            raise

        event_key = self._upsert_agenda_event(mutation.event, None, mutation.verified_at)
        self.database.update_intent_status(
            record.id,
            "verified",
            event_key=event_key,
            verified_at=mutation.verified_at,
        )
        if intent.target_interval.end > mutation.event.end_minute:
            self.database.upsert_extension(
                intent_id=record.id,
                event_key=event_key,
                current_end_minute=mutation.event.end_minute,
                target_end_minute=intent.target_interval.end,
                status="pending",
                next_attempt_at=horizon_release_at(
                    intent.date,
                    min(
                        intent.target_interval.end,
                        mutation.event.end_minute + self.rules.booking_increment,
                    ),
                    self.policy_by_room[intent.room_id].horizon_days,
                ),
            )
        summary.bookings_created += 1
        summary.booked.append(
            {
                "event_id": mutation.event.event_id,
                "date": intent.date.isoformat(),
                "room": intent.room_id,
                "start": mutation.event.start_minute,
                "end": mutation.event.end_minute,
                "target_end": intent.target_interval.end,
            }
        )
        self._event(
            "info",
            "booking_verified",
            **summary.booked[-1],
        )
        return mutation

    def _process_extensions(
        self,
        *,
        run_id: str,
        agenda: AgendaObservation,
        context: PolicyContext,
        summary: RunSummary,
        dry_run: bool,
        extension_ids: frozenset[str] | None = None,
    ) -> PolicyContext:
        extensions: dict[str, ExtensionRecord] = {}
        for status in ("pending", "retryable", "blocked", "in_progress"):
            for item in self.database.list_extensions(status=status):
                if extension_ids is not None and item.id not in extension_ids:
                    continue
                extensions[item.id] = item

        for extension in sorted(
            extensions.values(),
            key=lambda item: (item.next_attempt_at or "", item.created_at),
        ):
            try:
                context = self._process_one_extension(
                    run_id=run_id,
                    extension=extension,
                    agenda=agenda,
                    context=context,
                    summary=summary,
                    dry_run=dry_run,
                )
            except AmbiguousBookingResult:
                # No further write is safe until quota and the event end have
                # been re-read on the next run.
                raise
            except (BookingRejected, PageContractError) as exc:
                self.database.update_extension(
                    extension.id,
                    status="retryable",
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
                summary.rejected.append(
                    {
                        "action": "extend",
                        "extension_id": extension.id,
                        "reason": str(exc),
                    }
                )
        return context

    def _process_one_extension(
        self,
        *,
        run_id: str,
        extension: ExtensionRecord,
        agenda: AgendaObservation,
        context: PolicyContext,
        summary: RunSummary,
        dry_run: bool,
    ) -> PolicyContext:
        intent = self.database.get_intent(extension.intent_id)
        if extension.event_key is None:
            self.database.update_extension(
                extension.id,
                status="blocked",
                error_code="MissingEventIdentity",
                error_message="Extension has no verified Asimut event identity",
            )
            return context
        event_record = self.database.get_event(extension.event_key)
        event_id = event_record.external_event_id
        if event_id is None:
            self.database.update_extension(
                extension.id,
                status="blocked",
                error_code="MissingExternalEventId",
                error_message="Verified event has no Asimut event ID",
            )
            return context

        matches = agenda.find_exact(event_id=event_id)
        if len(matches) != 1:
            self.database.update_extension(
                extension.id,
                status="blocked",
                error_code="EventNotInAgenda",
                error_message=("The booking is absent or ambiguous in the refreshed agenda"),
            )
            return context
        current = matches[0]
        if not current.is_reservation:
            self.database.update_extension(
                extension.id,
                status="blocked",
                error_code="EventIsNotReservation",
                error_message="Only a verified Reservation may be extended",
            )
            return context
        if (
            current.event_date != date.fromisoformat(intent.requested_date)
            or current.room_name != intent.room_id
            or current.start_minute != intent.start_minute
        ):
            self.database.update_extension(
                extension.id,
                status="blocked",
                error_code="EventIdentityChanged",
                error_message="The Asimut event no longer matches its booking intent",
            )
            return context

        current_end = current.end_minute
        if current_end < extension.current_end_minute:
            self.database.update_extension(
                extension.id,
                status="blocked",
                error_code="EventWasShortened",
                error_message=(
                    "The Asimut event ends earlier than the last verified "
                    "extension state; automatic editing is disabled."
                ),
            )
            return context
        if current_end >= extension.target_end_minute:
            self.database.update_extension(
                extension.id,
                status="complete",
                current_end_minute=extension.target_end_minute,
            )
            return context

        policy = self.policy_by_room.get(intent.room_id)
        if policy is None:
            self.database.update_extension(
                extension.id,
                status="blocked",
                error_code="UnknownRoomPolicy",
                error_message="The room no longer has an enabled horizon policy",
            )
            return context
        now = self.clock()
        available_end = released_end_minute(
            current.event_date,
            policy.horizon_days,
            now,
            increment=self.rules.booking_increment,
        )
        desired_end = min(extension.target_end_minute, available_end)
        desired_end = floor_to_increment(desired_end, self.rules.booking_increment)
        if desired_end <= current_end:
            next_end = min(
                extension.target_end_minute,
                current_end + self.rules.booking_increment,
            )
            self.database.update_extension(
                extension.id,
                status="pending",
                current_end_minute=current_end,
                next_attempt_at=horizon_release_at(
                    current.event_date,
                    next_end,
                    policy.horizon_days,
                ),
            )
            return context

        overview = self.gateway.observe_overview(current.event_date)
        self._record_overview(run_id, overview, self.clock())
        room = overview.rooms_by_name.get(intent.room_id)
        if room is None:
            raise PageContractError(f"extension room {intent.room_id} is absent from overview")
        desired_end = self._visually_free_extension_end(room, current_end, desired_end)
        if desired_end <= current_end:
            self.database.update_extension(
                extension.id,
                status="blocked",
                current_end_minute=current_end,
                error_code="RoomTailOccupied",
                error_message="The room is no longer free after the booking",
            )
            return context

        active_rules = rules_with_server_peak_limit(
            self.rules,
            context,
            target_date=current.event_date,
            agenda=agenda,
        )
        without_current = context_without_event(
            context,
            event_id=event_id,
            current_duration=current.end_minute - current.start_minute,
        )
        proposed: BookingIntent | None = None
        decision_reason = ""
        end = desired_end
        while end > current_end:
            item = BookingIntent(
                date=current.event_date,
                room_id=intent.room_id,
                interval=Interval(current.start_minute, end),
                target_end=extension.target_end_minute,
            )
            decision = evaluate_intent(
                item,
                policy,
                now,
                rules=active_rules,
                context=without_current,
                check_horizon=True,
            )
            if decision.allowed:
                proposed = item
                break
            decision_reason = ", ".join(reason.value for reason in decision.reasons)
            end -= self.rules.booking_increment
        if proposed is None or proposed.interval.end <= current_end:
            self.database.update_extension(
                extension.id,
                status="blocked",
                current_end_minute=current_end,
                error_code="ExtensionPolicyBlocked",
                error_message=decision_reason or "No safe extension interval remains",
            )
            return context

        if dry_run:
            summary.notes.append(
                f"Would extend event {event_id} from {_time(current_end)} "
                f"to {_time(proposed.interval.end)}."
            )
            return context

        self.database.update_extension(
            extension.id,
            status="in_progress",
            current_end_minute=current_end,
            increment_attempt=True,
        )
        try:
            mutation = self.gateway.extend_booking(
                event_id=event_id,
                target_date=current.event_date,
                room_name=intent.room_id,
                start_minute=current.start_minute,
                current_end_minute=current_end,
                new_end_minute=proposed.interval.end,
            )
        except AmbiguousBookingResult as exc:
            self.database.update_extension(
                extension.id,
                status="retryable",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        status = (
            "complete" if mutation.event.end_minute >= extension.target_end_minute else "pending"
        )
        next_attempt = None
        if status == "pending":
            next_attempt = horizon_release_at(
                current.event_date,
                min(
                    extension.target_end_minute,
                    mutation.event.end_minute + self.rules.booking_increment,
                ),
                policy.horizon_days,
            )
        event_key = self._upsert_agenda_event(mutation.event, None, mutation.verified_at)
        if event_key != extension.event_key:
            raise AmbiguousBookingResult(
                "extension reconciliation returned a different event identity"
            )
        self.database.update_extension(
            extension.id,
            status=status,
            current_end_minute=mutation.event.end_minute,
            next_attempt_at=next_attempt,
        )
        summary.extensions_advanced += 1
        if status == "complete":
            summary.extensions_completed += 1
        summary.extended.append(
            {
                "event_id": event_id,
                "date": current.event_date.isoformat(),
                "room": intent.room_id,
                "start": current.start_minute,
                "previous_end": current_end,
                "end": mutation.event.end_minute,
                "target_end": extension.target_end_minute,
                "complete": status == "complete",
            }
        )
        self._event("info", "extension_verified", **summary.extended[-1])
        return context_with_event(context, mutation.event)

    def _visually_free_extension_end(
        self,
        room: RoomOverview,
        current_end: int,
        desired_end: int,
    ) -> int:
        for free in room.free:
            if free.start <= current_end < free.end:
                return floor_to_increment(
                    min(desired_end, free.end),
                    self.rules.booking_increment,
                )
        return current_end


def _simulate_dry_run_candidates(
    candidates: list[BookingCandidate],
    *,
    context: PolicyContext,
    policy_by_room: dict[str, RoomPolicy],
    rules_by_day: dict[date, BookingRules],
    now: datetime,
    maximum: int,
) -> tuple[list[BookingCandidate], PolicyContext]:
    """Select a mutually compatible dry-run plan without claiming writes.

    Each accepted candidate reserves its full target in a temporary policy
    context. This makes dry-run output match the choices a write-capable run
    could actually make instead of listing overlapping rooms or exceeding
    quota across several hypothetical bookings.
    """

    selected: list[BookingCandidate] = []
    simulated = context
    for candidate in candidates:
        if len(selected) >= maximum:
            break
        policy = policy_by_room.get(candidate.room_id)
        active_rules = rules_by_day.get(candidate.date)
        if policy is None or active_rules is None:
            continue
        future_release = candidate.release_at > now
        target = BookingIntent(
            date=candidate.date,
            room_id=candidate.room_id,
            interval=candidate.target_interval,
            target_end=candidate.target_interval.end,
        )
        target_decision = evaluate_intent(
            target,
            policy,
            now,
            rules=active_rules,
            context=simulated,
            check_horizon=False,
        )
        if not target_decision.allowed:
            continue
        submitted_decision = evaluate_intent(
            candidate.intent,
            policy,
            now,
            rules=active_rules,
            context=simulated,
            check_horizon=not future_release,
        )
        if not submitted_decision.allowed and not (
            future_release and submitted_decision.state is AvailabilityState.FREE_OUTSIDE_HORIZON
        ):
            continue

        synthetic = AgendaEvent(
            event_id=f"dry-run:{len(selected)}",
            event_date=candidate.date,
            start_minute=candidate.interval.start,
            end_minute=candidate.interval.end,
            title="Reservation",
            room_name=candidate.room_id,
            location_id=None,
            status="provisional",
            arrangement_href=None,
        )
        simulated = context_with_event(simulated, synthetic)
        if candidate.target_interval.end > candidate.interval.end:
            simulated = context_with_target_end(
                simulated,
                synthetic,
                candidate.target_interval.end,
            )
        selected.append(candidate)
    return selected, simulated


def _bookable_candidates_at(
    candidates: list[BookingCandidate],
    now: datetime,
) -> list[BookingCandidate]:
    """Refresh horizon-only classifications after a potentially slow scan."""

    refreshed = [
        replace(
            candidate,
            state=AvailabilityState.BOOKABLE,
            reasons=(),
        )
        if candidate.state is AvailabilityState.FREE_OUTSIDE_HORIZON and candidate.release_at <= now
        else candidate
        for candidate in candidates
    ]
    return bookable_candidates(refreshed)


def _time(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"

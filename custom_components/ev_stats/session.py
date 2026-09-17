"""The charging-session lifecycle.

A session opens when the car has been drawing for a minute, gathers evidence
while it runs, and at the end asks `classify.py` where it happened.

Three things here are less obvious than they look.

**Live routing.** Energy used to accrue to `unknown` and move once, at the end,
because a fix read at charge start described the previous parking spot. That
stopped being true once the tracker began refreshing while driving, so when the
fix is provably fresh the energy is filed from the first minute instead - a
charge that has already been identified as the workplace should not spend three
hours displayed as unattributed. The safety property is kept rather than traded
away: anything less than certain still routes to `unknown`, the closing verdict
still runs in full and moves the energy if it disagrees, and the watchdog puts
the route back to `unknown` whenever no session is open.

**The tail sweep.** The opening trigger waits a minute, and the integrator
lands the plug-in interval in one lump before that, so real session energy
accrues before a session exists - a tenth of a kWh, every time. A confident
verdict may claim it, capped, and never below the floor.

**The floor.** Energy a past session deliberately left unattributed is
protected from every later sweep. Without it, one confident session quietly
absorbs an earlier unresolved one, and the unattributed total - the number that
says how often this system does not know - reads zero for the wrong reason.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .classify import (
    CONFIDENCE_HIGH,
    SUPPLY_AC,
    SUPPLY_DC,
    Classification,
    Evidence,
    Thresholds,
    classify,
)
from .buckets import ZERO
from .const import (
    BUCKET_HOME,
    BUCKET_UNKNOWN,
    CHARGE_ON_KW,
    CONF_BATTERY,
    CONF_CHARGE_CURRENT,
    CONF_CHARGE_VOLTAGE,
    CONF_CHARGER_CONNECTION,
    CONF_DC_MIN_KW,
    CONF_HOME_RATIO_NO,
    CONF_HOME_RATIO_YES,
    CONF_MIN_HOURS,
    CONF_MIN_KWH,
    CONF_SUPPLY_SOURCE,
    DEFAULT_DC_MIN_KW,
    DEFAULT_HOME_RATIO_NO,
    DEFAULT_HOME_RATIO_YES,
    DEFAULT_MIN_HOURS,
    DEFAULT_MIN_KWH,
    DISCONNECT_DEBOUNCE_S,
    DOMAIN,
    END_DEBOUNCE_S,
    EVENT_SESSION_RECORDED,
    SECTION_CAR,
    SECTION_THRESHOLDS,
    START_DEBOUNCE_S,
    STUCK_SESSION_S,
    SWEEP_CAP_KWH,
    SWEEP_MIN_KWH,
    WATCHDOG_INTERVAL_S,
)
from .helpers import numeric_state, string_state

if TYPE_CHECKING:
    from .runtime import EvStatsRuntime

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1

# The voltage reading parks at 0 between sessions and would otherwise pin the
# session minimum at zero for ever; anything under this is not a charging
# voltage. The upper sentinel is the reset value, meaning "no sample yet".
VOLTS_FLOOR = 50.0
VOLTS_SENTINEL = 600.0

# A closing verdict only overrides the live route if there is enough energy for
# the disagreement to mean anything.
OVERRIDE_MIN_KWH = Decimal("0.05")

FLAG_LATE_OPEN = "late_open"
FLAG_LATE_CLOSE = "late_close"


@dataclass
class SessionState:
    """What is known about the session currently running.

    Persisted, so a restart mid-charge does not lose the opening readings and
    silently produce a session whose energy is measured from the wrong zero.
    """

    active: bool = False
    started: str | None = None
    start_energy: Decimal = Decimal(0)
    start_house_energy: Decimal = Decimal(0)
    # The provisional cost and solar meters at the moment this opened. The
    # session's own figures are the difference; see `meters.py` for why they
    # are priced before the place is known.
    start_cost: Decimal = Decimal(0)
    start_solar: Decimal = Decimal(0)
    start_soc: float | None = None
    peak_kw: float = 0.0
    peak_amps: float = 0.0
    min_volts: float = VOLTS_SENTINEL
    supply: str | None = None
    routed_to: str = BUCKET_UNKNOWN
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "started": self.started,
            "start_energy": str(self.start_energy),
            "start_house_energy": str(self.start_house_energy),
            "start_cost": str(self.start_cost),
            "start_solar": str(self.start_solar),
            "start_soc": self.start_soc,
            "peak_kw": self.peak_kw,
            "peak_amps": self.peak_amps,
            "min_volts": self.min_volts,
            "supply": self.supply,
            "routed_to": self.routed_to,
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        return cls(
            active=bool(data.get("active", False)),
            started=data.get("started"),
            start_energy=Decimal(str(data.get("start_energy", "0"))),
            start_house_energy=Decimal(str(data.get("start_house_energy", "0"))),
            start_cost=Decimal(str(data.get("start_cost", "0"))),
            start_solar=Decimal(str(data.get("start_solar", "0"))),
            start_soc=data.get("start_soc"),
            peak_kw=float(data.get("peak_kw", 0.0)),
            peak_amps=float(data.get("peak_amps", 0.0)),
            min_volts=float(data.get("min_volts", VOLTS_SENTINEL)),
            supply=data.get("supply"),
            routed_to=data.get("routed_to", BUCKET_UNKNOWN),
            flags=list(data.get("flags", [])),
        )


class SessionManager:
    """Opens, watches and closes charging sessions."""

    def __init__(self, hass: HomeAssistant, entry_id: str, runtime: EvStatsRuntime) -> None:
        self.hass = hass
        self.runtime = runtime
        self.config = runtime.config
        self.state = SessionState()
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.session")
        self._lock = asyncio.Lock()
        self._power = runtime.charging_power_entity
        self._connection: str | None = self.config.opt(SECTION_CAR, CONF_CHARGER_CONNECTION)
        self._open_timer = None
        self._close_timer = None
        self._close_deadline: float | None = None
        self._unsubs: list = []
        self._listeners: list = []

    # ------------------------------------------------------------ lifecycle --
    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self.state = SessionState.from_dict(data)

    async def async_start(self) -> None:
        self._unsubs.append(
            async_track_state_change_event(self.hass, [self._power], self._handle_power)
        )
        if self._connection:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, [self._connection], self._handle_connection
                )
            )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_watchdog,
                timedelta(seconds=WATCHDOG_INTERVAL_S),
            )
        )

    @callback
    def async_stop(self) -> None:
        self._cancel_open()
        self._cancel_close()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    def add_listener(self, cb) -> Any:
        self._listeners.append(cb)

        def _remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return _remove

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # --------------------------------------------------------- session view --
    @property
    def session_kwh(self) -> Decimal:
        """Zero when nothing is running.

        It used to keep reporting the last session's total for days afterwards -
        a live-looking number for something that was not happening.
        """
        if not self.state.active:
            return Decimal(0)
        return max(
            Decimal(0), self.runtime.charge_energy.total - self.state.start_energy
        )

    @property
    def duration_h(self) -> float:
        if not self.state.active or not self.state.started:
            return 0.0
        started = dt_util.parse_datetime(self.state.started)
        if started is None:
            return 0.0
        return max(0.0, (dt_util.utcnow() - started).total_seconds() / 3600)

    @property
    def house_ratio(self) -> Decimal | None:
        """How much of the car's energy our own house meter accounted for.

        None whenever it cannot honestly answer: no house meter configured, too
        little energy for the arithmetic to survive its own error bars, too
        short a session, or a baseline that has not filled enough of its window
        to be a baseline.
        """
        if not self.state.active:
            return None
        house_energy = self.runtime.house_energy
        baseline = self.runtime.baseline
        if house_energy is None or baseline is None or not baseline.usable:
            return None

        car = self.session_kwh
        hours = self.duration_h
        if car <= Decimal(str(self._thr(CONF_MIN_KWH, DEFAULT_MIN_KWH))):
            return None
        if hours <= self._thr(CONF_MIN_HOURS, DEFAULT_MIN_HOURS):
            return None

        median = baseline.median
        if median is None:
            return None

        house = house_energy.total - self.state.start_house_energy
        if house < 0:
            # The house meter reads lower than it did when the session opened,
            # which means the integrator restarted underneath it - a reload or
            # a restart part-way through a charge. The opening reading no
            # longer refers to the same series, so the only honest answer is
            # none. Carrying on would hand the classifier a large negative
            # ratio and it would read as firm proof the car charged elsewhere.
            return None
        expected = Decimal(str(median)) * Decimal(str(hours))
        return ((house - expected) / car).quantize(Decimal("0.001"))

    @property
    def evidence(self) -> Evidence:
        return self.evidence_with(self.house_ratio)

    def evidence_with(self, ratio: Decimal | None) -> Evidence:
        """Assemble the evidence around an already-computed ratio.

        Split out because the ratio walks a day of samples and the closing path
        needs both it and the verdict; going through the property twice would
        compute it twice, against a window that has moved in between.
        """
        return Evidence(
            active=self.state.active,
            supply=self.state.supply,
            peak_kw=self.state.peak_kw,
            gps_zone=self.runtime.location.zone(),
            gps_fresh=self.runtime.location.fresh,
            house_ratio=ratio,
            work_buckets=self.runtime.work_buckets,
        )

    @property
    def thresholds(self) -> Thresholds:
        return Thresholds(
            home_ratio_yes=self._thr(CONF_HOME_RATIO_YES, DEFAULT_HOME_RATIO_YES),
            home_ratio_no=self._thr(CONF_HOME_RATIO_NO, DEFAULT_HOME_RATIO_NO),
            dc_min_kw=self._thr(CONF_DC_MIN_KW, DEFAULT_DC_MIN_KW),
        )

    @property
    def classification(self) -> Classification:
        return classify(self.evidence, self.thresholds)

    def _thr(self, key: str, default: float) -> float:
        return float(self.config.opt(SECTION_THRESHOLDS, key, default))

    # -------------------------------------------------------------- trigger --
    @callback
    def _handle_power(self, _event: Event[EventStateChangedData]) -> None:
        power = numeric_state(self.hass, self._power)
        if power is None:
            # A dropout is not a stop. The car goes unavailable several times a
            # day, and treating that as zero power would close every session
            # that happened to span one and open a second when it came back.
            return

        if self.state.active:
            self._accumulate(power)

        if power > CHARGE_ON_KW:
            self._cancel_close()
            if not self.state.active:
                self._arm_open()
        else:
            self._cancel_open()
            if self.state.active:
                self._arm_close(END_DEBOUNCE_S)

    @callback
    def _handle_connection(self, event: Event[EventStateChangedData]) -> None:
        """The plug coming out ends a session sooner than power falling does."""
        new = event.data.get("new_state")
        if new is None or not self.state.active:
            return
        if str(new.state).strip().lower() in ("disconnected", "unplugged", "off"):
            self._arm_close(DISCONNECT_DEBOUNCE_S)

    @callback
    def _accumulate(self, power: float) -> None:
        """Gather evidence mid-session.

        Mid-session on purpose. Current and voltage both read zero between
        charges, so a value sampled at the end is worthless - it describes the
        socket after it was unplugged.
        """
        changed = False
        if power > self.state.peak_kw:
            self.state.peak_kw = round(power, 3)
            changed = True

        amps = numeric_state(self.hass, self.config.opt(SECTION_CAR, CONF_CHARGE_CURRENT))
        if amps is not None and amps > self.state.peak_amps:
            self.state.peak_amps = round(amps, 1)
            changed = True

        volts = numeric_state(self.hass, self.config.opt(SECTION_CAR, CONF_CHARGE_VOLTAGE))
        if volts is not None and volts > VOLTS_FLOOR and volts < self.state.min_volts:
            self.state.min_volts = round(volts, 1)
            changed = True

        if self._sample_supply():
            changed = True

        if changed:
            self._notify()

    def _sample_supply(self) -> bool:
        """Latch AC or DC, once DC always DC.

        One DC sample anywhere in a session is enough, and a later AC sample
        must not un-set it: a charger that reports AC while handshaking and DC
        once delivering would otherwise finish the session looking like AC.

        Read from a configured entity if there is one, otherwise from a
        `supply` attribute on the charging-power sensor, which is how the Geely
        integration reports it. With neither, this stays None and the DC rule
        simply never fires - deliberately, because inferring DC from high power
        would file a 22 kW three-phase AC charger as a public DC one, with high
        confidence and the right to sweep.
        """
        value = string_state(self.hass, self.config.opt(SECTION_CAR, CONF_SUPPLY_SOURCE))
        if value is None:
            state = self.hass.states.get(self._power)
            attr = state.attributes.get("supply") if state else None
            value = attr if isinstance(attr, str) else None
        if value is None:
            return False

        value = value.strip().upper()
        if value not in (SUPPLY_AC, SUPPLY_DC):
            return False
        if self.state.supply == SUPPLY_DC:
            return False
        if value != self.state.supply:
            self.state.supply = value
            return True
        return False

    # --------------------------------------------------------------- timers --
    @callback
    def _arm_open(self) -> None:
        if self._open_timer is not None:
            return
        self._open_timer = async_call_later(
            self.hass, START_DEBOUNCE_S, self._fire_open
        )

    @callback
    def _arm_close(self, delay: float) -> None:
        """Schedule the close, keeping whichever deadline is sooner.

        Two things close a session and they wait different lengths of time. If
        power fell to zero first, a 300-second close is already pending when
        the plug comes out - and the plug coming out is unambiguous, so it must
        be allowed to overtake rather than being ignored as a duplicate.
        """
        deadline = dt_util.utcnow().timestamp() + delay
        if self._close_timer is not None:
            if self._close_deadline is not None and deadline >= self._close_deadline:
                return
            self._cancel_close()
        self._close_deadline = deadline
        self._close_timer = async_call_later(self.hass, delay, self._fire_close)

    @callback
    def _cancel_open(self) -> None:
        if self._open_timer is not None:
            self._open_timer()
            self._open_timer = None

    @callback
    def _cancel_close(self) -> None:
        if self._close_timer is not None:
            self._close_timer()
            self._close_timer = None
        self._close_deadline = None

    @callback
    def _fire_open(self, _now) -> None:
        self._open_timer = None
        self.hass.async_create_task(self.async_open())

    @callback
    def _fire_close(self, _now) -> None:
        self._close_timer = None
        self._close_deadline = None
        self.hass.async_create_task(self.async_close())

    # ----------------------------------------------------------- open/close --
    async def async_open(self, flags: list[str] | None = None) -> None:
        """Start a session and decide where its energy goes."""
        async with self._lock:
            if self.state.active:
                return

            power = numeric_state(self.hass, self._power) or 0.0
            house_total = (
                self.runtime.house_energy.total
                if self.runtime.house_energy
                else Decimal(0)
            )
            self.state = SessionState(
                active=True,
                started=dt_util.utcnow().isoformat(timespec="seconds"),
                start_energy=self.runtime.charge_energy.total,
                start_house_energy=house_total,
                start_cost=self.runtime.meters.cost_total or Decimal(0),
                start_solar=self.runtime.meters.solar_total or Decimal(0),
                start_soc=numeric_state(self.hass, self.config.required(CONF_BATTERY)),
                peak_kw=round(power, 3),
                # Flags are passed in rather than written afterwards. Setting
                # them after opening meant the open itself cleared them, and the
                # flag is the only marker separating a watchdog-rescued session
                # - truncated energy window, non-comparable ratio - from a clean
                # one, so losing it silently poisoned the tuning data.
                flags=list(flags or []),
            )
            self._sample_supply()

            route = self._live_route()
            self.state.routed_to = route
            await self._async_persist()

        if route != BUCKET_UNKNOWN:
            await self.runtime.ledger.async_set_route(route)
            _LOGGER.info("Routing this session to %s live - the fix is fresh", route)

        self._notify()

    def _live_route(self) -> str:
        """Where to file energy from the first minute, if anywhere.

        Only on a fresh fix, and only to a place that has a bucket. Everything
        else stays `unknown`, which remains the default rather than a fallback.
        """
        if not self.runtime.location.fresh:
            return BUCKET_UNKNOWN
        zone = self.runtime.location.zone()
        return zone if zone in self.runtime.ledger.buckets else BUCKET_UNKNOWN

    async def async_close(self, flags: list[str] | None = None) -> None:
        """End the session, decide where it happened, and settle the ledger."""
        async with self._lock:
            if not self.state.active:
                return
            if flags:
                self.state.flags.extend(f for f in flags if f not in self.state.flags)

            # Computed once, here, while the session is still open. Every one
            # of these is meaningless the moment the state is reset, and the
            # ratio is expensive enough that asking twice would also be waste.
            ratio = self.house_ratio
            verdict = classify(self.evidence_with(ratio), self.thresholds)
            kwh = self.session_kwh
            routed = self.state.routed_to
            soc_now = numeric_state(self.hass, self.config.required(CONF_BATTERY))
            soc_delta = (
                round(soc_now - self.state.start_soc, 1)
                if soc_now is not None and self.state.start_soc is not None
                else None
            )
            started = self.state.started
            state_snapshot = self.state
            prov_cost = self._meter_delta(
                self.runtime.meters.cost_total, self.state.start_cost
            )
            prov_solar = self._meter_delta(
                self.runtime.meters.solar_total, self.state.start_solar
            )

        # Stop routing FIRST, so anything arriving from here on lands in
        # `unknown` rather than in a bucket the verdict may be about to reject.
        await self.runtime.ledger.async_set_route(BUCKET_UNKNOWN)

        if verdict.bucket != routed and kwh > OVERRIDE_MIN_KWH:
            # The same guarded path a manual correction takes, so a closing
            # verdict cannot drive a bucket negative either.
            await self.runtime.ledger.async_move(routed, verdict.bucket, kwh)
            _LOGGER.info(
                "Closing verdict %s overrode the live route %s - moved %s kWh",
                verdict.bucket,
                routed,
                kwh,
            )

        swept = await self._async_sweep(verdict)

        # The provisional figures become real ones only for a home session.
        # This is the one place cost enters the ledger from a live meter; a
        # later correction restates it through the same arithmetic. Anywhere
        # else, the charge did not bill through our meter and costs us nothing,
        # which is not the same as being worth nothing - see the free-charging
        # value sensor, which prices it at what home charging actually costs.
        if verdict.bucket == BUCKET_HOME and (prov_cost or prov_solar):
            await self.runtime.ledger.async_adjust(
                BUCKET_HOME, prov_cost or ZERO, prov_solar or ZERO
            )

        if verdict.bucket == BUCKET_UNKNOWN:
            # Protect what was deliberately left unattributed, so a later
            # confident session cannot absorb it.
            await self.runtime.ledger.async_set_floor(
                self.runtime.ledger.kwh(BUCKET_UNKNOWN)
            )

        self.hass.bus.async_fire(
            EVENT_SESSION_RECORDED,
            self._event_payload(
                verdict,
                kwh,
                swept,
                ratio,
                soc_delta,
                started,
                state_snapshot,
                prov_cost,
                prov_solar,
            ),
        )

        async with self._lock:
            self.state = SessionState()
            await self._async_persist()
        self._notify()

    async def _async_sweep(self, verdict: Classification) -> Decimal:
        """Claim the pre-session tail, if this verdict has earned the right."""
        if verdict.bucket == BUCKET_UNKNOWN or verdict.confidence != CONFIDENCE_HIGH:
            return Decimal(0)
        sweep = self.runtime.ledger.sweepable(Decimal(str(SWEEP_CAP_KWH)))
        if sweep <= Decimal(str(SWEEP_MIN_KWH)):
            return Decimal(0)
        await self.runtime.ledger.async_move(BUCKET_UNKNOWN, verdict.bucket, sweep)
        _LOGGER.info("Swept %s kWh of pre-session energy into %s", sweep, verdict.bucket)
        return sweep

    def _event_payload(
        self,
        verdict: Classification,
        kwh: Decimal,
        swept: Decimal,
        ratio: Decimal | None,
        soc_delta: float | None,
        started: str | None,
        snapshot: SessionState,
        prov_cost: Decimal | None,
        prov_solar: Decimal | None,
    ) -> dict[str, Any]:
        location = self.runtime.location
        at_home = verdict.bucket == BUCKET_HOME
        return {
            "id": dt_util.utcnow().strftime("%Y%m%d%H%M%S"),
            "start": started,
            "end": dt_util.utcnow().isoformat(timespec="seconds"),
            "kwh": float(round(kwh, 3)),
            "bucket": verdict.bucket,
            "verdict": verdict.verdict,
            "confidence": verdict.confidence,
            "reason": verdict.reason,
            # `is not None`, not truthiness. A ratio of exactly zero is a real
            # and strong finding - the house supplied none of it - and testing
            # it for truth would log that as "no opinion".
            "home_ratio": None if ratio is None else float(ratio),
            "gps_zone": location.zone(),
            "gps_fresh": location.fresh,
            "gps_stale_km": location.staleness_km,
            "peak_kw": snapshot.peak_kw,
            # Corroboration only, never a classifier input: a 10 A socket at
            # work fingerprints identically to a 10 A socket at home.
            "peak_amps": snapshot.peak_amps,
            "min_volts": (
                snapshot.min_volts
                if VOLTS_FLOOR < snapshot.min_volts < VOLTS_SENTINEL
                else None
            ),
            "supply": snapshot.supply,
            "soc_delta": soc_delta,
            "swept": float(round(swept, 3)),
            # What this session actually contributed to its bucket. Zero
            # anywhere but home, because nowhere else bills through our meter.
            "cost": float(round(prov_cost, 4)) if at_home and prov_cost else 0,
            "solar_kwh": float(round(prov_solar, 3)) if at_home and prov_solar else 0,
            # Recorded unconditionally, unlike the pair above: what this would
            # have cost at home, and how much of it our panels would have
            # covered. Meaningful for any bucket, and without them a later
            # correction to home would have nothing to reach for.
            "prov_cost": None if prov_cost is None else float(round(prov_cost, 4)),
            "prov_solar": None if prov_solar is None else float(round(prov_solar, 3)),
            "flags": list(snapshot.flags),
        }

    @staticmethod
    def _meter_delta(total: Decimal | None, start: Decimal) -> Decimal | None:
        """A meter's movement over this session, or None if it cannot say.

        None when the meter is not configured at all, and None when it now
        reads lower than it did at the open - which means it restarted
        underneath the session, so the opening reading refers to a different
        series. Reporting the difference anyway would log a negative cost.
        """
        if total is None:
            return None
        delta = total - start
        return delta if delta >= ZERO else None

    # ------------------------------------------------------------- watchdog --
    async def _async_watchdog(self, _now) -> None:
        """Catch the three ways the lifecycle can be left wrong."""
        power = numeric_state(self.hass, self._power)

        # Zero power and an unreadable car both leave a session open for ever,
        # and both need closing. They are not the same thing, though: charging
        # normally continues through a dropout, so the live path treats an
        # unreadable car as no information at all. Half an hour of it is
        # different - the session cannot be measured any more, and leaving it
        # open would let the next real charge accrue into it.
        if self.state.active and (power is None or power <= CHARGE_ON_KW):
            state = self.hass.states.get(self._power)
            if state is not None:
                idle_for = (dt_util.utcnow() - state.last_changed).total_seconds()
                if idle_for > STUCK_SESSION_S:
                    _LOGGER.warning(
                        "Session still open after %.0f minutes %s; closing it",
                        idle_for / 60,
                        "with the car unreadable" if power is None else "at zero",
                    )
                    await self.async_close(flags=[FLAG_LATE_CLOSE])
                    return

        if not self.state.active:
            # The route must never point at a bucket with no session open. If a
            # close ever fails part-way, this is what stops every later kWh
            # being filed as the last session's location.
            if self.runtime.ledger.route != BUCKET_UNKNOWN:
                _LOGGER.warning(
                    "Route was left on %s with no session open; resetting it",
                    self.runtime.ledger.route,
                )
                await self.runtime.ledger.async_set_route(BUCKET_UNKNOWN)

            if power is not None and power > CHARGE_ON_KW:
                _LOGGER.warning("Charging with no session open; opening one late")
                await self.async_open(flags=[FLAG_LATE_OPEN])

    async def _async_persist(self) -> None:
        await self._store.async_save(self.state.as_dict())

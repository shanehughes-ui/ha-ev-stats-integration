"""Constants for EV Stats.

Everything site-specific in the original YAML package lives here as a config key.
The package that became this integration carried roughly 165 hard-coded entity
ids; the point of this file is that there are now none.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ev_stats"

# --- config-flow sections ---------------------------------------------------
# Sections NEST their submitted data: user_input is {"car": {...}, ...}, not a
# flat dict. Changing this shape later needs a config-entry version bump and an
# async_migrate_entry, so it is fixed here and read through helpers.py.
SECTION_CAR: Final = "car"
SECTION_LOCATION: Final = "location"
SECTION_HOUSE: Final = "house"
SECTION_PRICING: Final = "pricing"
SECTION_EXTRAS: Final = "extras"
SECTION_THRESHOLDS: Final = "thresholds"

# --- the four the integration cannot work without ---------------------------
CONF_CHARGING_POWER: Final = "charging_power"
CONF_ODOMETER: Final = "odometer"
CONF_BATTERY: Final = "battery"
CONF_HOME_ZONE: Final = "home_zone"

# --- car, all optional ------------------------------------------------------
CONF_CHARGE_CURRENT: Final = "charge_current"
CONF_CHARGE_VOLTAGE: Final = "charge_voltage"
CONF_DEVICE_TRACKER: Final = "device_tracker"
CONF_ENGINE_STATE: Final = "engine_state"
CONF_CHARGER_PLUG: Final = "charger_plug"
CONF_CHARGER_CONNECTION: Final = "charger_connection"
# An entity reading AC or DC. Without one, a `supply` attribute on the charging
# power sensor is used instead - which is how the Geely integration reports it.
# With neither, the DC rule never fires, and that is deliberate: inferring DC
# from high power would file a 22 kW three-phase AC charger as public DC.
CONF_SUPPLY_SOURCE: Final = "supply_source"
CONF_EFFICIENCY: Final = "efficiency"
CONF_TRIP_CONSUMPTION: Final = "trip_consumption"
CONF_DAYS_TO_SERVICE: Final = "days_to_service"
CONF_DISTANCE_TO_SERVICE: Final = "distance_to_service"
CONF_12V_VOLTAGE: Final = "voltage_12v"
CONF_TYRE_PRESSURE: Final = "tyre_pressure"        # list, 4 entities
CONF_TYRE_TEMPERATURE: Final = "tyre_temperature"  # list, 4 entities
# Locks, doors, bonnet, boot, windows - any mix, any number. A single list
# rather than one key per opening: cars disagree about which they expose, and
# the summary only has to say whether anything is open and name it.
CONF_SECURITY_ENTITIES: Final = "security_entities"

# --- location ---------------------------------------------------------------
# Work zones are a LIST. The original package hard-coded a single `workplace`
# slug into the tariff list, the classifier, the display map and a notification
# action string; making it a list is most of what "fully parameterised" means.
CONF_WORK_ZONES: Final = "work_zones"
# Whether a completed trip records where it started and ended, to the metre.
# Off by default: this integration ships dashboards meant to be shared, and a
# home address is the most sensitive thing it could hold.
CONF_RECORD_POSITIONS: Final = "record_positions"
CONF_HOME_RADIUS_M: Final = "home_radius_m"
CONF_WORK_RADIUS_M: Final = "work_radius_m"

# --- house ------------------------------------------------------------------
CONF_HOUSE_LOAD: Final = "house_load"
CONF_SOLAR_POWER: Final = "solar_power"
CONF_SOLAR_CURTAILMENT: Final = "solar_curtailment"

# --- pricing ----------------------------------------------------------------
CONF_IMPORT_PRICE: Final = "import_price"
CONF_EXPORT_PRICE: Final = "export_price"
CONF_FUEL_PRICE: Final = "fuel_price"
CONF_CO2_INTENSITY: Final = "co2_intensity"

# --- extras -----------------------------------------------------------------
CONF_AMBIENT_TEMP: Final = "ambient_temp"
CONF_NOTIFY_TARGET: Final = "notify_target"

# --- thresholds, defaulting to what three weeks of real data measured --------
CONF_HOME_RATIO_YES: Final = "home_ratio_yes"
CONF_HOME_RATIO_NO: Final = "home_ratio_no"
CONF_MIN_KWH: Final = "min_kwh"
CONF_MIN_HOURS: Final = "min_hours"
CONF_DC_MIN_KW: Final = "dc_min_kw"
CONF_CHARGE_EFFICIENCY: Final = "charge_efficiency"
CONF_PETROL_L_PER_100KM: Final = "petrol_l_per_100km"
CONF_INSTALL_ODOMETER: Final = "install_odometer"

DEFAULT_HOME_RATIO_YES: Final = 0.60
DEFAULT_HOME_RATIO_NO: Final = 0.25
DEFAULT_MIN_KWH: Final = 2.0
DEFAULT_MIN_HOURS: Final = 0.75
DEFAULT_DC_MIN_KW: Final = 15.0
DEFAULT_CHARGE_EFFICIENCY: Final = 0.88
DEFAULT_PETROL_L_PER_100KM: Final = 7.5
DEFAULT_HOME_RADIUS_M: Final = 60
DEFAULT_WORK_RADIUS_M: Final = 120

# Physical constants, not tunables.
PETROL_KG_CO2_PER_L: Final = 2.31
KELVIN_0C: Final = 273.15
TYRE_REFERENCE_K: Final = 293.15  # 20 degC, an arbitrary but stated reference

# --- buckets ----------------------------------------------------------------
# `unknown` is the default and must stay first: every kWh lands here and is
# moved exactly once, so that if everything else fails the energy is still
# counted and visibly unattributed rather than silently misfiled.
BUCKET_UNKNOWN: Final = "unknown"
BUCKET_HOME: Final = "home"
BUCKET_PUBLIC_DC: Final = "public_dc"
BUCKET_OTHER: Final = "other"
BASE_BUCKETS: Final = (BUCKET_UNKNOWN, BUCKET_HOME, BUCKET_PUBLIC_DC, BUCKET_OTHER)

SERVICE_MOVE_ENERGY: Final = "move_energy"
SERVICE_CORRECT_SESSION: Final = "correct_session"
SERVICE_LOG_DC_SESSION: Final = "log_dc_session"
SERVICE_IMPORT_LEGACY: Final = "import_legacy"
SERVICE_DASHBOARD: Final = "dashboard"

EVENT_SESSION_RECORDED: Final = f"{DOMAIN}_session_recorded"
EVENT_SESSION_CORRECTED: Final = f"{DOMAIN}_session_corrected"
EVENT_TRIP_RECORDED: Final = f"{DOMAIN}_trip_recorded"
EVENT_PACK_ESTIMATE: Final = f"{DOMAIN}_pack_estimate"

# --- session lifecycle ------------------------------------------------------
# Not config keys. Each is a property of how the car behaves rather than a
# preference, and exposing them as options would invite tuning the wrong knob.

# Above this the car is charging. Not zero: the reading idles at a few watts of
# noise, and a threshold of zero opens a session every time the car is polled.
CHARGE_ON_KW: Final = 0.05

# A minute of sustained draw before a session is real. Preconditioning the
# cabin briefly draws from the wall, and it is not a charge.
START_DEBOUNCE_S: Final = 60
# Five to close, because the charger tapers to near zero at the top of a charge
# and comes back. Closing at the first zero would split one session into four.
END_DEBOUNCE_S: Final = 300
# The plug coming out is unambiguous, so it needs far less patience than power
# falling to zero does.
DISCONNECT_DEBOUNCE_S: Final = 120

WATCHDOG_INTERVAL_S: Final = 600
# Power at zero for half an hour with a session still open means the closing
# trigger was missed - usually the car dropped off the network mid-session.
STUCK_SESSION_S: Final = 1800

# The most pre-session energy a confident verdict may claim. Sized to a plug-in
# tail (~0.1 kWh), not to a session: without a cap, one high-confidence verdict
# could swallow every unattributed kWh the system had ever accumulated.
SWEEP_CAP_KWH: Final = 3.0
# Below this a move is not worth making - it is rounding, and each one writes
# to the store and redraws every bucket sensor.
SWEEP_MIN_KWH: Final = 0.005

# --- the house-load baseline ------------------------------------------------
BASELINE_MAX_AGE_S: Final = 24 * 3600
BASELINE_MAX_SAMPLES: Final = 2000
# A baseline still filling its window is not a baseline. Under half a window
# the house-supply proof declines to answer and the classifier falls back to
# location, which is honest; answering from thirty minutes of samples would be
# worse than admitting ignorance.
BASELINE_MIN_COVERAGE: Final = 0.5

# --- GPS --------------------------------------------------------------------
# About two metres. Below this the tracker is jittering in place rather than
# reporting a new position, and re-latching would reset the freshness test that
# is the entire reason the fix can be trusted.
GPS_MIN_DELTA_DEG: Final = 0.00002
# A latitude or longitude smaller than this is the null island sentinel a
# tracker emits when it has no fix, not a position off the coast of Ghana.
GPS_MIN_ABS_DEG: Final = 1.0

# --- trips -------------------------------------------------------------------
# The odometer is polled and lags the engine going off, so closing a trip
# immediately records a real drive as 0.0 km. In the readings this was built
# from, 13 of 57 engine cycles would have been written that way.
TRIP_END_DEBOUNCE_S: Final = 120
# The other half of the same guard: under half a kilometre is odometer lag, not
# a journey. The ceiling catches a glitched reading that would otherwise land in
# the log as a three-thousand-kilometre commute.
TRIP_MIN_KM: Final = 0.5
TRIP_MAX_KM: Final = 500.0

# --- how much of each log a sensor publishes as attributes -------------------
# The Store holds the whole history; these are the slices a dashboard can read.
# An attribute blob is rewritten to the recorder on every state change, so this
# is the number that has to stay small - not the log itself.
SESSIONS_IN_ATTRIBUTES: Final = 20
TRIPS_IN_ATTRIBUTES: Final = 40
ESTIMATES_IN_ATTRIBUTES: Final = 60

# --- tyres -------------------------------------------------------------------
# Thirty days, because the drift being looked for is a slow leak and a shorter
# window would track the leak rather than reveal it.
TYRE_BASELINE_MAX_AGE_S: Final = 30 * 24 * 3600
TYRE_BASELINE_MAX_SAMPLES: Final = 3000
# Lower than the house baseline needs: a tyre baseline is compared against
# itself over weeks, so a third of a window is already a useful reference.
TYRE_BASELINE_MIN_COVERAGE: Final = 0.3

# --- petrol comparison -------------------------------------------------------
# The same delta guard the trip log uses. Anything outside it is a glitched
# odometer reading, not a drive.
PETROL_MAX_KM_STEP: Final = 500.0

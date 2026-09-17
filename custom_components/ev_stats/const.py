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
CONF_EFFICIENCY: Final = "efficiency"
CONF_TRIP_CONSUMPTION: Final = "trip_consumption"
CONF_DAYS_TO_SERVICE: Final = "days_to_service"
CONF_DISTANCE_TO_SERVICE: Final = "distance_to_service"
CONF_12V_VOLTAGE: Final = "voltage_12v"
CONF_TYRE_PRESSURE: Final = "tyre_pressure"        # list, 4 entities
CONF_TYRE_TEMPERATURE: Final = "tyre_temperature"  # list, 4 entities

# --- location ---------------------------------------------------------------
# Work zones are a LIST. The original package hard-coded a single `allison_work`
# slug into the tariff list, the classifier, the display map and a notification
# action string; making it a list is most of what "fully parameterised" means.
CONF_WORK_ZONES: Final = "work_zones"
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
SERVICE_LOG_DC_SESSION: Final = "log_dc_session"
SERVICE_IMPORT_LEGACY: Final = "import_legacy"

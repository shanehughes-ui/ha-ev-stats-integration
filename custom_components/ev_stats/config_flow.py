"""Config and options flow for EV Stats."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_12V_VOLTAGE,
    CONF_AMBIENT_TEMP,
    CONF_BATTERY,
    CONF_CHARGE_CURRENT,
    CONF_CHARGE_EFFICIENCY,
    CONF_CHARGE_VOLTAGE,
    CONF_CHARGER_CONNECTION,
    CONF_CHARGER_PLUG,
    CONF_CHARGING_POWER,
    CONF_CO2_INTENSITY,
    CONF_DAYS_TO_SERVICE,
    CONF_DC_MIN_KW,
    CONF_DEVICE_TRACKER,
    CONF_DISTANCE_TO_SERVICE,
    CONF_EFFICIENCY,
    CONF_ENGINE_STATE,
    CONF_EXPORT_PRICE,
    CONF_FUEL_PRICE,
    CONF_HOME_RADIUS_M,
    CONF_HOME_RATIO_NO,
    CONF_HOME_RATIO_YES,
    CONF_HOME_ZONE,
    CONF_HOUSE_LOAD,
    CONF_IMPORT_PRICE,
    CONF_INSTALL_ODOMETER,
    CONF_MIN_HOURS,
    CONF_MIN_KWH,
    CONF_NOTIFY_TARGET,
    CONF_ODOMETER,
    CONF_PETROL_L_PER_100KM,
    CONF_SOLAR_CURTAILMENT,
    CONF_SOLAR_POWER,
    CONF_SUPPLY_SOURCE,
    CONF_TRIP_CONSUMPTION,
    CONF_TYRE_PRESSURE,
    CONF_TYRE_TEMPERATURE,
    CONF_WORK_RADIUS_M,
    CONF_WORK_ZONES,
    DEFAULT_CHARGE_EFFICIENCY,
    DEFAULT_DC_MIN_KW,
    DEFAULT_HOME_RADIUS_M,
    DEFAULT_HOME_RATIO_NO,
    DEFAULT_HOME_RATIO_YES,
    DEFAULT_MIN_HOURS,
    DEFAULT_MIN_KWH,
    DEFAULT_PETROL_L_PER_100KM,
    DEFAULT_WORK_RADIUS_M,
    DOMAIN,
    SECTION_CAR,
    SECTION_EXTRAS,
    SECTION_HOUSE,
    SECTION_LOCATION,
    SECTION_PRICING,
    SECTION_THRESHOLDS,
)


def _sensor(device_class: str | None = None, multiple: bool = False):
    """An entity picker for a sensor, optionally filtered by device class."""
    cfg: dict[str, Any] = {"domain": "sensor", "multiple": multiple}
    if device_class:
        cfg["device_class"] = device_class
    return selector.EntitySelector(selector.EntitySelectorConfig(**cfg))


def _entity(domain: str | list[str], multiple: bool = False):
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=domain, multiple=multiple)
    )


def _number(minimum: float, maximum: float, step: float, unit: str | None = None):
    cfg: dict[str, Any] = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit:
        cfg["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**cfg))


# The four the integration genuinely cannot work without. Everything else is
# optional and its absence removes entities rather than publishing a zero.
REQUIRED_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CHARGING_POWER): _sensor("power"),
        vol.Required(CONF_ODOMETER): _sensor("distance"),
        vol.Required(CONF_BATTERY): _sensor("battery"),
        vol.Required(CONF_HOME_ZONE): _entity("zone"),
    }
)


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    """Build the options form.

    Sections rather than one long form, and everything but the car collapsed by
    default: a first-time user should see that the optional groups exist without
    being asked twenty questions.
    """

    def sec(key: str) -> dict[str, Any]:
        return current.get(key) or {}

    car, loc = sec(SECTION_CAR), sec(SECTION_LOCATION)
    house, price = sec(SECTION_HOUSE), sec(SECTION_PRICING)
    extras, thr = sec(SECTION_EXTRAS), sec(SECTION_THRESHOLDS)

    def opt(store: dict[str, Any], key: str, default: Any = None):
        """vol.Optional carrying the stored value, or nothing at all.

        An EntitySelector with default=None renders as an error, so a key that
        has never been set is simply omitted and the field comes up empty.
        """
        if store.get(key) is not None:
            return vol.Optional(key, default=store[key])
        if default is not None:
            return vol.Optional(key, default=default)
        return vol.Optional(key)

    return vol.Schema(
        {
            vol.Required(SECTION_CAR): section(
                vol.Schema(
                    {
                        opt(car, CONF_CHARGE_CURRENT): _sensor("current"),
                        opt(car, CONF_CHARGE_VOLTAGE): _sensor("voltage"),
                        opt(car, CONF_DEVICE_TRACKER): _entity("device_tracker"),
                        opt(car, CONF_ENGINE_STATE): _sensor(),
                        opt(car, CONF_CHARGER_PLUG): _entity("binary_sensor"),
                        opt(car, CONF_CHARGER_CONNECTION): _sensor(),
                        opt(car, CONF_SUPPLY_SOURCE): _entity(
                            ["sensor", "input_select", "select"]
                        ),
                        opt(car, CONF_EFFICIENCY): _sensor(),
                        opt(car, CONF_TRIP_CONSUMPTION): _sensor(),
                        opt(car, CONF_DAYS_TO_SERVICE): _sensor(),
                        opt(car, CONF_DISTANCE_TO_SERVICE): _sensor(),
                        opt(car, CONF_12V_VOLTAGE): _sensor("voltage"),
                        opt(car, CONF_TYRE_PRESSURE): _sensor("pressure", multiple=True),
                        opt(car, CONF_TYRE_TEMPERATURE): _sensor(
                            "temperature", multiple=True
                        ),
                    }
                ),
                {"collapsed": False},
            ),
            vol.Required(SECTION_LOCATION): section(
                vol.Schema(
                    {
                        opt(loc, CONF_WORK_ZONES): _entity("zone", multiple=True),
                        opt(loc, CONF_HOME_RADIUS_M, DEFAULT_HOME_RADIUS_M): _number(
                            10, 1000, 5, "m"
                        ),
                        opt(loc, CONF_WORK_RADIUS_M, DEFAULT_WORK_RADIUS_M): _number(
                            10, 1000, 5, "m"
                        ),
                    }
                ),
                {"collapsed": True},
            ),
            vol.Required(SECTION_HOUSE): section(
                vol.Schema(
                    {
                        opt(house, CONF_HOUSE_LOAD): _sensor("power"),
                        opt(house, CONF_SOLAR_POWER): _sensor("power"),
                        opt(house, CONF_SOLAR_CURTAILMENT): _sensor(),
                    }
                ),
                {"collapsed": True},
            ),
            vol.Required(SECTION_PRICING): section(
                vol.Schema(
                    {
                        opt(price, CONF_IMPORT_PRICE): _sensor(),
                        opt(price, CONF_EXPORT_PRICE): _sensor(),
                        opt(price, CONF_FUEL_PRICE): _sensor(),
                        opt(price, CONF_CO2_INTENSITY): _sensor(),
                    }
                ),
                {"collapsed": True},
            ),
            vol.Required(SECTION_EXTRAS): section(
                vol.Schema(
                    {
                        opt(extras, CONF_AMBIENT_TEMP): _sensor("temperature"),
                        opt(extras, CONF_NOTIFY_TARGET): selector.TextSelector(),
                    }
                ),
                {"collapsed": True},
            ),
            vol.Required(SECTION_THRESHOLDS): section(
                vol.Schema(
                    {
                        vol.Required(
                            CONF_HOME_RATIO_YES,
                            default=thr.get(CONF_HOME_RATIO_YES, DEFAULT_HOME_RATIO_YES),
                        ): _number(0.1, 2.0, 0.01),
                        vol.Required(
                            CONF_HOME_RATIO_NO,
                            default=thr.get(CONF_HOME_RATIO_NO, DEFAULT_HOME_RATIO_NO),
                        ): _number(0.0, 1.0, 0.01),
                        vol.Required(
                            CONF_MIN_KWH, default=thr.get(CONF_MIN_KWH, DEFAULT_MIN_KWH)
                        ): _number(0.1, 20, 0.1, "kWh"),
                        vol.Required(
                            CONF_MIN_HOURS,
                            default=thr.get(CONF_MIN_HOURS, DEFAULT_MIN_HOURS),
                        ): _number(0.1, 12, 0.05, "h"),
                        vol.Required(
                            CONF_DC_MIN_KW,
                            default=thr.get(CONF_DC_MIN_KW, DEFAULT_DC_MIN_KW),
                        ): _number(5, 400, 0.5, "kW"),
                        vol.Required(
                            CONF_CHARGE_EFFICIENCY,
                            default=thr.get(
                                CONF_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY
                            ),
                        ): _number(0.5, 1.0, 0.01),
                        vol.Required(
                            CONF_PETROL_L_PER_100KM,
                            default=thr.get(
                                CONF_PETROL_L_PER_100KM, DEFAULT_PETROL_L_PER_100KM
                            ),
                        ): _number(3, 25, 0.1, "L/100km"),
                        opt(thr, CONF_INSTALL_ODOMETER): _number(0, 1_000_000, 1, "km"),
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


class EvStatsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Collect only what is needed to set up. The rest lives in options."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # One entry per car, keyed on the power sensor - the one entity
            # every other calculation here ultimately hangs off.
            await self.async_set_unique_id(user_input[CONF_CHARGING_POWER])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="EV Stats", data=user_input)

        return self.async_show_form(step_id="user", data_schema=REQUIRED_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> EvStatsOptionsFlow:
        return EvStatsOptionsFlow()


class EvStatsOptionsFlow(OptionsFlowWithReload):
    """Options reload the entry on save, so a changed entity takes effect."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Options shadow data, so first-run defaults survive until overridden.
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(current)
        )

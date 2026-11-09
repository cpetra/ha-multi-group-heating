"""Config flow for Multi-Group Boiler Heating."""

from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AUTO_CONTROL_BOILER,
    CONF_BOILER_ENTITY,
    CONF_BOILER_FIRING_TEMP,
    CONF_GROUP_1_NAME,
    CONF_GROUP_1_TRVS,
    CONF_GROUP_2_NAME,
    CONF_GROUP_2_TRVS,
    CONF_GROUP_3_NAME,
    CONF_GROUP_3_TRVS,
    CONF_GROUP_4_NAME,
    CONF_GROUP_4_TRVS,
    CONF_GROUP_5_NAME,
    CONF_GROUP_5_TRVS,
    CONF_HYSTERESIS,
    CONF_ZIGBEE_RETRY_DELAY,
    DEFAULT_AUTO_CONTROL_BOILER,
    DEFAULT_BOILER_FIRING_TEMP,
    DEFAULT_GROUPS,
    DEFAULT_HYSTERESIS,
    DEFAULT_ZIGBEE_RETRY_DELAY,
    DOMAIN,
)


def _build_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the config/options schema with 5 heating groups and boiler parameters."""
    schema_dict: dict[Any, Any] = {
        vol.Required(
            CONF_BOILER_ENTITY,
            default=defaults.get(CONF_BOILER_ENTITY),
        ): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="climate")
        ),
        vol.Required(
            CONF_HYSTERESIS,
            default=defaults.get(CONF_HYSTERESIS, DEFAULT_HYSTERESIS),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.1,
                max=2.0,
                step=0.1,
                unit_of_measurement="°C",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Required(
            CONF_BOILER_FIRING_TEMP,
            default=defaults.get(
                CONF_BOILER_FIRING_TEMP, DEFAULT_BOILER_FIRING_TEMP
            ),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=18.0,
                max=30.0,
                step=0.5,
                unit_of_measurement="°C",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Required(
            CONF_ZIGBEE_RETRY_DELAY,
            default=defaults.get(
                CONF_ZIGBEE_RETRY_DELAY, DEFAULT_ZIGBEE_RETRY_DELAY
            ),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=60,
                step=1,
                unit_of_measurement="s",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Required(
            CONF_AUTO_CONTROL_BOILER,
            default=defaults.get(
                CONF_AUTO_CONTROL_BOILER, DEFAULT_AUTO_CONTROL_BOILER
            ),
        ): selector.BooleanSelector(),
    }

    group_keys = [
        (CONF_GROUP_1_NAME, CONF_GROUP_1_TRVS),
        (CONF_GROUP_2_NAME, CONF_GROUP_2_TRVS),
        (CONF_GROUP_3_NAME, CONF_GROUP_3_TRVS),
        (CONF_GROUP_4_NAME, CONF_GROUP_4_TRVS),
        (CONF_GROUP_5_NAME, CONF_GROUP_5_TRVS),
    ]

    for idx, (name_key, trvs_key) in enumerate(group_keys, start=1):
        default_name = DEFAULT_GROUPS[idx - 1]["default_name"]
        schema_dict[
            vol.Required(
                name_key, default=defaults.get(name_key, default_name)
            )
        ] = selector.TextSelector()
        schema_dict[
            vol.Optional(trvs_key, default=defaults.get(trvs_key, []))
        ] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="climate", multiple=True)
        )

    return vol.Schema(schema_dict)


class MultiGroupHeatingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Multi-Group Boiler Heating."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_BOILER_ENTITY])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Multi-Group Heating ({user_input[CONF_BOILER_ENTITY]})",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return MultiGroupHeatingOptionsFlow(config_entry)


class MultiGroupHeatingOptionsFlow(OptionsFlow):
    """Handle options flow for Multi-Group Boiler Heating."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        merged = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(merged),
        )

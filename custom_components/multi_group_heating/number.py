"""Virtual Group Target and Preset Number entities for Multi-Group Boiler Heating."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, HEATING_MODES
from .coordinator import MultiGroupHeatingController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Virtual Group Target and Mode Preset numbers for all 5 groups."""
    controller: MultiGroupHeatingController = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    for idx in sorted(controller.groups):
        entities.append(GroupTargetNumber(controller, idx))
        for mode_key in HEATING_MODES:
            entities.append(GroupPresetNumber(controller, idx, mode_key))
    async_add_entities(entities, True)


class _BaseHeatingNumber(NumberEntity, RestoreEntity):
    """Shared base class for temperature setpoints."""

    _attr_has_entity_name = True
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 5.0
    _attr_native_max_value = 30.0
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False


class GroupTargetNumber(_BaseHeatingNumber):
    """Virtual Group Target temperature that propagates changes to group TRVs."""

    _attr_icon = "mdi:thermostat"

    def __init__(
        self, controller: MultiGroupHeatingController, group_idx: int
    ) -> None:
        """Initialize the virtual group target entity."""
        self._controller = controller
        self._group_idx = group_idx
        grp_name = controller.groups[group_idx]["name"]
        self._attr_name = f"TS Target {grp_name}"
        self._attr_unique_id = (
            f"{controller.entry.entry_id}_group_{group_idx}_target"
        )
        self._attr_device_info = controller.device_info

    @property
    def native_value(self) -> float:
        """Return the current virtual group target."""
        return float(self._controller.groups[self._group_idx]["target"])

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose group metadata."""
        grp = self._controller.groups[self._group_idx]
        return {
            "group_index": self._group_idx,
            "group_name": grp["name"],
            "trv_entities": grp["trvs"],
        }

    async def async_added_to_hass(self) -> None:
        """Restore virtual group target on startup without overriding TRVs."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                await self._controller.async_set_group_target(
                    self._group_idx,
                    float(last_state.state),
                    push_to_trvs=False,
                )
            except ValueError:
                pass
        self.async_on_remove(
            self._controller.add_group_target_listener(
                self._group_idx, self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Write updated target state."""
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Set virtual group target and broadcast to all TRVs in this group."""
        await self._controller.async_set_group_target(
            self._group_idx, value, push_to_trvs=True
        )


class GroupPresetNumber(_BaseHeatingNumber):
    """Preset temperature for a specific (Group, Mode) combination."""

    _attr_icon = "mdi:thermometer-lines"

    def __init__(
        self,
        controller: MultiGroupHeatingController,
        group_idx: int,
        mode_key: str,
    ) -> None:
        """Initialize the group mode preset entity."""
        self._controller = controller
        self._group_idx = group_idx
        self._mode_key = mode_key
        grp_name = controller.groups[group_idx]["name"]
        self._attr_name = f"{mode_key} Temp {grp_name}"
        self._attr_unique_id = f"{controller.entry.entry_id}_group_{group_idx}_preset_{mode_key.lower()}"
        self._attr_device_info = controller.device_info

    @property
    def native_value(self) -> float:
        """Return the preset value for this group and mode."""
        return float(
            self._controller.groups[self._group_idx]["presets"][self._mode_key]
        )

    async def async_added_to_hass(self) -> None:
        """Restore preset value on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                self._controller.groups[self._group_idx]["presets"][
                    self._mode_key
                ] = float(last_state.state)
            except ValueError:
                pass

    async def async_set_native_value(self, value: float) -> None:
        """Update preset temperature and live-sync if this mode is currently active."""
        await self._controller.async_set_group_preset(
            self._group_idx, self._mode_key, value
        )
        self.async_write_ha_state()


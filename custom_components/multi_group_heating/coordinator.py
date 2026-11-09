"""Central controller for Multi-Group Boiler Heating."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time
import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import (
    async_call_later,
    async_track_time_change,
)

from .const import (
    CONF_AUTO_CONTROL_BOILER,
    CONF_BOILER_ENTITY,
    CONF_BOILER_FIRING_TEMP,
    CONF_HYSTERESIS,
    CONF_ZIGBEE_RETRY_DELAY,
    DEFAULT_AUTO_CONTROL_BOILER,
    DEFAULT_BOILER_FIRING_TEMP,
    DEFAULT_GROUPS,
    DEFAULT_HYSTERESIS,
    DEFAULT_ZIGBEE_RETRY_DELAY,
    DOMAIN,
    MODE_AWAY,
    MODE_DAY,
    SCHEDULE_SLOTS,
    STATE_KEEP,
    STATE_START,
    STATE_STOP,
)

_LOGGER = logging.getLogger(__name__)


class MultiGroupHeatingController:
    """Coordinates schedule modes, 5 TRV groups, Zigbee retry, and boiler hysteresis."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the controller."""
        self.hass = hass
        self.entry = entry
        cfg = {**entry.data, **entry.options}

        self.boiler_entity: str = cfg[CONF_BOILER_ENTITY]
        self.hysteresis: float = float(
            cfg.get(CONF_HYSTERESIS, DEFAULT_HYSTERESIS)
        )
        self.boiler_firing_temp: float = float(
            cfg.get(CONF_BOILER_FIRING_TEMP, DEFAULT_BOILER_FIRING_TEMP)
        )
        self.zigbee_retry_delay: int = int(
            cfg.get(CONF_ZIGBEE_RETRY_DELAY, DEFAULT_ZIGBEE_RETRY_DELAY)
        )
        self.auto_control_boiler: bool = bool(
            cfg.get(CONF_AUTO_CONTROL_BOILER, DEFAULT_AUTO_CONTROL_BOILER)
        )

        self.mode: str = MODE_DAY
        self.schedule_times: dict[str, time] = {
            slot_id: time(hour=h, minute=m)
            for slot_id, _label, (h, m), _target_mode in SCHEDULE_SLOTS
        }

        self.groups: dict[int, dict[str, Any]] = {}
        for grp in DEFAULT_GROUPS:
            idx = grp["index"]
            name = cfg.get(f"group_{idx}_name") or grp["default_name"]
            trvs = list(cfg.get(f"group_{idx}_trvs", []))
            self.groups[idx] = {
                "index": idx,
                "name": name,
                "trvs": trvs,
                "target": float(grp["presets"][MODE_DAY]),
                "presets": dict(grp["presets"]),
            }

        self._mode_listeners: list[Callable[[], None]] = []
        self._group_target_listeners: dict[int, list[Callable[[], None]]] = {
            i: [] for i in range(1, 6)
        }
        self._demand_listeners: list[Callable[[], None]] = []
        self._schedule_unsubs: dict[str, CALLBACK_TYPE] = {}
        self._retry_unsubs: dict[int, CALLBACK_TYPE] = {}

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry info for the controller."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=f"Multi-Group Heating ({self.boiler_entity})",
            manufacturer="Costa",
            model="3-Tier Multi-Group TRV & Boiler Hysteresis Controller",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def all_trvs(self) -> list[str]:
        """Return flat list of all configured TRV climate entities across all 5 groups."""
        entities: list[str] = []
        for grp in self.groups.values():
            for trv in grp["trvs"]:
                if trv not in entities:
                    entities.append(trv)
        return entities

    def register_schedule_timers(self) -> None:
        """Subscribe to daily schedule time triggers."""
        self.clear_schedule_timers()
        for slot_id, _label, _default_hm, target_mode in SCHEDULE_SLOTS:
            t_val = self.schedule_times[slot_id]

            @callback
            def _handle_time(
                _now: datetime,
                _slot: str = slot_id,
                _mode: str = target_mode,
            ) -> None:
                if self.mode == MODE_AWAY:
                    _LOGGER.debug(
                        "Skipping scheduled transition %s -> %s because Away mode is active",
                        _slot,
                        _mode,
                    )
                    return
                self.hass.async_create_task(self.async_set_mode(_mode))

            self._schedule_unsubs[slot_id] = async_track_time_change(
                self.hass,
                _handle_time,
                hour=t_val.hour,
                minute=t_val.minute,
                second=0,
            )

    def clear_schedule_timers(self) -> None:
        """Cancel all schedule listeners and pending Zigbee retries."""
        for unsub in self._schedule_unsubs.values():
            unsub()
        self._schedule_unsubs.clear()
        for unsub in self._retry_unsubs.values():
            unsub()
        self._retry_unsubs.clear()

    def update_schedule_time(self, slot_id: str, new_time: time) -> None:
        """Update a schedule slot time and re-bind timers."""
        self.schedule_times[slot_id] = new_time
        self.register_schedule_timers()

    def add_mode_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        """Subscribe to mode changes."""
        self._mode_listeners.append(cb)
        return lambda: self._mode_listeners.remove(cb)

    def add_group_target_listener(
        self, group_idx: int, cb: Callable[[], None]
    ) -> CALLBACK_TYPE:
        """Subscribe to virtual group target changes."""
        self._group_target_listeners[group_idx].append(cb)
        return lambda: self._group_target_listeners[group_idx].remove(cb)

    def add_demand_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        """Subscribe to boiler demand recalculations."""
        self._demand_listeners.append(cb)
        return lambda: self._demand_listeners.remove(cb)

    async def async_set_mode(
        self, new_mode: str, apply_presets: bool = True
    ) -> None:
        """Transition heating mode and push preset targets to all groups."""
        self.mode = new_mode
        for cb in list(self._mode_listeners):
            cb()

        if apply_presets:
            for idx, grp in self.groups.items():
                preset_val = grp["presets"].get(new_mode)
                if preset_val is not None:
                    await self.async_set_group_target(
                        idx, float(preset_val), push_to_trvs=True
                    )

    async def async_set_group_preset(
        self, group_idx: int, mode_key: str, value: float
    ) -> None:
        """Update a group's preset temperature; if that mode is active, update its group target."""
        self.groups[group_idx]["presets"][mode_key] = value
        if self.mode == mode_key:
            await self.async_set_group_target(
                group_idx, value, push_to_trvs=True
            )

    async def async_set_group_target(
        self, group_idx: int, value: float, push_to_trvs: bool = True
    ) -> None:
        """Set a group's virtual target temperature and propagate to its TRVs."""
        grp = self.groups[group_idx]
        prev = grp["target"]
        grp["target"] = value
        for cb in list(self._group_target_listeners[group_idx]):
            cb()

        if not push_to_trvs or not grp["trvs"] or prev == value:
            return

        await self._async_push_target_to_group_trvs(group_idx, value)

    async def _async_push_target_to_group_trvs(
        self, group_idx: int, target_temp: float
    ) -> None:
        """Send climate.set_temperature to all TRVs in a group with optional sleepy-Zigbee retry."""
        trvs: list[str] = self.groups[group_idx]["trvs"]
        if not trvs:
            return

        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {
                ATTR_ENTITY_ID: trvs,
                ATTR_TEMPERATURE: target_temp,
            },
            blocking=False,
        )

        if group_idx in self._retry_unsubs:
            self._retry_unsubs.pop(group_idx)()

        if self.zigbee_retry_delay > 0:

            @callback
            def _verify_and_retry(_now: datetime) -> None:
                self._retry_unsubs.pop(group_idx, None)
                missed = False
                for entity_id in trvs:
                    st = self.hass.states.get(entity_id)
                    if st is not None and st.state == HVACMode.HEAT:
                        cur_sp = st.attributes.get(ATTR_TEMPERATURE)
                        try:
                            if float(cur_sp) != target_temp:
                                missed = True
                                break
                        except (TypeError, ValueError):
                            pass
                if missed:
                    _LOGGER.debug(
                        "Retrying group %s setpoint %s °C for sleepy Zigbee TRVs: %s",
                        group_idx,
                        target_temp,
                        trvs,
                    )
                    self.hass.async_create_task(
                        self.hass.services.async_call(
                            CLIMATE_DOMAIN,
                            SERVICE_SET_TEMPERATURE,
                            {
                                ATTR_ENTITY_ID: trvs,
                                ATTR_TEMPERATURE: target_temp,
                            },
                            blocking=False,
                        )
                    )

            self._retry_unsubs[group_idx] = async_call_later(
                self.hass,
                self.zigbee_retry_delay,
                _verify_and_retry,
            )

    def evaluate_boiler_demand(self) -> tuple[str, dict[str, Any]]:
        """Evaluate start / keep / stop boiler demand across all configured TRVs."""
        active: list[str] = []
        need_heat: list[str] = []
        heating_valve: list[str] = []
        satisfied: list[str] = []

        for entity_id in self.all_trvs:
            state = self.hass.states.get(entity_id)
            if state is None or state.state != HVACMode.HEAT:
                continue
            cur = state.attributes.get(ATTR_CURRENT_TEMPERATURE)
            target = state.attributes.get(ATTR_TEMPERATURE)
            hvac_action = state.attributes.get("hvac_action")
            if cur is None or target is None:
                continue
            try:
                cur_f = float(cur)
                target_f = float(target)
            except (ValueError, TypeError):
                continue

            active.append(entity_id)
            if cur_f <= round(target_f - self.hysteresis, 2):
                need_heat.append(entity_id)
            elif hvac_action == "heating" and cur_f < target_f:
                heating_valve.append(entity_id)
            else:
                satisfied.append(entity_id)

        if need_heat:
            action = STATE_START
        elif heating_valve:
            action = STATE_KEEP
        else:
            action = STATE_STOP

        attrs = {
            "boiler_entity": self.boiler_entity,
            "hysteresis_deadband": self.hysteresis,
            "boiler_firing_temp": self.boiler_firing_temp,
            "auto_control_boiler": self.auto_control_boiler,
            "active_count": len(active),
            "need_heat_count": len(need_heat),
            "heating_valve_count": len(heating_valve),
            "satisfied_count": len(satisfied),
            "active_trvs": active,
            "need_heat_trvs": need_heat,
            "heating_valve_trvs": heating_valve,
            "satisfied_trvs": satisfied,
        }
        return action, attrs

    async def async_apply_boiler_action(self, action: str) -> None:
        """Drive the central boiler thermostat based on demand action."""
        if not self.auto_control_boiler:
            return

        if action == STATE_START:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {
                    ATTR_ENTITY_ID: self.boiler_entity,
                    ATTR_HVAC_MODE: HVACMode.HEAT,
                    ATTR_TEMPERATURE: self.boiler_firing_temp,
                },
                blocking=False,
            )
        elif action == STATE_STOP:
            boiler_state = self.hass.states.get(self.boiler_entity)
            if boiler_state is None or boiler_state.state != HVACMode.OFF:
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN,
                    SERVICE_SET_HVAC_MODE,
                    {
                        ATTR_ENTITY_ID: self.boiler_entity,
                        ATTR_HVAC_MODE: HVACMode.OFF,
                    },
                    blocking=False,
                )


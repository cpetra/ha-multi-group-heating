"""Constants for the Multi-Group Boiler Heating integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "multi_group_heating"
PLATFORMS: list[Platform] = [
    Platform.SELECT,
    Platform.TIME,
    Platform.NUMBER,
    Platform.SENSOR,
]

CONF_BOILER_ENTITY = "boiler_entity"
CONF_BOILER_FIRING_TEMP = "boiler_firing_temp"
CONF_ZIGBEE_RETRY_DELAY = "zigbee_retry_delay"
CONF_AUTO_CONTROL_BOILER = "auto_control_boiler"

CONF_GROUP_1_NAME = "group_1_name"
CONF_GROUP_1_TRVS = "group_1_trvs"
CONF_GROUP_2_NAME = "group_2_name"
CONF_GROUP_2_TRVS = "group_2_trvs"
CONF_GROUP_3_NAME = "group_3_name"
CONF_GROUP_3_TRVS = "group_3_trvs"
CONF_GROUP_4_NAME = "group_4_name"
CONF_GROUP_4_TRVS = "group_4_trvs"
CONF_GROUP_5_NAME = "group_5_name"
CONF_GROUP_5_TRVS = "group_5_trvs"

DEFAULT_BOILER_FIRING_TEMP = 25.0
DEFAULT_ZIGBEE_RETRY_DELAY = 10
DEFAULT_AUTO_CONTROL_BOILER = True

MODE_AWAY = "Away"
MODE_COMFORT = "Comfort"
MODE_DAY = "Day"
MODE_NIGHT = "Night"
HEATING_MODES = [MODE_AWAY, MODE_COMFORT, MODE_DAY, MODE_NIGHT]

STATE_START = "start"
STATE_STOP = "stop"

SCHEDULE_SLOTS = [
    ("morning_comfort", "Morning Comfort Start", (6, 30), MODE_COMFORT),
    ("day_start", "Day Mode Start", (8, 0), MODE_DAY),
    ("evening_comfort", "Evening Comfort Start", (20, 0), MODE_COMFORT),
    ("night_start", "Night Mode Start", (22, 0), MODE_NIGHT),
]

DEFAULT_GROUPS = [
    {
        "index": 1,
        "default_name": "Living + Kitchen",
        "presets": {
            MODE_COMFORT: 22.0,
            MODE_DAY: 19.0,
            MODE_NIGHT: 18.0,
            MODE_AWAY: 16.0,
        },
    },
    {
        "index": 2,
        "default_name": "Dormitories",
        "presets": {
            MODE_COMFORT: 19.0,
            MODE_DAY: 20.0,
            MODE_NIGHT: 19.0,
            MODE_AWAY: 16.0,
        },
    },
    {
        "index": 3,
        "default_name": "Office",
        "presets": {
            MODE_COMFORT: 19.0,
            MODE_DAY: 21.0,
            MODE_NIGHT: 18.0,
            MODE_AWAY: 16.0,
        },
    },
    {
        "index": 4,
        "default_name": "Bathrooms",
        "presets": {
            MODE_COMFORT: 22.0,
            MODE_DAY: 20.0,
            MODE_NIGHT: 19.0,
            MODE_AWAY: 16.0,
        },
    },
    {
        "index": 5,
        "default_name": "Garage",
        "presets": {
            MODE_COMFORT: 15.0,
            MODE_DAY: 15.0,
            MODE_NIGHT: 14.0,
            MODE_AWAY: 10.0,
        },
    },
]

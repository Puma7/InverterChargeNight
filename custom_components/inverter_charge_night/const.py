"""Constants for the Inverter Charge Night integration."""

DOMAIN = "inverter_charge_night"

# Operation modes
MODE_NIGHT_CHARGE = "night_charge"
MODE_MORNING_DISCHARGE = "morning_discharge"
DEFAULT_OPERATION_MODE = MODE_NIGHT_CHARGE

# Default values
DEFAULT_MIN_SOC = 8.0
DEFAULT_MAX_SOC = 100.0
DEFAULT_START_TIME = "00:00"
DEFAULT_END_TIME = "05:59"
DEFAULT_FORECAST_ERROR_MARGIN = 10.0  # 10% buffer
DEFAULT_UPDATE_INTERVAL = 900  # 15 minutes in seconds
DEFAULT_COMMAND_DELAY = 0.1  # 0.1 seconds
DEFAULT_SAFE_FALLBACK_SOC = 50.0  # Safe fallback SOC when forecast unavailable (prevents charging to 100%)
DEFAULT_ACTIVE_START_DATE = ""  # Optional YYYY-MM-DD
DEFAULT_ACTIVE_END_DATE = ""  # Optional YYYY-MM-DD
DEFAULT_MIN_CHARGE_POWER_W = 1000  # Default min charge power (W)
DEFAULT_MAX_CHARGE_POWER_W = 10000  # Default max charge power (W)
DEFAULT_ABSOLUTE_MAX_CHARGE_POWER_W = 10000  # Default absolute max charge power (W)

# Configuration keys
CONF_OPERATION_MODE = "operation_mode"
CONF_KOSTAL_MIN_SOC_ENTITY = "kostal_min_soc_entity"
CONF_KOSTAL_GRID_CHARGE_SWITCH = "kostal_grid_charge_switch"
CONF_PV_FORECAST_ENTITY = "pv_forecast_entity"
CONF_BATTERY_SOC_ENTITY = "battery_soc_entity"
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_START_TIME = "start_time"
CONF_END_TIME = "end_time"
CONF_USER_MIN_SOC = "user_min_soc"
CONF_USER_MAX_SOC = "user_max_soc"
CONF_FORECAST_ERROR_MARGIN = "forecast_error_margin"
CONF_DEFAULT_MIN_SOC = "default_min_soc"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_COMMAND_DELAY = "command_delay"
CONF_ACTIVE_START_DATE = "active_start_date"
CONF_ACTIVE_END_DATE = "active_end_date"
CONF_BACKUP_MODE_ENTITY = "backup_mode_entity"
CONF_MIN_CHARGE_POWER_W = "min_charge_power_w"
CONF_MAX_CHARGE_POWER_W = "max_charge_power_w"
CONF_CHARGE_POWER_ENTITY = "charge_power_entity"
CONF_CHARGE_POWER_SENT_ENTITY = "charge_power_sent_entity"
CONF_CHARGE_POWER_RECEIVED_ENTITY = "charge_power_received_entity"
CONF_AUTO_EFFICIENT_CHARGE = "auto_efficient_charge"
CONF_AUTO_EFFICIENCY_DATA = "auto_efficiency_data"
CONF_ABSOLUTE_MAX_CHARGE_POWER_W = "absolute_max_charge_power_w"
CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY = "absolute_max_charge_power_entity"
CONF_PV_FORECAST_TODAY_ENTITY = "pv_forecast_today_entity"
CONF_FORCE_DISCHARGE_SWITCH = "force_discharge_switch"

# Attributes
ATTR_CALCULATED_SOC = "calculated_soc"
ATTR_ORIGINAL_MIN_SOC = "original_min_soc"
ATTR_IS_ACTIVE = "is_active"
ATTR_TARGET_SOC = "target_soc"
ATTR_CURRENT_SOC = "current_soc"
ATTR_OPERATION_MODE = "operation_mode"
ATTR_SKIP_NEXT = "skip_next"


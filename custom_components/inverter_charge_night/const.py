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
CONF_RUNTIME_STATE = "runtime_state"  # entry.options key: flags that survive a restart
CONF_ABSOLUTE_MAX_CHARGE_POWER_W = "absolute_max_charge_power_w"
CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY = "absolute_max_charge_power_entity"
CONF_PV_FORECAST_TODAY_ENTITY = "pv_forecast_today_entity"
CONF_FORCE_DISCHARGE_SWITCH = "force_discharge_switch"

# Planner v2 (plan 006). The target formula is selected by planner_mode:
# "headroom" is the original formula (room for tomorrow's forecast only),
# "bridge" also covers the house load from window end until the PV output
# exceeds it. The remaining keys feed the bridge planner and the discharge block.
PLANNER_MODE_HEADROOM = "headroom"
PLANNER_MODE_BRIDGE = "bridge"
CONF_PLANNER_MODE = "planner_mode"  # "headroom" (today) | "bridge" (new)
CONF_HOUSE_LOAD_ENTITY = "house_load_entity"  # cumulative kWh meter of the house load (optional)
CONF_AVG_HOUSE_LOAD_KW = "avg_house_load_kw"  # fallback without a meter: average load in kW
CONF_PV_CROSSOVER_DELAY_MIN = "pv_crossover_delay_min"  # minutes after sunrise until PV > load
CONF_BRIDGE_RESERVE_KWH = "bridge_reserve_kwh"  # safety reserve added to the bridge energy
CONF_CHARGE_EFFICIENCY = "charge_efficiency"  # 0.80-1.0, default 0.90
CONF_DISCHARGE_LIMIT_ENTITY = "discharge_limit_entity"  # number: discharge power limit (W), optional
CONF_FEED_IN_PRICE_CT = "feed_in_price_ct"  # optional
CONF_NIGHT_PRICE_CT = "night_price_ct"  # optional
CONF_DAY_PRICE_CT = "day_price_ct"  # optional
DEFAULT_PLANNER_MODE = PLANNER_MODE_HEADROOM
DEFAULT_AVG_HOUSE_LOAD_KW = 0.5
DEFAULT_PV_CROSSOVER_DELAY_MIN = 90
DEFAULT_BRIDGE_RESERVE_KWH = 0.5
DEFAULT_CHARGE_EFFICIENCY = 0.90
HOUSE_LOAD_PROFILE_DAYS = 14  # history used to learn the hourly load profile
HOUSE_LOAD_PROFILE_CACHE_S = 900  # 15 minutes
PLANNED_POWER_WRITE_THRESHOLD_W = 100  # write the charge setpoint only when it moves more than this

# Attributes
ATTR_SNOW_NIGHTS = "snow_nights"  # nights left in snow mode (charge to user max)


# House connection limit (plan 008). Two wallboxes and the battery can run at
# the same time for the whole six-hour window; that continuous load is what
# heats meter terminals and fuse contacts. The battery is the only load this
# integration controls, so it is the one that gives way. Safety feature, not an
# optimisation: it applies in both planner modes.
CONF_GRID_IMPORT_ENTITY = "grid_import_entity"  # current grid import in W (or kW)
CONF_MAIN_FUSE_A = "main_fuse_a"  # main fuse per phase, A
CONF_GRID_PHASES = "grid_phases"  # 1 or 3
CONF_GRID_VOLTAGE_V = "grid_voltage_v"  # phase voltage, default 230
CONF_GRID_CONTINUOUS_PCT = "grid_continuous_pct"  # continuous share of the rating, default 80
CONF_GRID_MAX_CONTINUOUS_W = "grid_max_continuous_w"  # alternative: the budget directly, in W
CONF_GRID_HEADROOM_W = "grid_headroom_w"  # safety margin below the budget, default 500
DEFAULT_GRID_PHASES = 3
DEFAULT_GRID_VOLTAGE_V = 230
DEFAULT_GRID_CONTINUOUS_PCT = 80
DEFAULT_GRID_HEADROOM_W = 500
GRID_LIMIT_STALE_AFTER_S = 300  # after this the grid import counts as unknown
GRID_LIMIT_MIN_WRITE_INTERVAL_S = 30  # debounce for the grid import listener
# Discharge block in the window (plan 009). Cheap grid energy at night is worth
# less than stored PV is during the day, so the battery must not run the house
# while the window is open. Three ways are tried in this order: a vendor switch,
# the discharge power limit, and finally the inverter's min SOC - a battery does
# not discharge below its min SOC, and that entity is the one this integration
# controls anyway, so the last way works on every inverter.
CONF_DISCHARGE_BLOCK_SWITCH = "discharge_block_switch"  # switch: "block battery discharge", optional
CONF_DISCHARGE_BLOCK_MODE = "discharge_block_mode"  # "auto" | "off"
DISCHARGE_BLOCK_OFF = "off"  # let the battery discharge, as before plan 006
DISCHARGE_BLOCK_AUTO = "auto"  # pick the best way the configuration offers
DISCHARGE_BLOCK_VIA_SWITCH = "switch"
DISCHARGE_BLOCK_VIA_LIMIT = "limit"
DISCHARGE_BLOCK_VIA_MIN_SOC = "min_soc"
DEFAULT_DISCHARGE_BLOCK_MODE = DISCHARGE_BLOCK_AUTO
ATTR_INVERTER_FLOOR_SOC = "inverter_floor_soc"  # what is written to the min SOC entity
ATTR_DISCHARGE_BLOCK = "discharge_block"  # switch | limit | min_soc | off

# Efficiency finder, measurement rules (plan 010). What the finder looks for is
# the charge power at which the least energy is lost between the grid and the
# battery. That only works if every sample really was measured at the power it
# is filed under, so a sample has to survive all of these:
CONF_CHARGE_ENERGY_SENT_ENTITY = "charge_energy_sent_entity"  # kWh meter, optional but exact
CONF_CHARGE_ENERGY_RECEIVED_ENTITY = "charge_energy_received_entity"  # kWh meter, optional
AUTO_TEST_SETTLE_S = 120  # the ramp to the new setpoint is not part of the measurement
AUTO_TEST_MIN_DURATION_S = 300  # measured time after settling
AUTO_TEST_MIN_ENERGY_WH = 300.0  # ... and this much energy, so 20 kW is measurable too
AUTO_TEST_MIN_FOLLOW_RATIO = 0.8  # the inverter has to draw what it was told to draw
AUTO_TEST_MAX_PLAUSIBLE_LOSS = 0.5  # above this the sensors are wrong, not the inverter
AUTO_TEST_MAX_ATTEMPTS = 2  # after this many failures a power counts as unmeasurable
AUTO_TEST_STATE_IDLE = "idle"
AUTO_TEST_STATE_MEASURING = "measuring"
AUTO_TEST_STATE_SETTLING = "settling"
AUTO_TEST_STATE_FINISHED = "finished"
AUTO_TEST_STATE_WAITING = "waiting"

# Backup / island operation. While the house runs on the battery instead of the
# grid, this integration keeps its hands off the inverter completely: there is
# nothing to charge from, and a raised min SOC would stop the battery from
# supplying the house - in a power cut, of all moments.
#
# The state is read from whatever entity the installation offers. A switch or
# binary sensor says on/off; a plain state sensor can say anything, so the
# states that mean backup can be declared in the configuration. Kostal reports
# "ESB" (Ersatzstrombetrieb) in its inverter state, other manufacturers use
# their own words - hence both a list of recognised terms and a free-form
# setting for the ones nobody has seen yet.
CONF_BACKUP_MODE_STATES = "backup_mode_states"  # comma separated, optional
BACKUP_ACTIVE_STATES: frozenset[str] = frozenset(
    {
        "on",
        "true",
        "1",
        "yes",
        "active",
        "backup",
        "backup_mode",
        "backupmode",
        "island",
        "island_mode",
        "islanding",
        "esb",  # Kostal: Ersatzstrombetrieb
        "ersatzstrom",
        "ersatzstrombetrieb",
        "insel",
        "inselbetrieb",
        "inselnetz",
        "notstrom",
        "notstrombetrieb",
        "offgrid",
        "off_grid",
        "off-grid",
        "standalone",
        "emergency",
        "emergency_power",
        "gridswitchoff",  # the inverter has disconnected from the grid
        "extswitchoff",
    }
)
BACKUP_INACTIVE_STATES: frozenset[str] = frozenset(
    {
        "off",
        "false",
        "0",
        "no",
        "normal",
        "grid",
        "grid_mode",
        "grid_connected",
        "gridconnected",
        "connected",
        "online",
        "netz",
        "netzbetrieb",
        "netzparallel",
        # States a Kostal inverter reports in normal grid operation
        "feedin",
        "feed_in",
        "throttled",
        "standby",
        "init",
        "isomeas",
        "gridcheck",
        "gridprecheck",
        "gridsync",
        "startup",
        "batterycharging",
        "batterydischarging",
    }
)

# Settings and stored data of earlier versions. Installations that have been
# through several releases carry keys nobody reads any more; one real entry
# held 500 dead measurement records, about a hundred kilobytes that Home
# Assistant loads and rewrites on every change.
LEGACY_HOUSE_LOAD_ENERGY_ENTITY = "home_consumption_energy_entity"  # -> house_load_entity
LEGACY_UNUSED_DATA_KEYS: tuple[str, ...] = (
    "battery_charge_energy_entity",  # kWh counter of grid energy into the battery
    "grid_import_energy_entity",  # kWh counter of the grid import
)
LEGACY_UNUSED_OPTION_KEYS: tuple[str, ...] = ("charge_session_data",)
# What the efficiency search itself stores; everything else in that option is
# from a version that no longer exists and is dropped on setup.
AUTO_EFFICIENCY_KEYS: frozenset[str] = frozenset(
    {"history", "best_power_w", "best_loss", "range_min_w", "range_max_w", "failed", "bounds_w"}
)

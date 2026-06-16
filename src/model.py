"""
Residential energy-system model (real-world calibrated) — linopy implementation.

Cost-optimal sizing + hourly dispatch of a residential building with rooftop PV,
battery storage, an air-source heat pump and a gas boiler, under a configurable
CO2 price, over one weather year (8760 h).

Real-world features beyond the baseline coursework model:
  * Investment costs annualised from real CAPEX, lifetime and discount rate (CRF).
  * Temperature-dependent heat-pump COP (Staffell et al. 2012 ASHP regression),
    driven by the outdoor-temperature series.
  * Gas-boiler efficiency < 1 and a gas commodity price (carbon priced separately).
  * PV export to the grid at a feed-in tariff (set below PV LCOE) + roof-area and
    grid-connection limits, so PV is valued for self-consumption and stays bounded.
  * Battery wear cost per kWh of throughput.
  * Fixed annual grid standing charge.

Formulation is a Linear Program. Solve it with the open-source HiGHS solver.

Author: Muhammad Ahmad Khan
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
import xarray as xr
import linopy


@dataclass(frozen=True)
class Config:
    # --- Energy prices (EUR / kWh) -------------------------------------------
    grid_price: float = 0.30          # retail electricity import price
    feed_in_tariff: float = 0.07      # PV export revenue (set just below PV LCOE
                                      #   so PV is built for self-consumption)
    gas_price: float = 0.10           # gas commodity + network, excl. carbon
    # --- Emission factors (kg CO2 / kWh) -------------------------------------
    co2_grid: float = 0.401
    co2_gas: float = 0.202            # per kWh of gas *fuel input*
    # --- Heat pump: temperature-dependent COP --------------------------------
    hp_sink_temp_c: float = 45.0      # supply-water temperature (45 = mixed; 35 = underfloor)
    cop_min: float = 1.8
    cop_max: float = 5.0
    # --- Gas boiler ----------------------------------------------------------
    boiler_efficiency: float = 0.92   # heat output / fuel input
    # --- Battery -------------------------------------------------------------
    battery_one_way_eff: float = 0.90       # round-trip = eff**2 = 0.81
    battery_power_kw: float = 5.0
    battery_wear_cost: float = 0.02         # EUR / kWh discharged (degradation)
    # --- Investment: real CAPEX + lifetime -> annualised via CRF -------------
    discount_rate: float = 0.04
    pv_capex_per_kwp: float = 1400.0
    pv_lifetime_yr: int = 25
    battery_capex_per_kwh: float = 800.0
    battery_lifetime_yr: int = 12
    hp_capex_per_kw_el: float = 4500.0      # EUR per kW of electrical input capacity
    hp_lifetime_yr: int = 18
    # --- Physical / connection limits ----------------------------------------
    pv_max_kwp: float = 10.0                # usable roof area
    grid_export_limit_kw: float = 7.0       # grid-connection export limit
    fixed_grid_charge_eur_yr: float = 100.0 # annual standing charge (constant)

    # Capital recovery factor: converts an up-front CAPEX into an equal annual cost.
    def _crf(self, lifetime: int) -> float:
        r = self.discount_rate
        return r * (1 + r) ** lifetime / ((1 + r) ** lifetime - 1)

    @property
    def ann_pv(self) -> float: return self.pv_capex_per_kwp * self._crf(self.pv_lifetime_yr)
    @property
    def ann_battery(self) -> float: return self.battery_capex_per_kwh * self._crf(self.battery_lifetime_yr)
    @property
    def ann_hp(self) -> float: return self.hp_capex_per_kw_el * self._crf(self.hp_lifetime_yr)


def heat_pump_cop(outdoor_temp_c, cfg: Config) -> np.ndarray:
    """Air-source heat-pump COP vs outdoor temperature (Staffell et al. 2012)."""
    dT = cfg.hp_sink_temp_c - np.asarray(outdoor_temp_c, dtype=float)
    cop = 6.81 - 0.121 * dT + 0.000630 * dT ** 2
    return np.clip(cop, cfg.cop_min, cfg.cop_max)


def load_inputs(data_dir: str | Path) -> pd.DataFrame:
    """Load and positionally align the four hourly input series (8760 h)."""
    data_dir = Path(data_dir)
    elec = pd.read_csv(data_dir / "electricity_demand_clean.csv")
    pv = pd.read_csv(data_dir / "ninja_pv_51.1638_10.4478_corrected.csv", skiprows=3)
    heat = pd.read_csv(data_dir / "ninja_demand_51.1638_10.4478_uncorrected.csv", skiprows=3)
    for name, frame in {"electricity": elec, "pv": pv, "heat": heat}.items():
        if len(frame) != 8760:
            raise ValueError(f"{name} series has {len(frame)} rows, expected 8760.")
    df = pd.DataFrame({
        "time": pd.date_range("2019-01-01", periods=8760, freq="h"),
        "elec_demand_kwh": elec["electricity_demand_kWh"].to_numpy(float),
        "pv_capacity_factor": pv["electricity"].to_numpy(float),
        "heat_demand_kwh": heat["heating_demand"].to_numpy(float),
        "outdoor_temp_c": pv["temperature"].to_numpy(float),
    })
    if df.isna().any().any():
        raise ValueError("Input data contains NaNs after alignment.")
    return df


def solve_scenario(df: pd.DataFrame, co2_price_eur_per_ton: float,
                   cfg: Config = Config(), solver: str = "highs") -> pd.DataFrame:
    """Build and solve the LP for one CO2 price; return the hourly solution."""
    n = len(df)
    time = pd.RangeIndex(n, name="time")
    p = co2_price_eur_per_ton / 1000.0          # EUR / kg
    eff = cfg.battery_one_way_eff

    # Time series as xarray DataArrays (aligned on the 'time' coordinate)
    demand = xr.DataArray(df["elec_demand_kwh"].to_numpy(float), coords=[time])
    heat = xr.DataArray(df["heat_demand_kwh"].to_numpy(float), coords=[time])
    cf = xr.DataArray(df["pv_capacity_factor"].to_numpy(float), coords=[time])
    cop = xr.DataArray(heat_pump_cop(df["outdoor_temp_c"].to_numpy(float), cfg), coords=[time])

    m = linopy.Model()

    # --- Hourly decision variables (all >= 0) --------------------------------
    grid_in = m.add_variables(lower=0, coords=[time], name="grid_import")
    grid_out = m.add_variables(lower=0, upper=cfg.grid_export_limit_kw, coords=[time], name="grid_export")
    pv_gen = m.add_variables(lower=0, coords=[time], name="pv_generation")
    b_chg = m.add_variables(lower=0, upper=cfg.battery_power_kw, coords=[time], name="battery_charge")
    b_dis = m.add_variables(lower=0, upper=cfg.battery_power_kw, coords=[time], name="battery_discharge")
    b_soc = m.add_variables(lower=0, coords=[time], name="battery_soc")
    gas_in = m.add_variables(lower=0, coords=[time], name="gas_input")
    hp_el = m.add_variables(lower=0, coords=[time], name="heat_pump_el")

    # --- Capacity decision variables (scalars) -------------------------------
    pv_cap = m.add_variables(lower=0, upper=cfg.pv_max_kwp, name="pv_capacity")
    bat_cap = m.add_variables(lower=0, name="battery_capacity")
    hp_cap = m.add_variables(lower=0, name="heat_pump_capacity")

    # --- Constraints ---------------------------------------------------------
    # Electricity balance: import + PV + discharge = demand + charge + hp + export
    m.add_constraints(grid_in + pv_gen + b_dis - b_chg - hp_el - grid_out == demand,
                      name="electricity_balance")
    # Heat balance: boiler heat + heat-pump heat = heat demand
    m.add_constraints(cfg.boiler_efficiency * gas_in + cop * hp_el == heat, name="heat_balance")
    # PV generation limited by installed capacity and capacity factor
    m.add_constraints(pv_gen <= cf * pv_cap, name="pv_availability")
    # State-of-charge / heat-pump output limited by installed capacity
    m.add_constraints(b_soc <= bat_cap, name="soc_capacity")
    m.add_constraints(hp_el <= hp_cap, name="hp_capacity")
    # Battery state-of-charge dynamics (soc_0 = 0).
    #   For t>=1 this links soc_t to soc_{t-1}; the t=0 row reduces to the initial
    #   condition because the shifted term has no predecessor. The explicit
    #   'soc_initial' constraint below pins t=0 regardless of shift edge behaviour.
    m.add_constraints(b_soc - b_soc.shift(time=1) - eff * b_chg + b_dis / eff == 0,
                      name="soc_dynamics")
    m.add_constraints(b_soc.isel(time=0) - eff * b_chg.isel(time=0) + b_dis.isel(time=0) / eff == 0,
                      name="soc_initial")

    # --- Objective: minimise total annual cost -------------------------------
    objective = (
        (cfg.grid_price + cfg.co2_grid * p) * grid_in.sum()
        - cfg.feed_in_tariff * grid_out.sum()
        + (cfg.gas_price + cfg.co2_gas * p) * gas_in.sum()
        + cfg.battery_wear_cost * b_dis.sum()
        + cfg.ann_pv * pv_cap + cfg.ann_battery * bat_cap + cfg.ann_hp * hp_cap
    )
    m.add_objective(objective)          # older linopy: use  m.objective = objective
    m.solve(solver_name=solver)

    # --- Extract solution ----------------------------------------------------
    val = lambda v: np.asarray(v.solution.values, dtype=float)
    cop_np = cop.values
    out = pd.DataFrame({
        "time": df["time"].to_numpy(),
        "co2_price": co2_price_eur_per_ton,
        "grid_import": val(grid_in),
        "grid_export": val(grid_out),
        "pv_generation": val(pv_gen),
        "battery_charge": val(b_chg),
        "battery_discharge": val(b_dis),
        "battery_soc": val(b_soc),
        "gas_input": val(gas_in),
        "heat_pump_el": val(hp_el),
        "cop": cop_np,
    })
    out["gas_heat"] = cfg.boiler_efficiency * out["gas_input"]
    out["heat_pump_heat"] = out["cop"] * out["heat_pump_el"]
    pv_cap_v = float(pv_cap.solution); bat_cap_v = float(bat_cap.solution); hp_cap_v = float(hp_cap.solution)
    out["pv_capacity_kwp"] = pv_cap_v
    out["battery_capacity_kwh"] = bat_cap_v
    out["heat_pump_capacity_kw"] = hp_cap_v
    out["co2_emissions_kg"] = out["grid_import"] * cfg.co2_grid + out["gas_input"] * cfg.co2_gas

    # Total annual system cost recomputed from the solution (robust across linopy versions)
    out["total_system_cost_eur"] = (
        out["grid_import"].sum() * cfg.grid_price
        - out["grid_export"].sum() * cfg.feed_in_tariff
        + out["gas_input"].sum() * cfg.gas_price
        + out["co2_emissions_kg"].sum() * p
        + out["battery_discharge"].sum() * cfg.battery_wear_cost
        + cfg.ann_pv * pv_cap_v + cfg.ann_battery * bat_cap_v + cfg.ann_hp * hp_cap_v
        + cfg.fixed_grid_charge_eur_yr
    )
    return out

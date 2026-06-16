"""Run the CO2-price sweep with the linopy model and write results + summary."""
from pathlib import Path
import pandas as pd
from model import Config, load_inputs, solve_scenario

ROOT = Path(__file__).resolve().parents[1]
DATA, RESULTS = ROOT / "data", ROOT / "results"
RESULTS.mkdir(exist_ok=True)

CO2_PRICES = [0, 25, 50, 75, 100, 125, 150, 175, 200]   # EUR / ton
SOLVER = "highs"


def summarise(h: pd.DataFrame) -> dict:
    grid = h["grid_import"].sum()
    exp = h["grid_export"].sum()
    pv = h["pv_generation"].sum()
    pv_self = pv - exp                       # PV used on-site
    dis, chg = h["battery_discharge"].sum(), h["battery_charge"].sum()
    gas_heat, hp_heat = h["gas_heat"].sum(), h["heat_pump_heat"].sum()
    load_supply = grid + pv_self + dis       # electricity serving on-site load
    hp_mask = h["heat_pump_heat"] > 1e-9
    avg_cop = (h.loc[hp_mask, "heat_pump_heat"].sum()
               / h.loc[hp_mask, "heat_pump_el"].sum()) if hp_mask.any() else 0.0
    return {
        "co2_price": int(h["co2_price"].iloc[0]),
        "total_system_cost_eur": round(float(h["total_system_cost_eur"].iloc[0]), 2),
        "co2_emissions_kg": round(float(h["co2_emissions_kg"].sum()), 1),
        "grid_import_kwh": round(float(grid), 1),
        "grid_export_kwh": round(float(exp), 1),
        "pv_generation_kwh": round(float(pv), 1),
        "pv_self_consumed_kwh": round(float(pv_self), 1),
        "battery_charge_kwh": round(float(chg), 1),
        "battery_discharge_kwh": round(float(dis), 1),
        "gas_heat_kwh": round(float(gas_heat), 1),
        "heat_pump_heat_kwh": round(float(hp_heat), 1),
        "renewable_share_pct": round(float((pv_self + dis) / load_supply * 100), 1),
        "heat_pump_share_pct": round(float(hp_heat / (gas_heat + hp_heat) * 100), 1),
        "avg_heat_pump_cop": round(float(avg_cop), 2),
        "pv_capacity_kwp": round(float(h["pv_capacity_kwp"].iloc[0]), 3),
        "battery_capacity_kwh": round(float(h["battery_capacity_kwh"].iloc[0]), 3),
        "heat_pump_capacity_kw": round(float(h["heat_pump_capacity_kw"].iloc[0]), 3),
    }


def main():
    cfg = Config()
    df = load_inputs(DATA)
    print(f"Loaded {len(df)} h | elec {df.elec_demand_kwh.sum():.0f} kWh/yr | "
          f"heat {df.heat_demand_kwh.sum():.0f} kWh/yr")
    print(f"Annualised costs: PV {cfg.ann_pv:.1f} | battery {cfg.ann_battery:.1f} | "
          f"heat pump {cfg.ann_hp:.1f} EUR/yr per unit")

    hourly_all, rows = [], []
    for price in CO2_PRICES:
        h = solve_scenario(df, price, cfg, solver=SOLVER)
        hourly_all.append(h)
        s = summarise(h)
        rows.append(s)
        print(f"  CO2={price:4d} EUR/t | cost {s['total_system_cost_eur']:8.1f} "
              f"| CO2 {s['co2_emissions_kg']:7.1f} kg | renew {s['renewable_share_pct']:5.1f}% "
              f"| HP heat {s['heat_pump_share_pct']:5.1f}% | PV {s['pv_capacity_kwp']:.2f} kWp")

    pd.concat(hourly_all, ignore_index=True).to_csv(RESULTS / "hourly_results.csv", index=False)
    pd.DataFrame(rows).to_csv(RESULTS / "scenario_summary.csv", index=False)
    print(f"\nWrote {RESULTS/'scenario_summary.csv'} and hourly_results.csv")


if __name__ == "__main__":
    main()

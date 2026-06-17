"""Generate result figures from results/scenario_summary.csv."""
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib as mpl

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "figures"; FIG.mkdir(exist_ok=True)
df = pd.read_csv(ROOT / "results" / "scenario_summary.csv").sort_values("co2_price")
bars = df[df.co2_price.isin([0, 50, 100])]
mpl.rcParams.update({"figure.dpi": 120, "savefig.dpi": 150, "font.size": 11,
                     "axes.grid": True, "grid.alpha": 0.3, "axes.axisbelow": True})


def save(fig, name):
    fig.tight_layout(); fig.savefig(FIG / name, bbox_inches="tight"); plt.close(fig)


fig, ax = plt.subplots(figsize=(7, 4.3))
ax.plot(df.co2_price, df.co2_emissions_kg, "o-", color="#C44E52", lw=2)
ax.set(title="Annual CO₂ Emissions vs. CO₂ Price", xlabel="CO₂ Price (€/ton)", ylabel="CO₂ (kg/yr)")
save(fig, "01_co2_emissions.png")

fig, ax = plt.subplots(figsize=(7, 4.3)); x = bars.co2_price.astype(str)
ax.bar(x, bars.grid_import_kwh, label="Grid import", color="#4C72B0")
ax.bar(x, bars.pv_self_consumed_kwh, bottom=bars.grid_import_kwh, label="PV self-consumed", color="#DD8452")
ax.bar(x, bars.battery_discharge_kwh, bottom=bars.grid_import_kwh + bars.pv_self_consumed_kwh,
       label="Battery discharge", color="#55A868")
ax.set(title="Electricity Supply to Load by CO₂ Price", xlabel="CO₂ Price (€/ton)", ylabel="kWh/yr"); ax.legend()
save(fig, "02_electricity_supply.png")

fig, ax = plt.subplots(figsize=(7, 4.3))
ax.bar(x, bars.gas_heat_kwh, label="Gas boiler", color="#4C72B0")
ax.bar(x, bars.heat_pump_heat_kwh, bottom=bars.gas_heat_kwh, label="Heat pump", color="#DD8452")
ax.set(title="Heat Supply by CO₂ Price", xlabel="CO₂ Price (€/ton)", ylabel="kWh/yr"); ax.legend()
save(fig, "03_heat_supply.png")

fig, ax = plt.subplots(figsize=(7, 4.3))
ax.plot(df.co2_price, df.renewable_share_pct, "o-", color="#2F6DB5", lw=2, label="Renewable electricity share")
ax.plot(df.co2_price, df.heat_pump_share_pct, "s--", color="#DD8452", lw=2, label="Heat-pump share of heat")
ax.set(title="Renewable & Heat-Pump Shares vs. CO₂ Price", xlabel="CO₂ Price (€/ton)", ylabel="Share (%)"); ax.legend()
save(fig, "04_shares.png")

fig, ax = plt.subplots(figsize=(7, 4.3))
ax.plot(df.co2_price, df.pv_capacity_kwp, "o-", label="PV (kWₚ)", lw=2)
ax.plot(df.co2_price, df.battery_capacity_kwh, "s-", label="Battery (kWh)", lw=2)
ax.plot(df.co2_price, df.heat_pump_capacity_kw, "^-", label="Heat pump (kW₍ₑₗ₎)", lw=2)
ax.set(title="Cost-Optimal Capacities vs. CO₂ Price", xlabel="CO₂ Price (€/ton)", ylabel="Installed capacity"); ax.legend()
save(fig, "06_capacities.png")

fig, ax = plt.subplots(figsize=(7, 4.3))
ax.plot(df.co2_price, df.total_system_cost_eur, "o-", color="#2F6DB5", lw=2)
ax.set(title="Total Annual System Cost vs. CO₂ Price", xlabel="CO₂ Price (€/ton)", ylabel="€/yr")
save(fig, "07_total_cost.png")

print("Wrote figures to", FIG)

# Optimal Battery Sizing

This project provides a simple, initial suite of Python tools to solve the problem of first-guess battery energy storage system (BESS) sizing for sites with large solar plants.
Generally, battery sizing is a complex problem involving economics, uncertainty, and grid integration, rather than just an energy balance. For the sake of simplicity, and to serve as a first-guess design tool, this project focuses exclusively on the energy balance constraints, sizing the battery primarily using demand and solar generation profiles.

This repository provides two complementary classes for battery studies:

- `src.bess_optimizer.Optimizer`: solves for optimal battery power (MW) and energy (MWh).
- `src.bess_simulator.Simulator`: simulates dispatch for a fixed battery size.

Use `Optimizer` when capacity is unknown. Use `Simulator` to validate shortlisted candidates.

## Repository Structure

- `src/`: package source code (`Optimizer`, `Simulator`, shared base utilities)
- `notebooks/`: end-to-end workflows (single run, batch sizing, candidate screening, fixed-size validation)
- `data/`: input data (for example `data/solar_profiles.csv`)
- `results/`: generated outputs and CSV reports
- `example_usage_opt.py`: simple optimizer usage example
- `example_usage_sim.py`: simple simulator usage example

## Quick Start

Install dependencies:

```bash
pip install -r requirements.txt
```

### 1) Optimize Battery Size (endogenous sizing)

```python
import numpy as np
from src import Optimizer

solar_profile = np.array([...], dtype=float)
demand_profile = np.array([...], dtype=float)

opt = Optimizer(solver_name="highs", unmet_penalty=1e9)
opt.add_solar(capacity=300.0, profile=solar_profile)
opt.add_storage(
	efficiency=0.9,
	battery_power_cost=200_000.0,
	battery_energy_cost=300_000.0,
	max_battery_power=500.0,
	max_hours=8.0,
)
opt.add_load(demand=demand_profile)
opt.solve()

results = opt.get_results()
print(results["battery_power_capacity"], results["battery_energy_capacity"])
```

### 2) Simulate a Fixed Candidate (exogenous sizing)

```python
import numpy as np
from src import Simulator

solar_profile = np.array([...], dtype=float)
demand_profile = np.array([...], dtype=float)

sim = Simulator(power_capacity=100.0, energy_capacity=650.0, efficiency=0.9)
sim.add_solar(capacity=300.0, profile=solar_profile)
sim.add_load(demand=demand_profile)
sim.simulate()

results = sim.get_results()
print(results["total_unmet"])
```

## API Summary

### Optimizer

Workflow:

1. `add_solar(capacity, profile)`
2. `add_storage(efficiency, battery_power_cost, battery_energy_cost, max_battery_power, max_hours)`
3. `add_load(demand)`
4. `solve()`
5. `get_results()`

Returns hourly dispatch arrays plus:

- `battery_power_capacity`
- `battery_energy_capacity`
- `objective_value`
- `total_unmet`

### Simulator

Workflow:

1. `Simulator(power_capacity, energy_capacity, efficiency)`
2. `add_solar(capacity, profile)`
3. `add_load(demand)`
4. `simulate()`
5. `get_results()`

Returns hourly dispatch arrays plus fixed capacities and `total_unmet`.

## Notebook Mapping

- `notebooks/01_minimal_bess_usage.ipynb`: minimal `Optimizer` run
- `notebooks/02_batch_daily_profiles.ipynb`: batch daily sizing with `Optimizer`
- `notebooks/03_candidate_design_screening.ipynb`: screen candidate designs from batch outputs
- `notebooks/04_validate_p80_full_year.ipynb`: fixed-candidate validation with `Simulator`
FIX THIS======
"""Battery dispatch simulation for a fixed-size BESS.

Use this module when battery power and energy capacity are **already known**
(e.g. from a previous :class:`~bess_optimizer.Optimizer` run, a vendor spec,
or a sensitivity study) and you want to evaluate how the system would perform
in practice.

How the simulation works
------------------------
A greedy forward-dispatch rule is applied at each time-step *t*:

1. **Solar surplus** (solar_max ≥ demand):
   Solar meets the full load; any remaining generation charges the battery
   up to its power and SOC limits.  Unmet load = 0.

2. **Solar deficit** (solar_max < demand):
   Solar covers what it can; the battery discharges to cover the shortfall
   up to its power and SOC limits.  Any residual gap becomes unmet load.

Round-trip efficiency is applied using the split-sqrt convention consistent
with :mod:`bess_optimizer`:

.. math::

    \\text{SOC}_t = \\text{SOC}_{t-1}
                  + \\text{charge}_t \\cdot \\sqrt{\\eta}
                  - \\frac{\\text{discharge}_t}{\\sqrt{\\eta}}

The simulation starts with an empty battery (SOC₀ = 0) and does **not**
enforce a cyclic SOC constraint — it is a forward simulation, not a
steady-state optimisation.

Compare with :mod:`bess_optimizer`
-----------------------------------
If battery sizes are not yet decided, use :class:`~bess_optimizer.Optimizer`
to find them via a linear programme.

Typical usage
-------------
::

    from bess_simulator import Simulator

    sim = Simulator(power_capacity=40.0, energy_capacity=80.0, efficiency=0.9)
    sim.add_solar(capacity=100, profile=capacity_factors)
    sim.add_load(demand=demand_mw)
    sim.simulate()
    results = sim.get_results()
    # results["unmet"]  → hourly unmet load (MW)
    # results["s_soc"]  → hourly battery state of charge (MWh)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

from ._base import _BaseModel, ArrayLike1D


class Simulator(_BaseModel):
    """Run a greedy dispatch simulation for a fixed-size BESS.

    Battery sizes are **required constructor arguments** — they are inputs
    here, unlike in :class:`~bess_optimizer.Optimizer` where they are outputs.

    Parameters
    ----------
    power_capacity:
        Battery power capacity in MW (charge / discharge rate limit).
    energy_capacity:
        Battery energy capacity in MWh (maximum state of charge).
    efficiency:
        Round-trip efficiency in (0, 1].  Defaults to 0.9 (90 %).
    """

    def __init__(
        self,
        power_capacity: float,
        energy_capacity: float,
        efficiency: float = 0.9,
    ) -> None:
        super().__init__()

        if power_capacity < 0:
            raise ValueError("power_capacity must be non-negative")
        if energy_capacity < 0:
            raise ValueError("energy_capacity must be non-negative")
        if not (0 < efficiency <= 1):
            raise ValueError("efficiency must be in (0, 1]")

        self._power_capacity = float(power_capacity)
        self._energy_capacity = float(energy_capacity)
        self._efficiency = float(efficiency)
        self._results: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def simulate(self) -> "Simulator":
        """Run the greedy dispatch simulation.  Call :meth:`get_results` afterwards."""
        self._validate_inputs()
        assert self._solar is not None and self._load is not None

        solar = self._solar
        load = self._load
        T = len(load.demand)
        eff_sqrt = np.sqrt(self._efficiency)

        # Pre-allocate output arrays
        sol = np.zeros(T)
        charge = np.zeros(T)
        discharge = np.zeros(T)
        soc = np.zeros(T)
        unmet = np.zeros(T)

        soc_prev = 0.0  # battery starts empty

        for t in range(T):
            solar_max = solar.capacity * solar.profile[t]
            net = solar_max - load.demand[t]  # positive → surplus, negative → deficit

            if net >= 0:
                # --- surplus: meet load with solar, charge battery with the rest ---
                # How much we can push into the battery this step
                headroom = (self._energy_capacity - soc_prev) / eff_sqrt  # MW equivalent
                c = min(net, self._power_capacity, headroom)
                c = max(0.0, c)

                sol[t] = load.demand[t] + c   # solar dispatched = load + charge
                charge[t] = c
                discharge[t] = 0.0
                unmet[t] = 0.0
            else:
                # --- deficit: solar covers what it can, battery makes up the rest ---
                deficit = -net                               # MW short
                available = soc_prev * eff_sqrt             # max discharge limited by SOC
                d = min(deficit, self._power_capacity, available)
                d = max(0.0, d)

                sol[t] = solar_max
                charge[t] = 0.0
                discharge[t] = d
                unmet[t] = max(0.0, deficit - d)

            # Update SOC
            soc[t] = soc_prev + charge[t] * eff_sqrt - discharge[t] / eff_sqrt
            soc_prev = soc[t]

        self._results = {
            "sol":                     sol,
            "s_charge":                charge,
            "s_discharge":             discharge,
            "s_soc":                   soc,
            "unmet":                   unmet,
            "battery_power_capacity":  self._power_capacity,
            "battery_energy_capacity": self._energy_capacity,
            "objective_value":         None,   # no LP — cost not computed
            "total_unmet":             float(unmet.sum()),
        }
        return self

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def get_results(self) -> Dict[str, Any]:
        """Return simulation results.

        Returns
        -------
        dict with keys:
            ``sol``, ``s_charge``, ``s_discharge``, ``s_soc``, ``unmet``
                Hourly time-series (numpy arrays, MW / MWh).
            ``battery_power_capacity``
                Fixed power capacity as supplied at construction (MW).
            ``battery_energy_capacity``
                Fixed energy capacity as supplied at construction (MWh).
            ``objective_value``
                ``None`` — no cost objective is computed in simulation mode.
            ``total_unmet``
                Total unmet load over the horizon (MWh).
        """
        if self._results is None:
            raise RuntimeError("Simulation has not been run yet — call simulate() first")
        return self._results

"""Battery sizing via linear-programme optimisation.

Use this module when you do **not** know the battery size and want the model
to find the least-cost power capacity (MW) and energy capacity (MWh) that
can meet the given demand at all times.

The optimisation formulation
----------------------------
Decision variables
    ``battery_power_capacity`` (MW) and ``battery_energy_capacity`` (MWh) —
    the two quantities being sized.

Objective
    Minimise capital cost of the battery plus a heavy penalty on any unmet
    load, forcing the solver to cover demand wherever possible:

    .. math::

        \\min \\; c_P \\cdot P_{bat} + c_E \\cdot E_{bat}
             + w_{unmet} \\sum_t u_t

Constraints
    * Solar generation ≤ installed capacity × capacity factor
    * Charge / discharge ≤ battery power capacity
    * State of charge ≤ battery energy capacity
    * Duration limit: E_bat ≤ max_hours × P_bat
    * SOC dynamics with round-trip efficiency (split-sqrt formulation)
    * Cyclic SOC: SOC at end of horizon equals SOC at start
    * Energy balance: solar + discharge − charge + unmet = demand

Compare with :mod:`bess_simulator`
-----------------------------------
If battery sizes are already decided (e.g. from a previous optimisation run
or a vendor spec), use :class:`~bess_simulator.Simulator` instead.  It runs
a fast greedy forward simulation without invoking any LP solver.

Typical usage
-------------
::

    from bess_optimizer import Optimizer

    opt = Optimizer()
    opt.add_solar(capacity=100, profile=capacity_factors)
    opt.add_storage(efficiency=0.9)
    opt.add_load(demand=demand_mw)
    opt.solve()
    results = opt.get_results()
    # results["battery_power_capacity"]  → sized MW
    # results["battery_energy_capacity"] → sized MWh
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import linopy
import numpy as np

from ._base import _BaseModel, ArrayLike1D


@dataclass
class _StorageSpec:
    """Internal storage of sizing assumptions passed to :meth:`Optimizer.add_storage`."""
    efficiency: float
    battery_power_cost: float
    battery_energy_cost: float
    max_battery_power: float
    max_hours: float


class Optimizer(_BaseModel):
    """Size a BESS via linear-programme optimisation.

    Battery power capacity and energy capacity are **decision variables** —
    the model finds the cheapest combination that meets demand.

    Parameters
    ----------
    solver_name:
        LP solver passed to :mod:`linopy` (default ``"highs"``).
    unmet_penalty:
        Cost coefficient applied to each MWh of unmet load in the objective.
        The default (1 × 10⁹) makes unmet load extremely expensive so the
        solver strongly prefers to cover all demand.
    """

    def __init__(self, solver_name: str = "highs", unmet_penalty: float = 1e9) -> None:
        super().__init__()
        self.solver_name = solver_name
        self.unmet_penalty = float(unmet_penalty)
        self._storage: Optional[_StorageSpec] = None
        self._model: Optional[linopy.Model] = None

    # ------------------------------------------------------------------
    # Input registration
    # ------------------------------------------------------------------

    def add_storage(
        self,
        efficiency: float = 0.9,
        battery_power_cost: float = 200_000.0,
        battery_energy_cost: float = 300_000.0,
        max_battery_power: float = 200.0,
        max_hours: float = 4.0,
    ) -> "Optimizer":
        """Set storage sizing assumptions.

        Note: there are **no** ``power_capacity`` or ``energy_capacity``
        arguments here — those are outputs, not inputs.  The parameters below
        only define cost coefficients and upper bounds for the LP.

        Parameters
        ----------
        efficiency:
            Round-trip efficiency (0, 1].
        battery_power_cost:
            Capital cost per MW of power capacity (£ or $).
        battery_energy_cost:
            Capital cost per MWh of energy capacity (£ or $).
        max_battery_power:
            Upper bound on battery power capacity (MW).  Acts as a big-M
            constraint; set generously so it does not restrict the solution.
        max_hours:
            Maximum duration ratio E/P (hours).  Limits the energy-to-power
            ratio of the battery.
        """
        if not (0 < efficiency <= 1):
            raise ValueError("efficiency must be in (0, 1]")
        if max_battery_power < 0:
            raise ValueError("max_battery_power must be non-negative")
        if max_hours <= 0:
            raise ValueError("max_hours must be positive")

        self._storage = _StorageSpec(
            efficiency=float(efficiency),
            battery_power_cost=float(battery_power_cost),
            battery_energy_cost=float(battery_energy_cost),
            max_battery_power=float(max_battery_power),
            max_hours=float(max_hours),
        )
        return self

    # ------------------------------------------------------------------
    # Model construction & solve
    # ------------------------------------------------------------------

    def _build(self) -> linopy.Model:
        self._validate_inputs()
        if self._storage is None:
            raise RuntimeError("Storage assumptions have not been added — call add_storage() first")

        solar = self._solar
        storage = self._storage
        load = self._load
        assert solar is not None and load is not None

        T = range(len(load.demand))
        m = linopy.Model()

        # --- sizing variables (the two decision variables) ---
        P_bat = m.add_variables(lower=0, upper=storage.max_battery_power, name="battery_power_capacity")
        E_bat = m.add_variables(lower=0, name="battery_energy_capacity")
        m.add_constraints(E_bat <= storage.max_hours * P_bat, name="battery_duration_limit")

        # --- time-series variables ---
        sol = m.add_variables(lower=0, coords=[T], name="sol")
        charge = m.add_variables(lower=0, coords=[T], name="s_charge")
        discharge = m.add_variables(lower=0, coords=[T], name="s_discharge")
        soc = m.add_variables(lower=0, coords=[T], name="s_soc")
        unmet = m.add_variables(lower=0, coords=[T], name="unmet")

        # --- bounds ---
        m.add_constraints(sol <= solar.capacity * solar.profile, name="sol_limit")  # type: ignore[arg-type]
        m.add_constraints(charge <= P_bat, name="charge_limit")
        m.add_constraints(discharge <= P_bat, name="discharge_limit")
        m.add_constraints(soc <= E_bat, name="soc_limit")

        # --- SOC dynamics (split-sqrt round-trip efficiency) ---
        eff_sqrt = np.sqrt(storage.efficiency)
        for t in T:
            t_prev = len(load.demand) - 1 if t == 0 else t - 1
            name = "cyclic_soc" if t == 0 else f"soc_{t}"
            m.add_constraints(
                soc[t] == soc[t_prev] + charge[t] * eff_sqrt - discharge[t] / eff_sqrt,
                name=name,
            )

        # --- energy balance ---
        for t in T:
            m.add_constraints(
                sol[t] + discharge[t] - charge[t] + unmet[t] == load.demand[t],  # pyright: ignore[reportOperatorIssue]
                name=f"balance_{t}",
            )

        # --- objective ---
        m.add_objective(
            storage.battery_power_cost * P_bat
            + storage.battery_energy_cost * E_bat
            + (unmet * self.unmet_penalty).sum()
        )
        return m

    def solve(self) -> "Optimizer":
        """Build and solve the LP.  Call :meth:`get_results` afterwards."""
        self._model = self._build()
        self._model.solve(solver_name=self.solver_name)
        return self

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def get_results(self) -> Dict[str, Any]:
        """Return optimisation results.

        Returns
        -------
        dict with keys:
            ``sol``, ``s_charge``, ``s_discharge``, ``s_soc``, ``unmet``
                Hourly time-series (numpy arrays, MW / MWh).
            ``battery_power_capacity``
                Optimal battery power capacity (MW).
            ``battery_energy_capacity``
                Optimal battery energy capacity (MWh).
            ``objective_value``
                Total cost (capital + penalty) from the LP objective.
            ``total_unmet``
                Total unmet load over the horizon (MWh).
        """
        if self._model is None:
            raise RuntimeError("Model has not been solved yet — call solve() first")

        sol = self._model
        obj = sol.objective.value
        if obj is None:
            raise RuntimeError("Solver did not return an objective value")

        results: Dict[str, Any] = {
            "sol":                     sol.solution["sol"].to_numpy(),
            "s_charge":                sol.solution["s_charge"].to_numpy(),
            "s_discharge":             sol.solution["s_discharge"].to_numpy(),
            "s_soc":                   sol.solution["s_soc"].to_numpy(),
            "unmet":                   sol.solution["unmet"].to_numpy(),
            "battery_power_capacity":  float(sol.solution["battery_power_capacity"]),
            "battery_energy_capacity": float(sol.solution["battery_energy_capacity"]),
            "objective_value":         float(obj),
        }
        results["total_unmet"] = float(results["unmet"].sum())
        return results

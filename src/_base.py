"""Shared base class for BESS models.

This module is private (not part of the public API) and exists solely to avoid
duplicating code between :mod:`bess_optimizer` and :mod:`bess_simulator`.

Both ``Optimizer`` and ``Simulator`` inherit from ``_BaseModel``, which owns:

- Input registration  (``add_solar``, ``add_load``)
- Input validation helpers
- Output presentation (``plot_dispatch``, ``report``)

The two subclasses differ fundamentally in *how they produce results*:

- ``Optimizer``  solves a linear programme (LP) to *find* the optimal battery
  power and energy capacity.
- ``Simulator``  accepts fixed battery sizes and runs a greedy forward-dispatch
  simulation — no LP solver involved.

Because both expose an identical ``get_results()`` interface, the base-class
``plot_dispatch`` and ``report`` methods work polymorphically for both.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import numpy as np


ArrayLike1D = Sequence[float] | np.ndarray[Any, Any]


@dataclass
class SolarSpec:
    capacity: float
    profile: np.ndarray


@dataclass
class LoadSpec:
    demand: np.ndarray


class _BaseModel(ABC):
    """Abstract base providing shared input handling and output presentation.

    Not intended for direct instantiation.  Use :class:`~bess_optimizer.Optimizer`
    or :class:`~bess_simulator.Simulator` instead.
    """

    def __init__(self) -> None:
        self._solar: Optional[SolarSpec] = None
        self._load: Optional[LoadSpec] = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_1d_array(values: ArrayLike1D, name: str) -> np.ndarray:
        """Convert *values* to a 1-D float NumPy array, raising on bad input."""
        array = np.asarray(values, dtype=float)
        if array.ndim != 1:
            raise ValueError(f"{name} must be a one-dimensional sequence")
        if len(array) == 0:
            raise ValueError(f"{name} cannot be empty")
        return array

    # ------------------------------------------------------------------
    # Input registration
    # ------------------------------------------------------------------

    def add_solar(self, capacity: float, profile: ArrayLike1D) -> "_BaseModel":
        """Register a solar farm.

        Parameters
        ----------
        capacity:
            Installed solar capacity in MW.
        profile:
            Hourly capacity factors (values in [0, 1]) with one entry per
            time-step.  Must have the same length as the demand array passed
            to :meth:`add_load`.
        """
        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        self._solar = SolarSpec(
            capacity=float(capacity),
            profile=self._to_1d_array(profile, "profile"),
        )
        return self

    def add_load(self, demand: ArrayLike1D) -> "_BaseModel":
        """Register the demand time-series in MW."""
        self._load = LoadSpec(demand=self._to_1d_array(demand, "demand"))
        return self

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_inputs(self) -> None:
        """Raise if required inputs are missing or inconsistent."""
        if self._solar is None:
            raise RuntimeError("Solar data has not been added — call add_solar() first")
        if self._load is None:
            raise RuntimeError("Load data has not been added — call add_load() first")
        if len(self._solar.profile) != len(self._load.demand):
            raise ValueError("Solar profile and demand must have the same length")

    # ------------------------------------------------------------------
    # Abstract interface — subclasses must implement
    # ------------------------------------------------------------------

    @abstractmethod
    def get_results(self) -> Dict[str, Any]:
        """Return a results dictionary.

        Expected keys (all subclasses must provide):

        ``sol``, ``s_charge``, ``s_discharge``, ``s_soc``, ``unmet``,
        ``battery_power_capacity``, ``battery_energy_capacity``,
        ``objective_value`` (or ``None`` for simulation), ``total_unmet``.
        """

    # ------------------------------------------------------------------
    # Output presentation
    # ------------------------------------------------------------------

    def plot_dispatch(self, save_path: Optional[str] = None, show: bool = False) -> Figure:
        """Plot dispatch and battery state-of-charge.

        Parameters
        ----------
        save_path:
            If given, the figure is saved to this path (parent dirs created
            automatically).
        show:
            If ``True``, call ``plt.show()`` before returning.
        """
        results = self.get_results()
        assert self._load is not None
        hours = np.arange(len(self._load.demand))

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

        ax1.plot(hours, self._load.demand, "k-", lw=2, label="Demand", marker="o")
        ax1.plot(hours, results["sol"], "y-", lw=2, label="Solar Generation", marker="s")
        ax1.plot(hours, results["s_discharge"], "g-", lw=2, label="Battery Discharge", marker="^")
        ax1.plot(hours, -results["s_charge"], "b-", lw=2, label="Battery Charge (negative)", marker="v")
        ax1.plot(hours, results["unmet"], "r--", lw=2, label="Unmet Load", marker="x")
        ax1.axhline(y=0, color="gray", lw=0.5)
        ax1.set_ylabel("Power (MW)")
        ax1.set_title("Energy Dispatch")
        ax1.legend(loc="upper left")
        ax1.grid(True, alpha=0.3)

        ax2.fill_between(hours, 0, results["s_soc"], alpha=0.3, color="blue")
        ax2.plot(hours, results["s_soc"], "b-", lw=2, marker="o", label="Battery SOC")
        ax2.axhline(
            y=results["battery_energy_capacity"],
            color="r",
            linestyle="--",
            lw=1,
            label=f'Battery Capacity ({results["battery_energy_capacity"]:.1f} MWh)',
        )
        ax2.set_xlabel("Hour")
        ax2.set_ylabel("Energy (MWh)")
        ax2.set_title("Battery State of Charge")
        ax2.legend(loc="upper right")
        ax2.grid(True, alpha=0.3)
        ax2.set_xlim(-0.5, len(hours) - 0.5)

        plt.tight_layout()
        if save_path is not None:
            output_path = Path(save_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(output_path, dpi=150, bbox_inches="tight")
        if show:
            plt.show()
        return fig

    def _format_report_text(self, include_hourly_details: bool = True) -> str:
        """Format the report text in its three sections.
        Called by report() to generate the text content."""

        results = self.get_results()
        assert self._load is not None and self._solar is not None

        duration = results["battery_energy_capacity"] / max(results["battery_power_capacity"], 0.001)
        obj = results["objective_value"]
        obj_str = f"${obj:,.2f}" if obj is not None else "N/A (simulation)"

        lines = [
            "=== Optimal Battery Sizing ===",
            f"Battery power capacity:   {results['battery_power_capacity']:.2f} MW",
            f"Battery energy capacity:  {results['battery_energy_capacity']:.2f} MWh",
            f"Battery duration:         {duration:.2f} hours",
            "",
            "=== System Performance ===",
            f"Objective value:          {obj_str}",
            f"Solar generation:         {results['sol'].sum():.2f} MWh",
            f"Battery discharge:        {results['s_discharge'].sum():.2f} MWh",
            f"Battery charge:           {results['s_charge'].sum():.2f} MWh",
            f"Unmet load:               {results['total_unmet']:.2f} MWh",
        ]

        if include_hourly_details:
            lines += [
                "",
                "=== Hourly Details ===",
                "Hour | Demand | Solar Max | Solar Gen | Charge | Discharge | SOC | Unmet",
                "-" * 80,
            ]
            for t in range(len(self._load.demand)):
                solar_max = self._solar.capacity * self._solar.profile[t]
                lines.append(
                    f"{t:4d} | {self._load.demand[t]:6.1f} | {solar_max:9.1f} | "
                    f"{results['sol'][t]:9.2f} | {results['s_charge'][t]:6.2f} | "
                    f"{results['s_discharge'][t]:9.2f} | {results['s_soc'][t]:6.2f} | "
                    f"{results['unmet'][t]:5.2f}"
                )

        return "\n".join(lines)

    def report(
        self,
        save_plot_path: Optional[str] = None,
        show_plot: bool = False,
        save_text_path: Optional[str] = "results/model_report.txt",
        include_hourly_details: bool = True,
    ) -> Figure:
        """Print a summary report, optionally save it, and return the dispatch figure."""
        text = self._format_report_text(include_hourly_details=include_hourly_details)
        print("\n" + "=" * 40 + "\n" + text + "\n" + "=" * 40 + "\n")

        if save_text_path is not None:
            path = Path(save_text_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")

        return self.plot_dispatch(save_path=save_plot_path, show=show_plot)

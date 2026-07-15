import numpy as np
import pandas as pd
import linopy
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt

# This is a simple energy system model for a solar farm with battery storage
# It has solar generation, storage, and load
# The goal is to meet demand while minimizing unmet load

def run_model(d, sol_cap, sol_profile, s_eff, battery_power_cost=200000, battery_energy_cost=300000, 
              max_battery_power=200, max_hours=4):
    """
    Run optimization model with battery sizing
    
    d: demand timeseries
    sol_cap: solar capacity
    sol_profile: solar availability profile (capacity factors)
    s_eff: storage round trip efficiency
    battery_power_cost: cost of battery power capacity ($/MW) - inverter/power electronics
    battery_energy_cost: cost of battery energy capacity ($/MWh) - battery cells
    max_battery_power: maximum allowable battery power capacity (MW)
    max_hours: maximum battery duration in hours (e.g., 4 for a 4-hour battery)
    
    Note: Both power and energy costs are needed because they represent separate components:
    - Power cost: inverters/converters that control charge/discharge rate
    - Energy cost: battery cells that store the actual energy
    A 4-hour battery needs less cells than an 8-hour battery (same power, different energy)
    """
    
    T = len(d)
    timesteps = range(T)
    
    # Create model
    m = linopy.Model()
    
    # Battery capacity decision variables (scalar - single value, not time-indexed)
    s_p_cap = m.add_variables(lower=0, upper=max_battery_power, name="battery_power_capacity")
    s_e_cap = m.add_variables(lower=0, name="battery_energy_capacity")
    
    # Constraint: Battery duration (energy/power ratio) cannot exceed max_hours
    # This creates: s_e_cap <= s_p_cap * max_hours
    m.add_constraints(s_e_cap <= max_hours * s_p_cap, name="battery_duration_limit")
    
    # Solar generation
    sol = m.add_variables(lower=0, coords=[timesteps], name="sol")
    for t in timesteps:
        m.add_constraints(sol[t] <= sol_cap * sol_profile[t], name=f"sol_limit_{t}")
    
    # Storage variables (no upper bounds here, will be constrained below)
    s_charge = m.add_variables(lower=0, coords=[timesteps], name="s_charge")
    s_discharge = m.add_variables(lower=0, coords=[timesteps], name="s_discharge")
    s_soc = m.add_variables(lower=0, coords=[timesteps], name="s_soc")
    
    # Constrain storage power and energy to capacity
    # Use linopy's constraint syntax for scalar vs time-indexed variables
    m.add_constraints(s_charge <= s_p_cap, name="charge_limit")
    m.add_constraints(s_discharge <= s_p_cap, name="discharge_limit")
    m.add_constraints(s_soc <= s_e_cap, name="soc_limit")
    
    # Unmet load (we want to minimize this)
    unmet = m.add_variables(lower=0, coords=[timesteps], name="unmet")
    
    # Storage dynamics with cyclic constraint (daily repeating pattern)
    # Battery state at hour 0 should account for state from end of previous day (hour T-1)
    for t in timesteps:
        if t == 0:
            # For cyclic operation: start from end-of-day state
            # We'll add an initial SOC variable to represent this
            pass  # Will handle this below with cyclic constraint
        else:
            m.add_constraints(s_soc[t] == s_soc[t-1] + s_charge[t] * np.sqrt(s_eff) - s_discharge[t] / np.sqrt(s_eff), name=f"soc_{t}")
    
    # Cyclic constraint: SOC at t=0 should equal what remains after hour T-1 plus hour 0 operations
    # This creates: s_soc[0] = s_soc[T-1] + charge[0] - discharge[0]
    m.add_constraints(s_soc[0] == s_soc[T-1] + s_charge[0] * np.sqrt(s_eff) - s_discharge[0] / np.sqrt(s_eff), 
                     name="cyclic_soc")
    
    # Energy balance: solar + discharge - charge + unmet = demand
    for t in timesteps:
        m.add_constraints(sol[t] + s_discharge[t] - s_charge[t] + unmet[t] == d[t], name=f"balance_{t}")
    
    # Objective: minimize battery cost + unmet load penalty
    penalty = 1e9  # $/MWh penalty for unmet load
    obj = battery_power_cost * s_p_cap + battery_energy_cost * s_e_cap + (unmet * penalty).sum()
    m.add_objective(obj)
    
    # Solve
    m.solve(solver_name="highs")
    
    return m

def get_results(m):
    """Extract results from model"""
    res = {}
    res['sol'] = m.solution['sol'].to_numpy()
    res['s_charge'] = m.solution['s_charge'].to_numpy()
    res['s_discharge'] = m.solution['s_discharge'].to_numpy()
    res['s_soc'] = m.solution['s_soc'].to_numpy()
    res['unmet'] = m.solution['unmet'].to_numpy()
    res['total_unmet'] = res['unmet'].sum()
    res['battery_power_capacity'] = float(m.solution['battery_power_capacity'])
    res['battery_energy_capacity'] = float(m.solution['battery_energy_capacity'])
    res['objective_value'] = m.objective.value
    return res


if __name__ == "__main__":
    # Example usage
    T = 24
    demand = np.array([50, 45, 40, 38, 36, 38, 45, 60, 75, 85, 90, 92, 
                       90, 88, 86, 88, 92, 95, 90, 80, 70, 65, 58, 52])
    
    # Solar farm
    solar_capacity = 300  # MW
    solar_profile = np.array([0, 0, 0, 0, 0, 0, 0.1, 0.3, 0.5, 0.7, 0.85, 0.95,
                              0.98, 0.95, 0.88, 0.75, 0.55, 0.3, 0.1, 0, 0, 0, 0, 0])
    
    # Storage: battery parameters
    battery_efficiency = 0.9  # round trip
    battery_power_cost = 200000  # $/MW (inverter/power electronics cost)
    battery_energy_cost = 300000  # $/MWh (battery cell cost)
    
    model = run_model(demand, solar_capacity, solar_profile, battery_efficiency,
                     battery_power_cost, battery_energy_cost,
                     max_battery_power=500,  # Allow up to 500 MW
                     max_hours=8)  # Allow up to 8-hour battery
    
    results = get_results(model)
    
    print("\n=== Optimal Battery Sizing ===")
    print(f"Battery power capacity: {results['battery_power_capacity']:.2f} MW")
    print(f"Battery energy capacity: {results['battery_energy_capacity']:.2f} MWh")
    print(f"Battery duration: {results['battery_energy_capacity']/max(results['battery_power_capacity'], 0.001):.2f} hours")
    print(f"\n=== System Performance ===")
    print(f"Objective value: ${results['objective_value']:,.2f}")
    print(f"Solar generation: {results['sol'].sum():.2f} MWh")
    print(f"Battery discharge: {results['s_discharge'].sum():.2f} MWh")
    print(f"Battery charge: {results['s_charge'].sum():.2f} MWh")
    print(f"Unmet load: {results['total_unmet']:.2f} MWh")
    if results['total_unmet'] > 0.01:
        print("Warning: Load is not fully met. Consider adjusting cost parameters or limits.")
    
    # Detailed hourly breakdown
    print("\n=== Hourly Details ===")
    print("Hour | Demand | Solar Max | Solar Gen | Charge | Discharge | SOC | Unmet")
    print("-" * 80)
    for t in range(min(25, T)):
        solar_max = solar_capacity * solar_profile[t]
        print(f"{t:4d} | {demand[t]:6.1f} | {solar_max:9.1f} | {results['sol'][t]:9.2f} | "
              f"{results['s_charge'][t]:6.2f} | {results['s_discharge'][t]:9.2f} | "
              f"{results['s_soc'][t]:6.2f} | {results['unmet'][t]:5.2f}")
    
    # Create dispatch plot
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
    hours = np.arange(T)
    
    # Top plot: Power flows
    ax1.plot(hours, demand, 'k-', linewidth=2, label='Demand', marker='o')
    ax1.plot(hours, results['sol'], 'y-', linewidth=2, label='Solar Generation', marker='s')
    ax1.plot(hours, results['s_discharge'], 'g-', linewidth=2, label='Battery Discharge', marker='^')
    ax1.plot(hours, -results['s_charge'], 'b-', linewidth=2, label='Battery Charge (negative)', marker='v')
    ax1.plot(hours, results['unmet'], 'r--', linewidth=2, label='Unmet Load', marker='x')
    
    ax1.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
    ax1.set_ylabel('Power (MW)', fontsize=12)
    ax1.set_title('24-Hour Energy Dispatch', fontsize=14, fontweight='bold')
    ax1.legend(loc='upper left', fontsize=10)
    ax1.grid(True, alpha=0.3)
    
    # Bottom plot: Battery state of charge
    ax2.fill_between(hours, 0, results['s_soc'], alpha=0.3, color='blue', label='Battery SOC')
    ax2.plot(hours, results['s_soc'], 'b-', linewidth=2, marker='o')
    ax2.axhline(y=results['battery_energy_capacity'], color='r', linestyle='--', 
                linewidth=1, label=f'Battery Capacity ({results["battery_energy_capacity"]:.1f} MWh)')
    
    ax2.set_xlabel('Hour', fontsize=12)
    ax2.set_ylabel('Energy (MWh)', fontsize=12)
    ax2.set_title('Battery State of Charge', fontsize=14, fontweight='bold')
    ax2.legend(loc='upper right', fontsize=10)
    ax2.grid(True, alpha=0.3)
    ax2.set_xlim(-0.5, T-0.5)
    
    plt.tight_layout()
    plt.savefig('dispatch_plot.png', dpi=150, bbox_inches='tight')
    print("\n📊 Dispatch plot saved as 'dispatch_plot.png'")
    plt.close()
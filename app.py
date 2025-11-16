import streamlit as st
import pandas as pd
import json
from typing import Dict, List
from io import BytesIO
from products_data import SAMPLE_PRODUCTS

# Compatibility helper for button width parameter
def get_button_width_param():
    """Returns the appropriate width parameter for buttons based on Streamlit version."""
    try:
        import streamlit as st_module
        version = st_module.__version__
        # Parse version (e.g., "1.29.0" -> [1, 29, 0])
        version_parts = [int(x) for x in version.split('.')]
        # width parameter was added around version 1.40+
        if version_parts[0] > 1 or (version_parts[0] == 1 and version_parts[1] >= 40):
            return {'width': 'stretch'}
        else:
            return {'use_container_width': True}
    except:
        # Fallback to old parameter if version check fails
        return {'use_container_width': True}

# Compatibility helper for dataframe width parameter
def get_dataframe_width_param():
    """Returns the appropriate width parameter for dataframes based on Streamlit version."""
    try:
        import streamlit as st_module
        version = st_module.__version__
        version_parts = [int(x) for x in version.split('.')]
        # width parameter for dataframes was added around version 1.40+
        if version_parts[0] > 1 or (version_parts[0] == 1 and version_parts[1] >= 40):
            return {'width': 'stretch'}
        else:
            return {'use_container_width': True}
    except:
        return {'use_container_width': True}

# Compatibility helper for download_button width parameter
def get_download_button_width_param():
    """Returns the appropriate width parameter for download buttons based on Streamlit version."""
    try:
        import streamlit as st_module
        version = st_module.__version__
        version_parts = [int(x) for x in version.split('.')]
        # width parameter was added around version 1.40+
        if version_parts[0] > 1 or (version_parts[0] == 1 and version_parts[1] >= 40):
            return {'width': 'stretch'}
        else:
            return {'use_container_width': True}
    except:
        return {'use_container_width': True}

# Page configuration
st.set_page_config(
    page_title="Manufacturing Run Time Planner",
    page_icon="⚙️",
    layout="wide"
)

# Initialize session state
if 'products' not in st.session_state:
    st.session_state.products = []
if 'plan_quantities' not in st.session_state:
    st.session_state.plan_quantities = {}
if 'selected_products' not in st.session_state:
    st.session_state.selected_products = []
if 'editing_product_id' not in st.session_state:
    st.session_state.editing_product_id = None
if 'product_search' not in st.session_state:
    st.session_state.product_search = ""
if 'schedule_generated' not in st.session_state:
    st.session_state.schedule_generated = False
if 'generate_schedule_requested' not in st.session_state:
    st.session_state.generate_schedule_requested = False

# Initialize products if empty
if len(st.session_state.products) == 0:
    st.session_state.products = SAMPLE_PRODUCTS.copy()

def calculate_machine_time(products: List[Dict], plan_quantities: Dict[str, int]) -> Dict[str, float]:
    """Calculate total machine run time in seconds for each machine."""
    machine_run_times = {}
    
    for product in products:
        quantity = plan_quantities.get(product['id'], 0)
        if quantity > 0:
            for op in product['operations']:
                cycle_time = op.get('cycleTimeSeconds', 0)
                time_required = quantity * cycle_time
                machine_id = op['machine']
                machine_run_times[machine_id] = machine_run_times.get(machine_id, 0) + time_required
    
    return machine_run_times

def calculate_machine_breakdown(products: List[Dict], plan_quantities: Dict[str, int]) -> Dict[str, List[Dict]]:
    """Calculate breakdown of which products use each machine and their time contributions."""
    machine_breakdown = {}
    
    for product in products:
        quantity = plan_quantities.get(product['id'], 0)
        if quantity > 0:
            for op in product['operations']:
                cycle_time = op.get('cycleTimeSeconds', 0)
                time_required_seconds = quantity * cycle_time
                machine_id = op['machine']
                
                if machine_id not in machine_breakdown:
                    machine_breakdown[machine_id] = []
                
                machine_breakdown[machine_id].append({
                    'product_id': product['id'],
                    'product_name': product['name'],
                    'quantity': quantity,
                    'cycle_time_seconds': cycle_time,
                    'total_time_seconds': time_required_seconds,
                    'total_time_hours': time_required_seconds / 3600
                })
    
    # Sort each machine's breakdown by total time (descending)
    for machine_id in machine_breakdown:
        machine_breakdown[machine_id].sort(key=lambda x: x['total_time_seconds'], reverse=True)
    
    return machine_breakdown

def format_time(total_seconds: float) -> str:
    """Format seconds to Hh Mm Ss format."""
    total_minutes = total_seconds / 60
    hours = int(total_minutes // 60)
    minutes = int(total_minutes % 60)
    seconds = int(total_seconds % 60)
    return f"{hours}h {minutes}m {seconds}s"

def create_manufacturing_schedule(products: List[Dict], plan_quantities: Dict[str, int], hours_per_day: float = 24.0, max_days: int = 30, max_chunk: float = None) -> tuple:
    """
    Create a daily manufacturing schedule using coordinated multi-op scheduling.
    All operations of a product must run simultaneously (strict coordination).
    
    Args:
        products: List of products with operations
        plan_quantities: Dictionary of product_id -> quantity
        hours_per_day: Available working hours per day (default 24)
        max_days: Scheduling horizon (default 30 days)
        max_chunk: Maximum chunk size in hours (optional, e.g., 8 or 16)
    
    Returns:
        Tuple of (daily_schedules, remaining_quantities, product_data)
    """
    EPS = 1e-3  # Small epsilon for floating point comparisons
    
    # PREPROCESS: Build machine map
    machines = {}
    all_machines_set = set()
    
    # First pass: collect all machines from products
    for product in products:
        if plan_quantities.get(product['id'], 0) > 0:
            for op in product['operations']:
                all_machines_set.add(op['machine'])
    
    # Initialize machines with daily capacity
    for machine_id in all_machines_set:
        machines[machine_id] = {
            'dailyCapacity': hours_per_day,
            'avail': 0,  # Will be reset each day
            'reserved': False,
            'tasksToday': []
        }
    
    # PREPROCESS: Build product map with remaining hours per machine
    product_data = {}
    machine_to_products = {m: [] for m in all_machines_set}  # Reverse index
    
    for product in products:
        product_id = product['id']
        quantity = plan_quantities.get(product_id, 0)
        
        if quantity <= 0:
            continue
        
        # Calculate remaining hours per machine for this product
        operations = product['operations']
        required_machines = [op['machine'] for op in operations]
        is_multi_op = len(required_machines) > 1
        
        # Calculate hours needed per machine
        remaining_hours = {}
        for op in operations:
            machine = op['machine']
            cycle_time_hours = op['cycleTimeSeconds'] / 3600
            total_hours = quantity * cycle_time_hours
            remaining_hours[machine] = total_hours
        
        product_data[product_id] = {
            'id': product_id,
            'name': product.get('name', product_id),
            'machines': required_machines,
            'remainingHours': remaining_hours,
            'isMultiOp': is_multi_op,
            'completed': False,
            'totalQuantity': quantity,
            'completedQuantity': 0,  # Track completed quantity
            'cycleTimeHours': max(op['cycleTimeSeconds'] for op in operations) / 3600  # Max cycle time in hours
        }
        
        # Build reverse index
        for machine in required_machines:
            machine_to_products[machine].append(product_id)
    
    # Priority function: largest total remaining hours (reduces large jobs early)
    def get_priority(product_id):
        prod = product_data[product_id]
        return sum(prod['remainingHours'].values())
    
    # Find all unfinished multi-op products
    def get_unfinished_multi_products():
        return [
            product_id for product_id, prod in product_data.items()
            if not prod['completed'] and prod['isMultiOp']
        ]
    
    # Find all unfinished single-op products for a machine
    def get_unfinished_single_ops_for_machine(machine):
        return [
            product_id for product_id, prod in product_data.items()
            if not prod['completed'] and not prod['isMultiOp']
            and machine in prod['machines']
            and prod['remainingHours'].get(machine, 0) > EPS
        ]
    
    # Find products including a machine
    def get_products_including_machine(machine):
        return [
            product_id for product_id in machine_to_products.get(machine, [])
            if not product_data[product_id]['completed']
        ]
    
    # Pick product by priority (largest total remaining hours)
    def pick_by_priority(product_ids):
        if not product_ids:
            return None
        return max(product_ids, key=get_priority)
    
    # SCHEDULING: Day loop
    daily_schedules = []
    
    for day in range(1, max_days + 1):
        # Reset machines for the day
        for machine_id in machines:
            machines[machine_id]['avail'] = machines[machine_id]['dailyCapacity']
            machines[machine_id]['reserved'] = False
            machines[machine_id]['tasksToday'] = []
        
        day_schedule = {
            'day': day,
            'products': [],
            'machines_used': set(),
            'machine_times': {},
            'machine_end_times': {},
            'machine_tasks': {m: [] for m in machines},
            'machine_time_slots': {m: [] for m in machines},  # Track actual time slots [start, end]
            'total_hours': 0,
            'product_quantities': {}  # Track quantities completed per product this day
        }
        
        made_assignment = True
        iteration = 0
        max_iterations = 10000  # Safety limit
        
        while made_assignment and iteration < max_iterations:
            iteration += 1
            made_assignment = False
            
            # 1. Try to start any multi-op where all machines are free and have avail
            unfinished_multi = get_unfinished_multi_products()
            eligible_multi = []
            for pid in unfinished_multi:
                prod = product_data[pid]
                # Only check machines that still have remaining hours
                active_machines = [m for m in prod['machines'] if m in prod['remainingHours']]
                if active_machines and all(
                    machines[m]['avail'] > EPS and
                    not machines[m]['reserved'] and
                    prod['remainingHours'][m] > EPS
                    for m in active_machines
                ):
                    eligible_multi.append(pid)
            
            if eligible_multi:
                prod_id = pick_by_priority(eligible_multi)
                prod = product_data[prod_id]
                
                # Compute synchronized chunk - only consider machines with remaining hours
                active_machines = [m for m in prod['machines'] if m in prod['remainingHours']]
                if not active_machines:
                    continue  # No active machines, skip this product
                
                chunk = min(
                    min(machines[m]['avail'] for m in active_machines),
                    min(prod['remainingHours'][m] for m in active_machines)
                )
                
                if max_chunk:
                    chunk = min(chunk, max_chunk)
                
                if chunk > EPS:
                    # Reserve and assign chunk to all active machines (use the same list we computed chunk with)
                    machines_to_update = active_machines.copy()  # Use the active_machines we already computed
                    for machine in machines_to_update:
                        # Double-check machine is still in remainingHours (safety check)
                        if machine not in prod['remainingHours']:
                            continue
                        
                        # Get current remaining hours safely
                        current_remaining = prod['remainingHours'].get(machine, 0)
                        if current_remaining <= EPS:
                            continue  # Skip if already completed
                        
                        machines[machine]['avail'] -= chunk
                        machines[machine]['reserved'] = True
                        machines[machine]['tasksToday'].append({
                            'product': prod_id,
                            'hours': chunk,
                            'coordinated': True
                        })
                        
                        # Update remaining hours
                        new_remaining = current_remaining - chunk
                        if new_remaining <= EPS:
                            del prod['remainingHours'][machine]
                        else:
                            prod['remainingHours'][machine] = new_remaining
                    
                    # Check if product is completed
                    if len(prod['remainingHours']) == 0:
                        prod['completed'] = True
                    
                    # Calculate units completed from chunk hours
                    # Units = chunk_hours / cycle_time_per_unit
                    units_completed = int(chunk / prod['cycleTimeHours']) if prod.get('cycleTimeHours', 0) > 0 else 0
                    
                    # CRITICAL FIX: Cap units to not exceed planned quantity
                    remaining_quantity = prod['totalQuantity'] - prod['completedQuantity']
                    if remaining_quantity <= 0:
                        # Already completed all planned units, skip this product
                        continue
                    units_completed = min(units_completed, remaining_quantity)
                    
                    if units_completed > 0:
                        # Update completed quantity
                        prod['completedQuantity'] += units_completed
                        # Cap to exact quantity to prevent over-production
                        if prod['completedQuantity'] >= prod['totalQuantity']:
                            prod['completedQuantity'] = prod['totalQuantity']
                            prod['completed'] = True
                            # Clear remaining hours since we've completed the planned quantity
                            prod['remainingHours'] = {}
                        # Track in day schedule
                        day_schedule['product_quantities'][prod_id] = day_schedule['product_quantities'].get(prod_id, 0) + units_completed
                    
                    # Track in day schedule - use only active machines
                    if prod_id not in [p['product_id'] for p in day_schedule['products']]:
                        day_schedule['products'].append({
                            'product_id': prod_id,
                            'product_name': prod['name'],
                            'units': units_completed,
                            'machines': machines_to_update,  # Only active machines
                            'time_hours': chunk,
                            'chunk_hours': chunk,
                            'coordinated': True
                        })
                    else:
                        # Update existing entry
                        for p in day_schedule['products']:
                            if p['product_id'] == prod_id:
                                p['time_hours'] += chunk
                                p['chunk_hours'] = p.get('chunk_hours', 0) + chunk
                                p['units'] = p.get('units', 0) + units_completed
                                # Update machines list to include only active ones
                                p['machines'] = list(set(p.get('machines', []) + machines_to_update))
                                break
                    
                    # Track time slots for machines (for coordinated multi-op, all start at same time)
                    # Find the earliest available time for all machines
                    earliest_start = 0
                    for machine in machines_to_update:
                        if day_schedule['machine_time_slots'][machine]:
                            # Find the latest end time for this machine
                            latest_end = max(slot[1] for slot in day_schedule['machine_time_slots'][machine])
                            earliest_start = max(earliest_start, latest_end)
                    
                    # All machines in coordinated operation start and end at the same time
                    start_time = earliest_start
                    end_time = start_time + chunk
                    
                    for machine in machines_to_update:
                        day_schedule['machines_used'].add(machine)
                        day_schedule['machine_tasks'][machine].append({
                            'product': prod_id,
                            'hours': chunk,
                            'coordinated': True,
                            'start_time': start_time,
                            'end_time': end_time
                        })
                        # Track time slot
                        day_schedule['machine_time_slots'][machine].append([start_time, end_time])
                    
                    made_assignment = True
                    continue
            
            # 2. Find alternative products for idle machines
            idle_machines = [
                m for m in machines
                if machines[m]['avail'] > EPS and not machines[m]['reserved']
            ]
            
            for machine in idle_machines:
                candidates = []
                for pid in get_products_including_machine(machine):
                    prod = product_data[pid]
                    if not prod['isMultiOp']:
                        continue
                    # Only check machines that still have remaining hours
                    active_machines = [mm for mm in prod['machines'] if mm in prod['remainingHours']]
                    if active_machines and all(
                        machines[mm]['avail'] > EPS and
                        not machines[mm]['reserved'] and
                        prod['remainingHours'][mm] > EPS
                        for mm in active_machines
                    ):
                        candidates.append(pid)
                
                if candidates:
                    prod_id = pick_by_priority(candidates)
                    prod = product_data[prod_id]
                    
                    # Only consider machines with remaining hours
                    active_machines = [m for m in prod['machines'] if m in prod['remainingHours']]
                    if not active_machines:
                        continue  # No active machines, skip this product
                    
                    chunk = min(
                        min(machines[m]['avail'] for m in active_machines),
                        min(prod['remainingHours'][m] for m in active_machines)
                    )
                    
                    if max_chunk:
                        chunk = min(chunk, max_chunk)
                    
                    if chunk > EPS:
                        # Only update active machines (use the same list we computed chunk with)
                        machines_to_update = active_machines.copy()  # Use the active_machines we already computed
                        for m in machines_to_update:
                            # Double-check machine is still in remainingHours (safety check)
                            if m not in prod['remainingHours']:
                                continue
                            
                            # Get current remaining hours safely
                            current_remaining = prod['remainingHours'].get(m, 0)
                            if current_remaining <= EPS:
                                continue  # Skip if already completed
                            
                            machines[m]['avail'] -= chunk
                            machines[m]['reserved'] = True
                            machines[m]['tasksToday'].append({
                                'product': prod_id,
                                'hours': chunk,
                                'coordinated': True
                            })
                            
                            # Update remaining hours
                            new_remaining = current_remaining - chunk
                            if new_remaining <= EPS:
                                del prod['remainingHours'][m]
                            else:
                                prod['remainingHours'][m] = new_remaining
                        
                        # Calculate units completed from chunk hours
                        units_completed = int(chunk / prod['cycleTimeHours']) if prod.get('cycleTimeHours', 0) > 0 else 0
                        
                        # CRITICAL FIX: Cap units to not exceed planned quantity
                        remaining_quantity = prod['totalQuantity'] - prod['completedQuantity']
                        if remaining_quantity <= 0:
                            # Already completed all planned units, skip this product
                            continue
                        units_completed = min(units_completed, remaining_quantity)
                        
                        if units_completed > 0:
                            prod['completedQuantity'] += units_completed
                            # Cap to exact quantity to prevent over-production
                            if prod['completedQuantity'] >= prod['totalQuantity']:
                                prod['completedQuantity'] = prod['totalQuantity']
                                prod['completed'] = True
                                # Clear remaining hours since we've completed the planned quantity
                                prod['remainingHours'] = {}
                            elif len(prod['remainingHours']) == 0:
                                prod['completed'] = True
                            day_schedule['product_quantities'][prod_id] = day_schedule['product_quantities'].get(prod_id, 0) + units_completed
                        
                        if prod_id not in [p['product_id'] for p in day_schedule['products']]:
                            day_schedule['products'].append({
                                'product_id': prod_id,
                                'product_name': prod['name'],
                                'units': units_completed,
                                'machines': machines_to_update,  # Only active machines
                                'time_hours': chunk,
                                'chunk_hours': chunk,
                                'coordinated': True
                            })
                        else:
                            for p in day_schedule['products']:
                                if p['product_id'] == prod_id:
                                    p['time_hours'] += chunk
                                    p['chunk_hours'] = p.get('chunk_hours', 0) + chunk
                                    p['units'] = p.get('units', 0) + units_completed
                                    # Update machines list to include only active ones
                                    p['machines'] = list(set(p.get('machines', []) + machines_to_update))
                                    break
                        
                        # Track time slots for machines (for coordinated multi-op, all start at same time)
                        earliest_start = 0
                        for m in machines_to_update:
                            if day_schedule['machine_time_slots'][m]:
                                latest_end = max(slot[1] for slot in day_schedule['machine_time_slots'][m])
                                earliest_start = max(earliest_start, latest_end)
                        
                        start_time = earliest_start
                        end_time = start_time + chunk
                        
                        for m in machines_to_update:
                            day_schedule['machines_used'].add(m)
                            day_schedule['machine_tasks'][m].append({
                                'product': prod_id,
                                'hours': chunk,
                                'coordinated': True,
                                'start_time': start_time,
                                'end_time': end_time
                            })
                            day_schedule['machine_time_slots'][m].append([start_time, end_time])
                        
                        made_assignment = True
                        break  # Break idle-machine loop and reevaluate
            
            if made_assignment:
                continue
            
            # 3. Schedule single-op products for idle machines
            for machine in idle_machines:
                singles = get_unfinished_single_ops_for_machine(machine)
                
                if singles:
                    single_id = pick_by_priority(singles)
                    single = product_data[single_id]
                    
                    chunk = min(
                        machines[machine]['avail'],
                        single['remainingHours'].get(machine, 0)
                    )
                    
                    if chunk > EPS:
                        machines[machine]['avail'] -= chunk
                        machines[machine]['tasksToday'].append({
                            'product': single_id,
                            'hours': chunk,
                            'coordinated': False
                        })
                        single['remainingHours'][machine] -= chunk
                        if single['remainingHours'][machine] <= EPS:
                            del single['remainingHours'][machine]
                        
                        if len(single['remainingHours']) == 0:
                            single['completed'] = True
                        
                        # Calculate units completed from chunk hours
                        units_completed = int(chunk / single['cycleTimeHours']) if single.get('cycleTimeHours', 0) > 0 else 0
                        
                        # CRITICAL FIX: Cap units to not exceed planned quantity
                        remaining_quantity = single['totalQuantity'] - single['completedQuantity']
                        if remaining_quantity <= 0:
                            # Already completed all planned units, skip this product
                            continue
                        units_completed = min(units_completed, remaining_quantity)
                        
                        if units_completed > 0:
                            single['completedQuantity'] += units_completed
                            # Cap to exact quantity to prevent over-production
                            if single['completedQuantity'] >= single['totalQuantity']:
                                single['completedQuantity'] = single['totalQuantity']
                                single['completed'] = True
                                # Clear remaining hours since we've completed the planned quantity
                                single['remainingHours'] = {}
                            day_schedule['product_quantities'][single_id] = day_schedule['product_quantities'].get(single_id, 0) + units_completed
                        
                        if single_id not in [p['product_id'] for p in day_schedule['products']]:
                            day_schedule['products'].append({
                                'product_id': single_id,
                                'product_name': single['name'],
                                'units': units_completed,
                                'machines': [machine],
                                'time_hours': chunk,
                                'chunk_hours': chunk,
                                'coordinated': False
                            })
                        else:
                            for p in day_schedule['products']:
                                if p['product_id'] == single_id:
                                    p['time_hours'] += chunk
                                    p['chunk_hours'] = p.get('chunk_hours', 0) + chunk
                                    p['units'] = p.get('units', 0) + units_completed
                                    break
                        
                        # For single-op, find when this machine is available
                        start_time = 0
                        if day_schedule['machine_time_slots'][machine]:
                            start_time = max(slot[1] for slot in day_schedule['machine_time_slots'][machine])
                        end_time = start_time + chunk
                        
                        day_schedule['machines_used'].add(machine)
                        day_schedule['machine_tasks'][machine].append({
                            'product': single_id,
                            'hours': chunk,
                            'coordinated': False,
                            'start_time': start_time,
                            'end_time': end_time
                        })
                        day_schedule['machine_time_slots'][machine].append([start_time, end_time])
                        
                        made_assignment = True
        
        # Calculate total hours for the day based on actual time slots
        if day_schedule['machine_time_slots']:
            max_end_time = 0
            for machine, time_slots in day_schedule['machine_time_slots'].items():
                if time_slots:
                    # Find the latest end time for this machine
                    machine_end = max(slot[1] for slot in time_slots)
                    max_end_time = max(max_end_time, machine_end)
            day_schedule['total_hours'] = max_end_time
            
            # Update machine_end_times based on actual time slots (not sum of tasks)
            for machine, time_slots in day_schedule['machine_time_slots'].items():
                if time_slots:
                    # Get the latest end time for this machine
                    machine_end = max(slot[1] for slot in time_slots)
                    day_schedule['machine_end_times'][machine] = machine_end * 3600  # Convert to seconds
                else:
                    day_schedule['machine_end_times'][machine] = 0
        
        daily_schedules.append(day_schedule)
        
        # Check if all products are completed
        if all(prod['completed'] for prod in product_data.values()):
            break
    
    # Calculate remaining quantities (approximate from remaining hours)
    remaining_quantities = {}
    for product_id, prod in product_data.items():
        if not prod['completed'] and prod['remainingHours']:
            # Approximate remaining quantity from remaining hours
            # This is approximate since we track hours, not units
            max_remaining = max(prod['remainingHours'].values())
            if max_remaining > EPS:
                # Find original product to get cycle time
                original_product = next((p for p in products if p['id'] == product_id), None)
                if original_product:
                    max_cycle_hours = max(op['cycleTimeSeconds'] for op in original_product['operations']) / 3600
                    remaining_quantities[product_id] = int(max_remaining / max_cycle_hours) + 1
    
    return daily_schedules, remaining_quantities, product_data

# Main App

st.title("⚙️ Manufacturing Run Time Planner")

# Tabs
tab1, tab2 = st.tabs(["1. Product Setup", "2. Monthly Planning & Results"])

# Tab 1: Product Setup
with tab1:
    st.header("Define Products and Operations")
    
    # Add new product form (moved to top)
    st.subheader("Add New Product")
    with st.form("new_product_form"):
        product_name = st.text_input("Product Name", key="form_product_name")
        product_id = st.text_input("Product ID (e.g., POC-S03C)", key="form_product_id")
        
        st.write("**Operations:**")
        
        num_ops = st.number_input("Number of Operations", min_value=1, max_value=20, value=1, key="form_num_ops")
        
        # Create input fields for operations
        cols = st.columns(2)
        for i in range(num_ops):
            with cols[0] if i % 2 == 0 else cols[1]:
                st.text_input(f"Machine ID {i+1}", key=f"form_machine_{i}")
                st.number_input(f"Cycle Time (seconds) {i+1}", min_value=0.1, value=0.1, key=f"form_time_{i}")
        
        submitted = st.form_submit_button("💾 Save Product")
        if submitted:
            # Get form values
            product_name = st.session_state.get("form_product_name", "").strip()
            product_id = st.session_state.get("form_product_id", "").strip()
            num_ops = int(st.session_state.get("form_num_ops", 1))
            
            # Collect operations from form inputs
            operations = []
            for i in range(num_ops):
                machine_key = f"form_machine_{i}"
                time_key = f"form_time_{i}"
                machine = st.session_state.get(machine_key, "").strip()
                cycle_time = st.session_state.get(time_key, 0)
                
                if machine and cycle_time and cycle_time > 0:
                    operations.append({
                        "machine": machine,
                        "cycleTimeSeconds": float(cycle_time)
                    })
            
            if product_name and product_id and len(operations) > 0:
                # Check if ID already exists
                if any(p['id'] == product_id for p in st.session_state.products):
                    st.error(f"Product ID '{product_id}' already exists!")
                else:
                    new_product = {
                        "id": product_id,
                        "name": product_name,
                        "operations": operations
                    }
                    st.session_state.products.append(new_product)
                    st.success(f"Product '{product_name}' saved successfully!")
                    st.rerun()
            else:
                st.error("Please fill in all fields and add at least one valid operation (Machine ID and Cycle Time > 0).")
    
    st.divider()
    
    # Display current products in table format with selection
    st.subheader("Current Products")
    if len(st.session_state.products) == 0:
        st.info("No products defined yet. Use the form above to add products.")
    else:
        # Search box for products
        search_term = st.text_input(
            "🔍 Search Products",
            value=st.session_state.product_search,
            key="product_search_input",
            placeholder="Search by Product ID or Name..."
        )
        if search_term != st.session_state.product_search:
            st.session_state.product_search = search_term
        
        # Filter products based on search
        filtered_products = st.session_state.products
        if st.session_state.product_search:
            search_lower = st.session_state.product_search.lower()
            filtered_products = [
                p for p in st.session_state.products 
                if search_lower in p['id'].lower() or search_lower in p['name'].lower()
            ]
        
        if not filtered_products:
            st.warning(f"No products found matching '{st.session_state.product_search}'")
            st.session_state.selected_products = []
        else:
            # Selection and action buttons row
            col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
            with col1:
                # Check if all filtered products are selected
                filtered_product_ids = [p['id'] for p in filtered_products]
                all_selected = (
                    len(filtered_product_ids) > 0 and 
                    all(pid in st.session_state.selected_products for pid in filtered_product_ids)
                )
                select_all = st.checkbox("Select All", value=all_selected, key="select_all_products")
                if select_all and not all_selected:
                    # Add all filtered products to selection
                    for pid in filtered_product_ids:
                        if pid not in st.session_state.selected_products:
                            st.session_state.selected_products.append(pid)
                    st.rerun()
                elif not select_all and all_selected:
                    # Remove all filtered products from selection
                    st.session_state.selected_products = [
                        pid for pid in st.session_state.selected_products 
                        if pid not in filtered_product_ids
                    ]
                    st.rerun()
            
            with col2:
                if st.button("🗑️ Delete Selected", type="primary", **get_button_width_param()):
                    if st.session_state.selected_products:
                        num_deleted = len(st.session_state.selected_products)
                        # Remove selected products
                        st.session_state.products = [
                            p for p in st.session_state.products 
                            if p['id'] not in st.session_state.selected_products
                        ]
                        # Remove from plan quantities
                        for prod_id in st.session_state.selected_products:
                            if prod_id in st.session_state.plan_quantities:
                                del st.session_state.plan_quantities[prod_id]
                        st.session_state.selected_products = []
                        st.session_state.editing_product_id = None
                        st.success(f"Deleted {num_deleted} product(s)!")
                        st.rerun()
                    else:
                        st.warning("Please select at least one product to delete.")
            
            with col3:
                if st.button("✏️ Edit Selected", **get_button_width_param()):
                    if len(st.session_state.selected_products) == 1:
                        st.session_state.editing_product_id = st.session_state.selected_products[0]
                        st.rerun()
                    elif len(st.session_state.selected_products) > 1:
                        st.warning("Please select only one product to edit.")
                    else:
                        st.warning("Please select a product to edit.")
            
            with col4:
                if st.button("🔄 Clear Selection", **get_button_width_param()):
                    st.session_state.selected_products = []
                    st.session_state.editing_product_id = None
                    st.rerun()
            
            st.divider()
            
            # Products table with checkboxes (only show filtered products)
            for idx, product in enumerate(filtered_products):
                is_selected = product['id'] in st.session_state.selected_products
                is_editing = st.session_state.editing_product_id == product['id']
                
                # Create columns for checkbox, product info, and actions
                col_check, col_info, col_actions = st.columns([0.5, 5, 1])
                
                with col_check:
                    checkbox_key = f"select_{product['id']}"
                    checked = st.checkbox(
                        f"Select {product['id']}", 
                        value=is_selected, 
                        key=checkbox_key, 
                        label_visibility="collapsed"
                    )
                    if checked and product['id'] not in st.session_state.selected_products:
                        st.session_state.selected_products.append(product['id'])
                    elif not checked and product['id'] in st.session_state.selected_products:
                        st.session_state.selected_products.remove(product['id'])
                
                with col_info:
                    if is_editing:
                        st.markdown(f"**✏️ Editing: {product['id']}**")
                    else:
                        st.markdown(f"**{product['id']}** - {product['name']}")
                    
                    # Format operations
                    operations_str = " | ".join([
                            f"{op['machine']} ({op['cycleTimeSeconds']}s)" 
                            for op in product['operations']
                        ])
                    st.caption(operations_str)
                
                with col_actions:
                    if not is_editing:
                        if st.button("✏️ Edit", key=f"edit_{product['id']}", **get_button_width_param()):
                            st.session_state.editing_product_id = product['id']
                            st.rerun()
                    else:
                        if st.button("❌ Cancel", key=f"cancel_{product['id']}", **get_button_width_param()):
                            st.session_state.editing_product_id = None
                            st.rerun()
            
            # Edit form (shown when editing)
            if st.session_state.editing_product_id:
                product_to_edit = next((p for p in st.session_state.products if p['id'] == st.session_state.editing_product_id), None)
                if product_to_edit:
                    st.divider()
                    st.subheader(f"✏️ Edit Product: {product_to_edit['id']}")
                    with st.form("edit_product_form"):
                        edit_product_name = st.text_input("Product Name", value=product_to_edit['name'], key="edit_product_name")
                        edit_product_id = st.text_input("Product ID", value=product_to_edit['id'], key="edit_product_id", disabled=True)
                        
                        st.write("**Operations:**")
                        edit_num_ops = st.number_input(
                            "Number of Operations", 
                            min_value=1, 
                            max_value=20, 
                            value=len(product_to_edit['operations']), 
                            key="edit_num_ops"
                        )
                        
                        # Pre-populate existing operations
                        cols = st.columns(2)
                        edit_operations = []
                        for i in range(edit_num_ops):
                            with cols[0] if i % 2 == 0 else cols[1]:
                                existing_op = product_to_edit['operations'][i] if i < len(product_to_edit['operations']) else None
                                machine = st.text_input(
                                    f"Machine ID {i+1}", 
                                    value=existing_op['machine'] if existing_op else "",
                                    key=f"edit_machine_{i}"
                                )
                                cycle_time = st.number_input(
                                    f"Cycle Time (seconds) {i+1}", 
                                    min_value=0.1, 
                                    value=existing_op['cycleTimeSeconds'] if existing_op else 0.1,
                                    key=f"edit_time_{i}"
                                )
                        
                        edit_submitted = st.form_submit_button("💾 Save Changes")
                        if edit_submitted:
                            # Collect edited operations
                            edit_operations = []
                            for i in range(edit_num_ops):
                                machine_key = f"edit_machine_{i}"
                                time_key = f"edit_time_{i}"
                                machine = st.session_state.get(machine_key, "").strip()
                                cycle_time = st.session_state.get(time_key, 0)
                                
                                if machine and cycle_time and cycle_time > 0:
                                    edit_operations.append({
                                        "machine": machine,
                                        "cycleTimeSeconds": float(cycle_time)
                                    })
                            
                            if edit_product_name and len(edit_operations) > 0:
                                # Update product
                                product_to_edit['name'] = edit_product_name.strip()
                                product_to_edit['operations'] = edit_operations
                                st.session_state.editing_product_id = None
                                st.success(f"Product '{edit_product_name}' updated successfully!")
                                st.rerun()
                            else:
                                st.error("Please fill in all fields and add at least one valid operation.")

# Tab 2: Monthly Planning & Results
with tab2:
    st.header("Set Production Plan (Units)")
    st.caption("Enter the required units for each product this period. The required machine run time will be calculated automatically.")
    
    # File Upload Section (Excel or CSV)
    st.subheader("📤 Upload File (Excel or CSV)")
    
    # Show persistent message if data was imported (even after rerun)
    if 'plan_quantities' in st.session_state and sum(st.session_state.plan_quantities.values()) > 0:
        total_qty = sum(st.session_state.plan_quantities.values())
        product_count = len([q for q in st.session_state.plan_quantities.values() if q > 0])
        st.success(f"✅ **Data loaded**: {product_count} products with {total_qty:,} total units in plan. You can generate the schedule now!")
    
    uploaded_file = st.file_uploader(
        "Upload Excel or CSV file with Product IDs and Quantities",
        type=['xlsx', 'xls', 'csv'],
        help="Upload Excel (.xlsx, .xls) or CSV file with Product ID/JS Code and Quantity columns. Data persists after import - you don't need to re-upload."
    )
    
    if uploaded_file is not None:
        try:
            # Determine file type and read accordingly
            file_extension = uploaded_file.name.split('.')[-1].lower()
            
            if file_extension == 'csv':
                # Reset file pointer
                uploaded_file.seek(0)
                
                # Read first few rows without headers to find the actual header row
                try:
                    df_temp = pd.read_csv(uploaded_file, encoding='utf-8', header=None, nrows=10)
                except UnicodeDecodeError:
                    uploaded_file.seek(0)
                    df_temp = pd.read_csv(uploaded_file, encoding='latin-1', header=None, nrows=10)
                
                # Find the header row (look for "JS Code" or column names in any row)
                header_row = 0
                for i in range(len(df_temp)):
                    row_values = [str(x).lower() for x in df_temp.iloc[i].values if pd.notna(x) and str(x).strip() != '']
                    row_str = ' '.join(row_values)
                    # Check if this row contains typical header keywords
                    if any(keyword in row_str for keyword in ['js code', 'jscode', 'si no', 'station', 'customer', 'part no', 'total schedule']):
                        header_row = int(i)  # Ensure it's an integer
                        break
                
                # Ensure header_row is definitely an integer
                header_row = int(header_row)
                
                # Read CSV with proper header row
                uploaded_file.seek(0)
                
                try:
                    # If header_row is 0, use header=0 directly (no skiprows needed)
                    if header_row == 0:
                        df = pd.read_csv(uploaded_file, encoding='utf-8', header=0)
                    else:
                        # Skip rows before header, then use the first row after skipping as header
                        # skiprows with a list skips those specific row indices
                        rows_to_skip = list(range(header_row))
                        df = pd.read_csv(uploaded_file, encoding='utf-8', skiprows=rows_to_skip, header=0)
                except (UnicodeDecodeError, ValueError, TypeError) as csv_error:
                    uploaded_file.seek(0)
                    try:
                        if header_row == 0:
                            df = pd.read_csv(uploaded_file, encoding='latin-1', header=0)
                        else:
                            rows_to_skip = list(range(int(header_row)))
                            df = pd.read_csv(uploaded_file, encoding='latin-1', skiprows=rows_to_skip, header=0)
                    except Exception as e2:
                        # Last resort: try reading without any header detection
                        uploaded_file.seek(0)
                        try:
                            df = pd.read_csv(uploaded_file, encoding='utf-8')
                        except:
                            uploaded_file.seek(0)
                            df = pd.read_csv(uploaded_file, encoding='latin-1')
                        # If we get here, warn user about header detection
                        st.warning("⚠️ Could not auto-detect header row. Using first row as header.")
                
                # Clean column names (remove extra spaces, newlines, and handle trailing text)
                df.columns = df.columns.str.strip()
                
                # Remove rows where all values are NaN (empty rows)
                df = df.dropna(how='all')
                
                # Reset index after cleaning
                df = df.reset_index(drop=True)
            else:
                # Read Excel file
                # Convert uploaded file to BytesIO for pandas
                import io
                
                # Reset file pointer first
                uploaded_file.seek(0)
                file_bytes = uploaded_file.read()
                file_buffer = io.BytesIO(file_bytes)
                
                # Determine engine based on file extension
                if file_extension == 'xlsx':
                    engine = 'openpyxl'
                elif file_extension == 'xls':
                    engine = 'xlrd'
                else:
                    engine = 'openpyxl'  # Default
                
                # Read Excel with explicit sheet_name=0 (first sheet) and engine
                # Use integer 0 explicitly, not a variable that might be string
                df = None
                last_error = None
                
                # Method 1: Try with specified engine and integer sheet_name
                try:
                    file_buffer.seek(0)
                    df = pd.read_excel(file_buffer, sheet_name=0, engine=engine)
                except Exception as e1:
                    last_error = e1
                    # Method 2: Try without engine (let pandas auto-detect)
                    try:
                        file_buffer.seek(0)
                        df = pd.read_excel(file_buffer, sheet_name=0)
                    except Exception as e2:
                        last_error = e2
                        # Method 3: Try with alternative engine
                        try:
                            file_buffer.seek(0)
                            alt_engine = 'xlrd' if engine == 'openpyxl' else 'openpyxl'
                            df = pd.read_excel(file_buffer, sheet_name=0, engine=alt_engine)
                        except Exception as e3:
                            last_error = e3
                            # Method 4: Try reading first sheet by name (using string sheet name)
                            try:
                                # Create a fresh BytesIO for ExcelFile
                                file_buffer2 = io.BytesIO(file_bytes)
                                xl_file = pd.ExcelFile(file_buffer2, engine=engine)
                                if len(xl_file.sheet_names) > 0:
                                    first_sheet_name = str(xl_file.sheet_names[0])  # Ensure it's a string
                                    file_buffer.seek(0)
                                    df = pd.read_excel(file_buffer, sheet_name=first_sheet_name, engine=engine)
                                else:
                                    raise Exception("No sheets found in Excel file")
                                xl_file.close()
                            except Exception as e4:
                                last_error = e4
                                # Method 5: Last resort - try with None (read all sheets, take first)
                                try:
                                    file_buffer.seek(0)
                                    all_sheets = pd.read_excel(file_buffer, sheet_name=None, engine=engine)
                                    if all_sheets and len(all_sheets) > 0:
                                        # Get the first sheet from the dictionary
                                        first_sheet_key = list(all_sheets.keys())[0]
                                        df = all_sheets[first_sheet_key]
                                    else:
                                        raise Exception("No data found in Excel file")
                                except Exception as e5:
                                    last_error = e5
                                    raise Exception(f"Failed to read Excel file after trying all methods. Last error: {str(last_error)}")
                
                if df is None or df.empty:
                    raise Exception("Excel file appears to be empty or could not be read")
                
                # Reset file pointer for potential reuse
                uploaded_file.seek(0)
            
            st.success("✅ File uploaded successfully!")
            
            # Display preview
            st.write("**File Preview:**")
            st.dataframe(df.head(10), **get_dataframe_width_param())
            
            # Auto-detect columns for JSA template format
            js_code_col = None
            quantity_col = None
            
            # Look for JS Code column (case-insensitive, handle variations)
            for col in df.columns:
                col_lower = str(col).lower().strip()
                # Check for JS Code variations
                if any(keyword in col_lower for keyword in ['js code', 'jscode', 'js_code', 'product id', 'productid', 'product_id']):
                    js_code_col = col
                    break
            
            # Look for quantity column (prioritize "Total Schedule")
            quantity_candidates = []
            for col in df.columns:
                col_lower = str(col).lower().strip()
                if 'total schedule' in col_lower:
                    quantity_col = col  # Highest priority
                    break
                elif any(keyword in col_lower for keyword in ['schedule', 'quantity', 'units', 'qty', 'total']):
                    quantity_candidates.append(col)
            
            # If no "Total Schedule" found, use first candidate
            if not quantity_col and quantity_candidates:
                quantity_col = quantity_candidates[0]
            
            # Column mapping
            st.write("**Column Mapping:**")
            col1, col2 = st.columns(2)
            
            with col1:
                # Auto-select JS Code column if found
                default_js_index = 0
                if js_code_col and js_code_col in df.columns.tolist():
                    default_js_index = df.columns.tolist().index(js_code_col)
                
                product_id_col = st.selectbox(
                    "Select Product ID / JS Code Column",
                    options=df.columns.tolist(),
                    index=default_js_index,
                    help="Select the column containing Product IDs or JS Codes (e.g., POC S03C, ACC S01A)"
                )
            
            with col2:
                # Auto-select quantity column if found
                default_qty_index = min(1, len(df.columns) - 1) if len(df.columns) > 1 else 0
                if quantity_col and quantity_col in df.columns.tolist():
                    default_qty_index = df.columns.tolist().index(quantity_col)
                
                quantity_col = st.selectbox(
                    "Select Quantity/Units Column",
                    options=df.columns.tolist(),
                    index=default_qty_index,
                    help="Select the column containing quantities/units (e.g., Total Schedule given by sales)"
                )
            
            if st.button("📥 Import Data from File"):
                imported_count = 0
                not_found = []
                errors = []
                
                # Get all product IDs (case-insensitive matching)
                product_ids = {p['id']: p['id'] for p in st.session_state.products}
                product_ids_lower = {p['id'].lower(): p['id'] for p in st.session_state.products}
                
                # Process each row
                for idx, row in df.iterrows():
                    try:
                        # Try to get product ID (handle different formats)
                        prod_id_raw = str(row[product_id_col]).strip()
                        if pd.isna(prod_id_raw) or prod_id_raw == 'nan' or prod_id_raw == '':
                            continue
                        
                        # Normalize product ID: handle spaces vs dashes (e.g., "POC S03C" -> "POC-S03C")
                        prod_id_normalized = prod_id_raw.replace(' ', '-').upper()
                        prod_id_normalized_lower = prod_id_normalized.lower()
                        
                        # Try multiple matching strategies
                        prod_id = None
                        
                        # 1. Try exact match
                        if prod_id_raw in product_ids:
                            prod_id = product_ids[prod_id_raw]
                        # 2. Try normalized (space to dash)
                        elif prod_id_normalized in product_ids:
                            prod_id = product_ids[prod_id_normalized]
                        # 3. Try case-insensitive exact
                        elif prod_id_raw.lower() in product_ids_lower:
                            prod_id = product_ids_lower[prod_id_raw.lower()]
                        # 4. Try case-insensitive normalized
                        elif prod_id_normalized_lower in product_ids_lower:
                            prod_id = product_ids_lower[prod_id_normalized_lower]
                        # 5. Try partial match (for cases like "POC S03C" matching "POC-S03C")
                        else:
                            # Try matching by removing spaces/dashes and comparing
                            for sys_prod_id in product_ids.keys():
                                sys_normalized = sys_prod_id.replace('-', '').replace(' ', '').upper()
                                input_normalized = prod_id_raw.replace('-', '').replace(' ', '').upper()
                                if sys_normalized == input_normalized:
                                    prod_id = sys_prod_id
                                    break
                        
                        if not prod_id:
                            not_found.append(prod_id_raw)
                            continue
                        
                        # Get quantity - handle comma-separated numbers
                        qty_str = str(row[quantity_col]).strip()
                        # Remove commas and spaces
                        qty_str = qty_str.replace(',', '').replace(' ', '')
                        quantity = pd.to_numeric(qty_str, errors='coerce')
                        
                        if pd.isna(quantity) or quantity < 0:
                            errors.append(f"Row {idx+2}: Invalid quantity for {prod_id_raw} (value: {row[quantity_col]})")
                            continue
                        
                        quantity = int(quantity)
                        
                        # If product already has a quantity, add to it (for duplicate JS Codes)
                        if prod_id in st.session_state.plan_quantities:
                            st.session_state.plan_quantities[prod_id] += quantity
                        else:
                            st.session_state.plan_quantities[prod_id] = quantity
                        imported_count += 1
                    except Exception as e:
                        errors.append(f"Row {idx+2}: {str(e)}")
                        continue
                
                # Show results
                if imported_count > 0:
                    total_qty = sum(st.session_state.plan_quantities.values())
                    product_count = len([q for q in st.session_state.plan_quantities.values() if q > 0])
                    st.success(f"✅ Successfully imported {imported_count} product quantities!")
                    st.info(f"📊 Total quantities now in plan: {total_qty:,} units across {product_count} products")
                    # Reset schedule generation flag so user needs to regenerate after import
                    st.session_state.schedule_generated = False
                    st.session_state.generate_schedule_requested = False  # Also reset this flag
                    if 'schedule' in st.session_state:
                        del st.session_state.schedule
                        del st.session_state.schedule_product_data
                        del st.session_state.schedule_remaining
                    # Store import timestamp to show persistent message
                    st.session_state.data_imported = True
                    # Rerun to refresh UI and show updated quantities in manual entry and machine calculations
                    # This is safe because we've already updated session state
                    st.rerun()
                if not_found:
                    unique_not_found = list(set(not_found))[:20]
                    st.warning(f"⚠️ {len(not_found)} product ID(s) not found in system: {', '.join(unique_not_found)}")
                    if len(not_found) > 20:
                        st.caption(f"... and {len(not_found) - 20} more")
                if errors:
                    st.error(f"❌ {len(errors)} error(s) occurred:")
                    for err in errors[:10]:
                        st.text(err)
                    if len(errors) > 10:
                        st.caption(f"... and {len(errors) - 10} more errors")
        
        except Exception as e:
            error_msg = str(e)
            # Determine if it's CSV or Excel based on file extension
            if uploaded_file is not None:
                file_ext = uploaded_file.name.split('.')[-1].lower() if '.' in uploaded_file.name else 'unknown'
                if file_ext == 'csv':
                    st.error(f"Error reading CSV file: {error_msg}")
                    st.info("Please ensure the file is a valid CSV file with proper encoding (UTF-8 or Latin-1)")
                else:
                    st.error(f"Error reading Excel file: {error_msg}")
                    st.info("Please ensure the file is a valid Excel file (.xlsx or .xls)")
            else:
                st.error(f"Error reading file: {error_msg}")
            
            # Show detailed error for debugging
            with st.expander("🔍 Error Details"):
                st.code(f"Error: {error_msg}\nFile: {uploaded_file.name if uploaded_file else 'Unknown'}")
                import traceback
                st.code(traceback.format_exc())
    
    st.divider()
    
    # Manual Input Section - Collapsible Accordion
    with st.expander("✏️ Manual Entry", expanded=False):
        if len(st.session_state.products) == 0:
            st.warning("⚠️ No products defined. Please add products in the 'Product Setup' tab first.")
        else:
            # Create input grid
            st.write("Enter quantities for each product:")
            
            # Search/filter functionality
            search_term = st.text_input("🔍 Search products by ID or name:", "")
            
            # Filter products based on search
            products = st.session_state.products
            if search_term:
                search_lower = search_term.lower()
                products = [p for p in products if search_lower in p['id'].lower() or search_lower in p['name'].lower()]
            
            if not products:
                st.info("No products match your search.")
            else:
                # Create columns for better layout
                cols_per_row = 3
                
                for i in range(0, len(products), cols_per_row):
                    cols = st.columns(cols_per_row)
                    for j, col in enumerate(cols):
                        if i + j < len(products):
                            product = products[i + j]
                            current_qty = st.session_state.plan_quantities.get(product['id'], 0)
                            with col:
                                qty = st.number_input(
                                    f"{product['id']}",
                                    min_value=0,
                                    value=int(current_qty),
                                    key=f"qty_{product['id']}",
                                    help=product['name']
                                )
                                st.session_state.plan_quantities[product['id']] = qty
    
    st.divider()
    
    # Calculate and Display Results
    st.subheader("📊 Calculated Machine Run Time Requirements")
    
    # Debug: Show plan quantities count (can be removed later)
    total_quantities = sum(st.session_state.plan_quantities.values())
    if total_quantities > 0:
        st.info(f"ℹ️ Total quantities in plan: {total_quantities} units across {len([q for q in st.session_state.plan_quantities.values() if q > 0])} products")
    
    machine_times = calculate_machine_time(st.session_state.products, st.session_state.plan_quantities)
    machine_breakdown = calculate_machine_breakdown(st.session_state.products, st.session_state.plan_quantities)
    
    if len(machine_times) == 0:
        st.info("No machine run time calculated. Enter quantities in the plan above.")
    else:
        # Create table with unique machine IDs and product breakdown in one column
        table_rows = []
        machine_hours_map = {}  # Store hours for styling
        
        for machine_id in sorted(machine_times.keys()):
            total_seconds = machine_times[machine_id]
            total_hours = total_seconds / 3600
            total_minutes = total_seconds / 60
            
            # Store hours for styling
            machine_hours_map[machine_id] = total_hours
            
            if machine_id in machine_breakdown:
                breakdown = machine_breakdown[machine_id]
                num_parts = len(breakdown)
                
                # Format products as "X parts - part 1: ProductID, X hours, part 2: ProductID, Y hours"
                product_parts = []
                for idx, item in enumerate(breakdown, 1):
                    product_parts.append(f"part {idx}: {item['product_id']}, {item['total_time_hours']:.2f} hours")
                
                products_text = f"{num_parts} parts - {', '.join(product_parts)}"
                
                table_rows.append({
                    "Machine ID": machine_id,
                    "Total Time (Hours)": f"{total_hours:.2f}",
                    "Total Time (Minutes)": f"{int(total_minutes):,}",
                    "Total Time (Seconds)": f"{int(total_seconds):,}",
                    "Products": products_text
                })
            else:
                # Machine with no products (shouldn't happen, but handle it)
                table_rows.append({
                    "Machine ID": machine_id,
                    "Total Time (Hours)": f"{total_hours:.2f}",
                    "Total Time (Minutes)": f"{int(total_minutes):,}",
                    "Total Time (Seconds)": f"{int(total_seconds):,}",
                    "Products": "No products"
                })
        
        # Display as single table with conditional row coloring
        if table_rows:
            results_df = pd.DataFrame(table_rows)
            
            # Add row styling based on total hours
            def style_rows(row):
                machine_id = row['Machine ID']
                total_hours = machine_hours_map.get(machine_id, 0)
                
                # Apply background color and text color based on hours
                if total_hours > 720:
                    return ['background-color: #ffcccc; color: #000000'] * len(row)  # Red background, black text
                elif total_hours > 400:
                    return ['background-color: #fff4cc; color: #000000'] * len(row)  # Yellow background, black text
                else:
                    return [''] * len(row)  # Default (no color)
            
            # Apply styling
            styled_df = results_df.style.apply(style_rows, axis=1)
            
            st.dataframe(
                styled_df,
                hide_index=True,
                **get_dataframe_width_param()
            )
            
            # Add legend
            st.caption("⚠️ Yellow: > 400 hours | 🔴 Red: > 720 hours")
        
        st.divider()
        
        # Create summary DataFrame for download (machine totals only)
        summary_data = []
        for machine_id, total_seconds in sorted(machine_times.items()):
            total_hours = total_seconds / 3600
            total_minutes = total_seconds / 60
            summary_data.append({
                "Machine ID": machine_id,
                "Total Time (Seconds)": int(total_seconds),
                "Total Time (Minutes)": f"{int(total_minutes):,}",
                "Total Time (Hours)": f"{total_hours:.2f}"
            })
        summary_df = pd.DataFrame(summary_data)
        
        # Download buttons
        col1, col2 = st.columns(2)
        
        with col1:
            # Download summary (machine totals only)
            summary_csv = summary_df.to_csv(index=False)
            st.download_button(
                label="📥 Download Summary (Machine Totals)",
                data=summary_csv,
                file_name="machine_run_times_summary.csv",
                mime="text/csv",
                **get_download_button_width_param()
            )
        
        with col2:
            # Download detailed table (with product breakdown)
            detailed_csv = results_df.to_csv(index=False)
            st.download_button(
                label="📥 Download Detailed Table (All Data)",
                data=detailed_csv,
                file_name="machine_run_times_detailed.csv",
                mime="text/csv",
                **get_download_button_width_param()
            )
    
    # Manufacturing Schedule Section
    st.divider()
    st.subheader("📅 Daily Manufacturing Schedule")
    st.caption("Schedule showing which products to manufacture each day. All operations of a product run simultaneously.")
    
    # Check plan quantities
    total_qty = sum(st.session_state.plan_quantities.values())
    product_count = len([q for q in st.session_state.plan_quantities.values() if q > 0])
    
    if total_qty == 0:
        st.info("Enter production quantities in the plan above to generate a schedule.")
        if 'data_imported' in st.session_state and st.session_state.data_imported:
            st.warning("⚠️ Data was imported but quantities appear empty. Please check your file format or try importing again.")
    else:
        st.success(f"✅ Ready to generate schedule: {product_count} products with {total_qty:,} total units loaded.")
        # Schedule configuration
        col1, col2, col3 = st.columns(3)
        with col1:
            hours_per_day = st.number_input(
                "Working Hours per Day",
                min_value=1.0,
                max_value=24.0,
                value=24.0,
                step=0.5,
                help="Number of working hours available per day"
            )
        
        with col2:
            max_days = st.number_input(
                "Scheduling Horizon (Days)",
                min_value=1,
                max_value=90,
                value=30,
                step=1,
                help="Number of days to schedule ahead"
            )
        
        with col3:
            max_chunk = st.number_input(
                "Max Chunk Size (Hours)",
                min_value=0.0,
                max_value=24.0,
                value=0.0,
                step=0.5,
                help="Maximum chunk size per product per day (0 = no limit). Use 8 or 16 to allow more products per day."
            )
            if max_chunk == 0:
                max_chunk = None
        
        generate_clicked = st.button("🔄 Generate Schedule", type="primary", **get_button_width_param())
        
        # Set flag when button is clicked (persists across reruns)
        if generate_clicked:
            st.session_state.generate_schedule_requested = True
        
        # Check if we have a cached schedule in session state
        has_cached_schedule = (
            'schedule' in st.session_state and 
            st.session_state.schedule and 
            len(st.session_state.schedule) > 0
        )
        
        # Generate schedule if button was clicked (via flag) or if we have cached schedule
        should_generate = st.session_state.generate_schedule_requested or has_cached_schedule
        
        if should_generate:
            # Check if this is a new generation request (not just displaying cached)
            is_new_generation = st.session_state.generate_schedule_requested
            
            if is_new_generation:
                st.session_state.schedule_generated = True
                st.session_state.generate_schedule_requested = False  # Reset flag after processing
                # Clear old schedule when regenerating
                if 'schedule' in st.session_state:
                    del st.session_state.schedule
                    del st.session_state.schedule_product_data
                    del st.session_state.schedule_remaining
            
            # Verify we have plan quantities before generating
            total_qty = sum(st.session_state.plan_quantities.values())
            if len(st.session_state.plan_quantities) == 0 or total_qty == 0:
                st.error("❌ No production quantities found. Please enter quantities in the plan above first.")
                st.session_state.schedule_generated = False
            else:
                # Generate schedule if this is a new request or if we don't have a cached schedule
                if is_new_generation or not has_cached_schedule:
                    try:
                        # Validate inputs before scheduling
                        if not st.session_state.products:
                            st.error("❌ No products defined. Please add products in the Product Setup tab.")
                            st.session_state.schedule_generated = False
                        elif not st.session_state.plan_quantities:
                            st.error("❌ No production quantities found. Please enter quantities in the plan above first.")
                            st.session_state.schedule_generated = False
                        else:
                            with st.spinner("🔄 Generating schedule... This may take a moment."):
                                schedule, remaining, product_data = create_manufacturing_schedule(
                                    st.session_state.products,
                                    st.session_state.plan_quantities,
                                    hours_per_day,
                                    max_days=max_days,
                                    max_chunk=max_chunk
                                )
                            
                            # Store in session state for persistence
                            st.session_state.schedule = schedule
                            st.session_state.schedule_product_data = product_data
                            st.session_state.schedule_remaining = remaining
                            
                            # Check for remaining quantities
                            remaining_items = {pid: qty for pid, qty in remaining.items() if qty > 0}
                            if remaining_items:
                                st.warning(f"⚠️ Could not schedule all products. Remaining quantities: {remaining_items}")
                            
                    except Exception as e:
                        st.error(f"❌ Error generating schedule: {str(e)}")
                        import traceback
                        with st.expander("🔍 Error Details"):
                            st.code(traceback.format_exc())
                        st.session_state.schedule_generated = False
                        st.session_state.schedule = None
                
                # Display schedule if available (either newly generated or cached)
                schedule = st.session_state.get('schedule')
                product_data = st.session_state.get('schedule_product_data', {})
                
                if schedule and len(schedule) > 0:
                    # Get all machines that should be used (from the plan)
                    all_machines_in_plan = set()
                    for product in st.session_state.products:
                        if st.session_state.plan_quantities.get(product['id'], 0) > 0:
                            for op in product['operations']:
                                all_machines_in_plan.add(op['machine'])
                    all_machines_in_plan = sorted(all_machines_in_plan)
                    
                    # Display schedule summary
                    st.success(f"✅ Schedule generated for {len(schedule)} days")
                    
                    # Summary metrics
                    total_days = len(schedule)
                    total_products_scheduled = sum(len(day['products']) for day in schedule)
                    avg_utilization = sum(day['total_hours'] for day in schedule) / (total_days * hours_per_day) * 100 if total_days > 0 else 0
                    
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Total Days", total_days)
                    with col2:
                        st.metric("Total Product Runs", total_products_scheduled)
                    with col3:
                        st.metric("Avg Daily Utilization", f"{avg_utilization:.1f}%")
                    with col4:
                        st.metric("Total Machines in Plan", len(all_machines_in_plan))
                    
                    st.divider()
                    
                    # Daily schedule table
                    schedule_data = []
                    for day_schedule in schedule:
                        # Format products and machines for this day
                        products_list = []
                        machines_list = []
                        
                        for prod in day_schedule['products']:
                            products_list.append(f"{prod['product_id']} ({prod['units']} units)")
                            machines_list.extend(prod['machines'])
                        
                        # Remove duplicates from machines
                        machines_used = sorted(set(machines_list))
                        
                        # Find idle machines (machines in plan but not used this day)
                        idle_machines = sorted(set(all_machines_in_plan) - set(machines_used))
                        
                        schedule_data.append({
                            "Day": day_schedule['day'],
                            "Products": ", ".join(products_list) if products_list else "None",
                            "Machines Used": ", ".join(machines_used) if machines_used else "None",
                            "Idle Machines": ", ".join(idle_machines) if idle_machines else "None",
                            "Machines Used / Total": f"{len(machines_used)} / {len(all_machines_in_plan)}",
                            "Total Hours": f"{day_schedule['total_hours']:.2f}",
                            "Utilization %": f"{(day_schedule['total_hours'] / hours_per_day * 100):.1f}%"
                        })
                    
                    schedule_df = pd.DataFrame(schedule_data)
                    st.dataframe(
                        schedule_df,
                        hide_index=True,
                        **get_dataframe_width_param()
                    )
                    
                    # Calculate total products in plan
                    total_products_in_plan = len([p for p in st.session_state.products 
                                                if st.session_state.plan_quantities.get(p['id'], 0) > 0])
                    
                    # Track completion status day by day
                    # We need to simulate day-by-day to track when products complete
                    daily_completion_tracker = {}  # day -> {completed: [], remaining: []}
                    products_completed_so_far = set()
                    
                    # Build a map of when products complete by checking remainingHours after each day
                    for day_idx, day_schedule in enumerate(schedule):
                        day_num = day_schedule['day']
                        # Products worked on this day
                        products_worked_today = set(p['product_id'] for p in day_schedule['products'])
                        
                        # Check which products completed (we'll check at end, but track incrementally)
                        # For now, we'll calculate at display time based on current state
                        daily_completion_tracker[day_num] = {
                            'products_worked': products_worked_today,
                            'completed': set(),
                            'remaining': set()
                        }
                    
                    # Detailed daily view
                    st.subheader("📋 Detailed Daily Schedule")
                    cumulative_completed = 0
                    
                    for day_idx, day_schedule in enumerate(schedule):
                        day_num = day_schedule['day']
                        
                        # Calculate completed products up to this day
                        # We need to check product_data state, but since it's final state,
                        # we'll show current completion status
                        completed_products = [pid for pid, prod in product_data.items() 
                                             if prod.get('completed', False)]
                        cumulative_completed = len(completed_products)
                        remaining_products = total_products_in_plan - cumulative_completed
                        
                        with st.expander(f"📅 Day {day_schedule['day']} - {day_schedule['total_hours']:.2f} hours ({len(day_schedule['products'])} products)"):
                                # Products for this day (sorted by chunk hours)
                                sorted_products = sorted(
                                    day_schedule['products'],
                                    key=lambda x: x.get('chunk_hours', x.get('time_hours', 0)),
                                    reverse=True
                                )
                                
                                for prod in sorted_products:
                                    chunk_hours = prod.get('chunk_hours', prod.get('time_hours', 0))
                                    is_coordinated = prod.get('coordinated', True)
                                    
                                    st.write(f"**{prod['product_id']}** - {chunk_hours:.2f} hours")
                                    st.write(f"  - **Type:** {'Multi-op (Coordinated)' if is_coordinated else 'Single-op'}")
                                    st.write(f"  - **Machines (all run simultaneously):** {', '.join(prod['machines'])}")
                                    st.write(f"  - **Total Time:** {prod.get('time_hours', chunk_hours):.2f} hours")
                                    st.write("---")
                                
                                # Machine utilization - show ALL machines in plan
                                st.write("**Machine Utilization (All Machines in Plan):**")
                                machine_util = {}
                                
                                # Get all machines from the plan
                                all_machines = set()
                                for product in st.session_state.products:
                                    if st.session_state.plan_quantities.get(product['id'], 0) > 0:
                                        for op in product['operations']:
                                            all_machines.add(op['machine'])
                                
                                for machine in sorted(all_machines):
                                    if machine in day_schedule['machine_end_times']:
                                        # Machine was used
                                        time_used = day_schedule['machine_end_times'].get(machine, 0) / 3600
                                        util_pct = (time_used / hours_per_day) * 100
                                        machine_util[machine] = {
                                            'hours': time_used,
                                            'utilization': util_pct,
                                            'status': 'Used'
                                        }
                                    else:
                                        # Machine is idle
                                        machine_util[machine] = {
                                            'hours': 0.0,
                                            'utilization': 0.0,
                                            'status': 'Idle'
                                        }
                                
                                util_df = pd.DataFrame([
                                    {
                                        "Machine": machine,
                                        "Status": data['status'],
                                        "Hours Used": f"{data['hours']:.2f}",
                                        "Utilization": f"{data['utilization']:.1f}%"
                                    }
                                    for machine, data in sorted(machine_util.items())
                                ])
                                st.dataframe(util_df, hide_index=True, **get_dataframe_width_param())
                                
                                # Show idle machines warning if any
                                idle_count = sum(1 for data in machine_util.values() if data['status'] == 'Idle')
                                if idle_count > 0:
                                    idle_machines_list = [machine for machine, data in machine_util.items() if data['status'] == 'Idle']
                                    st.warning(f"⚠️ {idle_count} machine(s) idle this day: {', '.join(idle_machines_list)}")
                                
                                st.divider()
                                
                                # Daily Progress Summary
                                st.subheader("📊 Daily Progress Summary")
                                
                                # Calculate products completed today (newly completed)
                                # A product is completed today if:
                                # 1. It was worked on today
                                # 2. It's marked as completed in product_data
                                # 3. It has no remaining hours
                                products_completed_today = []
                                products_worked_today = [p['product_id'] for p in day_schedule['products']]
                                
                                for pid in products_worked_today:
                                    prod = product_data.get(pid)
                                    if prod and prod.get('completed', False):
                                        if len(prod.get('remainingHours', {})) == 0:
                                            products_completed_today.append(pid)
                                
                                # Also check if any product that was worked on earlier days completed today
                                # by checking if it has remaining hours that are now 0
                                for pid, prod in product_data.items():
                                    if pid not in products_worked_today:  # Not worked on today
                                        continue
                                    if prod.get('completed', False) and pid not in products_completed_today:
                                        # Check if it just completed (has no remaining hours)
                                        if len(prod.get('remainingHours', {})) == 0:
                                            products_completed_today.append(pid)
                                
                                # Calculate progress percentage
                                progress_pct = (cumulative_completed / total_products_in_plan * 100) if total_products_in_plan > 0 else 0
                                
                                col1, col2, col3, col4 = st.columns(4)
                                with col1:
                                    st.metric(
                                        "Products Completed",
                                        f"{cumulative_completed}",
                                        delta=f"+{len(products_completed_today)} today" if products_completed_today else None
                                    )
                                with col2:
                                    st.metric(
                                        "Products Remaining",
                                        f"{remaining_products}",
                                        delta=f"-{len(products_completed_today)}" if products_completed_today else None,
                                        delta_color="inverse"
                                    )
                                with col3:
                                    st.metric(
                                        "Total Products in Plan",
                                        f"{total_products_in_plan}",
                                    )
                                with col4:
                                    st.metric(
                                        "Progress",
                                        f"{progress_pct:.1f}%",
                                    )
                                
                                # Progress bar
                                st.progress(progress_pct / 100)
                                
                                # Show completed products today if any
                                if products_completed_today:
                                    st.success(f"✅ {len(products_completed_today)} product(s) completed today: {', '.join(products_completed_today)}")
                                
                                # Show remaining products if any
                                if remaining_products > 0:
                                    remaining_product_ids = [pid for pid, prod in product_data.items() 
                                                            if not prod.get('completed', False) 
                                                            and st.session_state.plan_quantities.get(pid, 0) > 0]
                                    if remaining_product_ids:
                                        st.info(f"📋 {remaining_products} product(s) remaining: {', '.join(remaining_product_ids[:10])}" + 
                                               (f" and {len(remaining_product_ids) - 10} more..." if len(remaining_product_ids) > 10 else ""))
                                
                                st.divider()
                                
                                # Product Progress Table - Cumulative by Day
                                st.subheader("📈 Product Progress (Cumulative)")
                                st.caption(f"Showing progress for all products up to Day {day_num}")
                                
                                # Build cumulative progress data
                                progress_data = []
                                cumulative_quantities = {}  # Track cumulative quantities up to this day
                                
                                # Sum up quantities from all previous days up to current day
                                for prev_day_idx in range(day_idx + 1):
                                    prev_day = schedule[prev_day_idx]
                                    for pid, qty in prev_day.get('product_quantities', {}).items():
                                        cumulative_quantities[pid] = cumulative_quantities.get(pid, 0) + qty
                                
                                # Get all products in plan
                                for product in st.session_state.products:
                                    pid = product['id']
                                    total_qty = st.session_state.plan_quantities.get(pid, 0)
                                    if total_qty > 0:
                                        completed_qty = cumulative_quantities.get(pid, 0)
                                        # Also check product_data for final completed quantity
                                        if pid in product_data:
                                            completed_qty = product_data[pid].get('completedQuantity', completed_qty)
                                        
                                        progress_pct = (completed_qty / total_qty * 100) if total_qty > 0 else 0
                                        status = "✅ Complete" if completed_qty >= total_qty else "🔄 In Progress"
                                        
                                        progress_data.append({
                                            "Product ID": pid,
                                            "Product Name": product.get('name', pid),
                                            "Completed": completed_qty,
                                            "Total": total_qty,
                                            "Progress": f"{completed_qty}/{total_qty}",
                                            "Progress %": f"{progress_pct:.1f}%",
                                            "Status": status
                                        })
                                
                                if progress_data:
                                    progress_df = pd.DataFrame(progress_data)
                                    # Sort by progress percentage (lowest first to see what needs attention)
                                    progress_df = progress_df.sort_values(by=['Progress %', 'Product ID'])
                                    st.dataframe(
                                        progress_df,
                                        hide_index=True,
                                        **get_dataframe_width_param()
                                    )
                                    
                                    # Summary metrics
                                    total_units_planned = sum(p['Total'] for p in progress_data)
                                    total_units_completed = sum(p['Completed'] for p in progress_data)
                                    overall_progress = (total_units_completed / total_units_planned * 100) if total_units_planned > 0 else 0
                                    
                                    col1, col2, col3 = st.columns(3)
                                    with col1:
                                        st.metric("Total Units Completed", f"{total_units_completed:,}")
                                    with col2:
                                        st.metric("Total Units Planned", f"{total_units_planned:,}")
                                    with col3:
                                        st.metric("Overall Progress", f"{overall_progress:.1f}%")
                    
                    # Download schedule
                    st.divider()
                    schedule_csv = schedule_df.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Schedule as CSV",
                        data=schedule_csv,
                        file_name="manufacturing_schedule.csv",
                        mime="text/csv",
                        **get_download_button_width_param()
                    )
                else:
                    st.warning("Could not generate schedule. Please check your production plan.")


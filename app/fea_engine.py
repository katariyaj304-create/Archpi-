"""
ArchPi FEA Engine — Deterministic 2D Matrix Stiffness Solver
Zero AI inference. Pure mathematics. K·u = f

This engine computes:
  1. Global stiffness matrix assembly
  2. Force vector construction (gravity, wind, seismic)
  3. Displacement solution via Gaussian elimination
  4. Stress extraction per beam element
  5. Safety factor computation

All calculations are deterministic float64 — no randomness, no ML.
"""

import sys
import json
import math

# ═══════════════════════════════════════════════════════════
# MATERIAL DATABASE (Steel Grades — ASTM Standard)
# ═══════════════════════════════════════════════════════════
MATERIALS = {
    "A36": {
        "name": "ASTM A36 Structural Steel",
        "E": 200e9,        # Young's Modulus (Pa) — 200 GPa
        "fy": 250e6,       # Yield Strength (Pa) — 250 MPa
        "density": 7850,   # kg/m³
        "poisson": 0.3
    },
    "A992": {
        "name": "ASTM A992 (W-shapes)",
        "E": 200e9,
        "fy": 345e6,
        "density": 7850,
        "poisson": 0.3
    },
    "A500B": {
        "name": "ASTM A500 Grade B (HSS)",
        "E": 200e9,
        "fy": 290e6,
        "density": 7850,
        "poisson": 0.3
    }
}

# ═══════════════════════════════════════════════════════════
# CROSS-SECTION DATABASE
# ═══════════════════════════════════════════════════════════
SECTIONS = {
    "W14x30": {"A": 57.03e-4, "I": 291e-8, "desc": "W14x30 Column"},
    "W14x48": {"A": 91.03e-4, "I": 484e-8, "desc": "W14x48 Heavy Column"},
    "W10x22": {"A": 41.81e-4, "I": 118e-8, "desc": "W10x22 Beam"},
    "W12x26": {"A": 49.03e-4, "I": 204e-8, "desc": "W12x26 Beam"},
    "W16x36": {"A": 68.39e-4, "I": 448e-8, "desc": "W16x36 Girder"},
}


def create_default_structure(num_cols=6, num_floors=5, bay_width=6.0, floor_height=3.5):
    """
    Generate a 2D building frame: columns + beams + supports.
    Returns nodes, elements, supports, and metadata.
    """
    nodes = []
    elements = []
    supports = []

    node_id = 0
    node_map = {}  # (col, floor) -> node_id

    # Create nodes at grid intersections
    for floor in range(num_floors + 1):  # 0 = ground
        for col in range(num_cols):
            x = col * bay_width
            y = floor * floor_height
            nodes.append({
                "id": node_id,
                "x": x,
                "y": y,
                "floor": floor,
                "col": col,
                "label": f"N{node_id}"
            })
            node_map[(col, floor)] = node_id
            node_id += 1

    elem_id = 0

    # Create column elements (vertical)
    for col in range(num_cols):
        for floor in range(num_floors):
            n1 = node_map[(col, floor)]
            n2 = node_map[(col, floor + 1)]
            # Ground-floor columns are heavier
            section = "W14x48" if floor == 0 else "W14x30"
            elements.append({
                "id": elem_id,
                "type": "column",
                "node_i": n1,
                "node_j": n2,
                "section": section,
                "material": "A992",
                "label": f"COL-{col}-F{floor}"
            })
            elem_id += 1

    # Create beam elements (horizontal)
    for floor in range(1, num_floors + 1):
        for col in range(num_cols - 1):
            n1 = node_map[(col, floor)]
            n2 = node_map[(col + 1, floor)]
            section = "W16x36" if floor <= 2 else "W10x22"
            elements.append({
                "id": elem_id,
                "type": "beam",
                "node_i": n1,
                "node_j": n2,
                "section": section,
                "material": "A36",
                "label": f"BM-F{floor}-{col}"
            })
            elem_id += 1

    # Create supports (pinned at ground level)
    for col in range(num_cols):
        ground_node = node_map[(col, 0)]
        supports.append({
            "node": ground_node,
            "dx": True,  # restrained in x
            "dy": True,  # restrained in y
            "rz": False  # free rotation (pin)
        })

    return {
        "nodes": nodes,
        "elements": elements,
        "supports": supports,
        "num_cols": num_cols,
        "num_floors": num_floors,
        "bay_width": bay_width,
        "floor_height": floor_height
    }


def element_length(n1, n2):
    """Euclidean distance between two nodes."""
    dx = n2["x"] - n1["x"]
    dy = n2["y"] - n1["y"]
    return math.sqrt(dx * dx + dy * dy)


def element_angle(n1, n2):
    """Angle of element from horizontal (radians)."""
    dx = n2["x"] - n1["x"]
    dy = n2["y"] - n1["y"]
    return math.atan2(dy, dx)


def local_stiffness_2d_frame(E, A, I, L):
    """
    2D frame element local stiffness matrix (6x6).
    DOFs per node: [u, v, theta] (axial, transverse, rotation)
    
    K_local = standard 2D frame element stiffness matrix
    """
    k = [[0.0] * 6 for _ in range(6)]

    EA_L = E * A / L
    EI_L3 = E * I / (L * L * L)
    EI_L2 = E * I / (L * L)
    EI_L = E * I / L

    # Axial stiffness
    k[0][0] = EA_L;  k[0][3] = -EA_L
    k[3][0] = -EA_L; k[3][3] = EA_L

    # Flexural stiffness
    k[1][1] = 12 * EI_L3;   k[1][2] = 6 * EI_L2
    k[1][4] = -12 * EI_L3;  k[1][5] = 6 * EI_L2

    k[2][1] = 6 * EI_L2;    k[2][2] = 4 * EI_L
    k[2][4] = -6 * EI_L2;   k[2][5] = 2 * EI_L

    k[4][1] = -12 * EI_L3;  k[4][2] = -6 * EI_L2
    k[4][4] = 12 * EI_L3;   k[4][5] = -6 * EI_L2

    k[5][1] = 6 * EI_L2;    k[5][2] = 2 * EI_L
    k[5][4] = -6 * EI_L2;   k[5][5] = 4 * EI_L

    return k


def transformation_matrix(theta):
    """
    2D rotation/transformation matrix (6x6) for frame element.
    Transforms from local to global coordinates.
    """
    c = math.cos(theta)
    s = math.sin(theta)

    T = [[0.0] * 6 for _ in range(6)]
    T[0][0] = c;  T[0][1] = s
    T[1][0] = -s; T[1][1] = c
    T[2][2] = 1.0
    T[3][3] = c;  T[3][4] = s
    T[4][3] = -s; T[4][4] = c
    T[5][5] = 1.0

    return T


def transpose(matrix):
    """Transpose a 2D matrix."""
    rows = len(matrix)
    cols = len(matrix[0])
    result = [[0.0] * rows for _ in range(cols)]
    for i in range(rows):
        for j in range(cols):
            result[j][i] = matrix[i][j]
    return result


def mat_mult(A, B):
    """Multiply two matrices."""
    rows_a = len(A)
    cols_a = len(A[0])
    cols_b = len(B[0])
    result = [[0.0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += A[i][k] * B[k][j]
    return result


def assemble_global_stiffness(structure):
    """
    Assemble the global stiffness matrix K from all elements.
    Each node has 3 DOFs: [u, v, theta]
    Total DOFs = num_nodes * 3
    """
    nodes = structure["nodes"]
    elements = structure["elements"]
    n_dof = len(nodes) * 3

    K = [[0.0] * n_dof for _ in range(n_dof)]

    for elem in elements:
        ni = elem["node_i"]
        nj = elem["node_j"]
        n1 = nodes[ni]
        n2 = nodes[nj]

        mat = MATERIALS[elem["material"]]
        sec = SECTIONS[elem["section"]]

        E = mat["E"]
        A = sec["A"]
        I = sec["I"]
        L = element_length(n1, n2)
        theta = element_angle(n1, n2)

        # Local stiffness
        k_local = local_stiffness_2d_frame(E, A, I, L)

        # Transformation
        T = transformation_matrix(theta)
        Tt = transpose(T)

        # Global element stiffness: K_global = T^T * K_local * T
        temp = mat_mult(k_local, T)
        k_global = mat_mult(Tt, temp)

        # DOF mapping
        dofs = [ni * 3, ni * 3 + 1, ni * 3 + 2, nj * 3, nj * 3 + 1, nj * 3 + 2]

        # Assemble into global K
        for i in range(6):
            for j in range(6):
                K[dofs[i]][dofs[j]] += k_global[i][j]

    return K


def build_force_vector(structure, load_config):
    """
    Build the global force vector f.
    Supports: gravity, wind, storm, seismic, flood, lightning,
              fire, tornado, blizzard, landslide, volcanic.
    """
    nodes = structure["nodes"]
    elements = structure["elements"]
    n_dof = len(nodes) * 3
    f = [0.0] * n_dof

    load_type = load_config.get("type", "gravity")
    magnitude = load_config.get("magnitude", 1.0)
    num_floors = structure["num_floors"]
    floor_height = structure["floor_height"]
    total_height = num_floors * floor_height

    # ── Gravity / Storm (vertical floor loads) ──────────────────
    if load_type in ("gravity", "storm", "blizzard", "flood", "volcanic"):
        for elem in elements:
            if elem["type"] == "beam":
                ni = elem["node_i"]
                nj = elem["node_j"]
                n1 = nodes[ni]
                n2 = nodes[nj]
                L = element_length(n1, n2)
                mat = MATERIALS[elem["material"]]
                sec = SECTIONS[elem["section"]]
                base_w = mat["density"] * sec["A"] * 9.81 + 10000.0
                extra = 0.0
                if load_type == "blizzard":
                    extra = 1500.0 * magnitude       # snow load N/m
                elif load_type == "volcanic":
                    extra = 3000.0 * magnitude       # ash load N/m
                elif load_type == "flood":
                    extra = 1200.0 * magnitude       # hydrostatic head
                w = base_w + extra
                f[ni * 3 + 1] -= w * L / 2
                f[nj * 3 + 1] -= w * L / 2

    # ── Wind / Storm / Tornado lateral ──────────────────────────
    if load_type in ("storm", "wind", "tornado", "blizzard"):
        if load_type == "tornado":
            wind_pressure = 4500.0 * magnitude   # EF-scale amplified
        elif load_type == "blizzard":
            wind_pressure = 1200.0 * magnitude
        else:
            wind_pressure = 2000.0 * magnitude
        for node in nodes:
            if node["floor"] > 0:
                height_factor = node["y"] / total_height if total_height > 0 else 0
                wind_force = wind_pressure * height_factor * height_factor * 3.0
                f[node["id"] * 3] += wind_force
                # Tornado: also adds rotational component (perpendicular)
                if load_type == "tornado":
                    f[node["id"] * 3 + 1] -= wind_force * 0.4 * height_factor

    # ── Seismic ─────────────────────────────────────────────────
    if load_type == "seismic":
        base_shear = 500000.0 * magnitude
        total_wh = 0.0
        node_weights = {}
        for node in nodes:
            if node["floor"] > 0:
                w = 50000.0
                total_wh += w * node["y"]
                node_weights[node["id"]] = w * node["y"]
        if total_wh > 0:
            for node in nodes:
                if node["id"] in node_weights:
                    fx = base_shear * node_weights[node["id"]] / total_wh
                    f[node["id"] * 3] += fx

    # ── Flood: hydrostatic lateral pressure on base floors ──────
    if load_type == "flood":
        water_depth = 3.5 * magnitude           # m of water
        rho_g = 9810.0                           # water unit weight N/m³
        for node in nodes:
            node_height = node["y"]              # y is elevation in metres
            if node_height <= water_depth and node["floor"] > 0:
                hydro_p = rho_g * (water_depth - node_height) * 1.5 * magnitude
                f[node["id"] * 3] += hydro_p
                f[node["id"] * 3 + 1] -= hydro_p * 0.2   # uplift component

    # ── Lightning: concentrated apex impulse ────────────────────
    if load_type == "lightning":
        energy_mj = 5.0 * magnitude             # MJ
        impulse_force = energy_mj * 1e6 / 0.001 # force over 1ms
        apex_nodes = [n for n in nodes if n["floor"] == num_floors]
        if apex_nodes:
            per_node = impulse_force / len(apex_nodes)
            for node in apex_nodes:
                f[node["id"] * 3] += per_node * 0.3
                f[node["id"] * 3 + 1] -= per_node         # downward impulse

    # ── Fire: thermal expansion load (equivalent lateral) ───────
    if load_type == "fire":
        # Thermal gradient causes differential expansion → lateral force
        temperature = 800.0 * magnitude         # °C
        alpha_steel = 1.2e-5                    # thermal expansion /°C
        E_steel = 200e9
        for elem in elements:
            if elem["type"] == "column":
                ni = elem["node_i"]
                nj = elem["node_j"]
                n1 = nodes[ni]
                floor_frac = n1["floor"] / num_floors if num_floors > 0 else 0
                # Fire primarily affects lower floors
                thermal_factor = max(0, 1.0 - floor_frac) * magnitude
                sec = SECTIONS[elem["section"]]
                # Thermal axial force: F = E*A*alpha*deltaT
                F_thermal = E_steel * sec["A"] * alpha_steel * temperature * thermal_factor * 0.001
                f[ni * 3 + 1] -= F_thermal
                f[nj * 3 + 1] -= F_thermal
                # Lateral bow from differential heating
                f[ni * 3] += F_thermal * 0.3 * thermal_factor
                f[nj * 3] += F_thermal * 0.3 * thermal_factor

    # ── Landslide: asymmetric horizontal base push ───────────────
    if load_type == "landslide":
        soil_pressure = 80000.0 * magnitude      # N/m²
        for node in nodes:
            if node["floor"] <= 2:               # base slab affected
                depth_factor = max(0, (2 - node["floor"]) / 2.0)
                lateral_force = soil_pressure * depth_factor * 2.0
                f[node["id"] * 3] += lateral_force
                # Differential settlement: vertical uplift/depression
                if node["col"] < 2:
                    f[node["id"] * 3 + 1] -= lateral_force * 0.5
                elif node["col"] > 3:
                    f[node["id"] * 3 + 1] += lateral_force * 0.4

    # ── Volcanic: ash load + shockwave ──────────────────────────
    if load_type == "volcanic":
        # Shockwave: blast pressure decays with height
        blast_psi = 12.0 * magnitude
        blast_pa = blast_psi * 6894.76          # psi to Pa
        for node in nodes:
            if node["floor"] > 0:
                height_factor = 1.0 - (node["y"] / total_height) * 0.5
                f[node["id"] * 3] += blast_pa * height_factor * 0.5
                f[node["id"] * 3 + 1] -= blast_pa * height_factor * 0.15

    return f



def apply_boundary_conditions(K, f, supports):
    """
    Apply support conditions by zeroing rows/columns of fixed DOFs.
    Uses the penalty method to maintain matrix size.
    """
    n = len(f)
    K_mod = [row[:] for row in K]
    f_mod = f[:]

    penalty = 1e20  # Large number for penalty method

    for sup in supports:
        nid = sup["node"]
        if sup["dx"]:
            dof = nid * 3
            K_mod[dof][dof] += penalty
            f_mod[dof] = 0.0
        if sup["dy"]:
            dof = nid * 3 + 1
            K_mod[dof][dof] += penalty
            f_mod[dof] = 0.0
        if sup.get("rz", False):
            dof = nid * 3 + 2
            K_mod[dof][dof] += penalty
            f_mod[dof] = 0.0

    return K_mod, f_mod


def solve_system(K, f):
    """
    Solve K·u = f using Gaussian elimination with partial pivoting.
    Returns displacement vector u.
    Pure deterministic — no iterative methods, no randomness.
    """
    n = len(f)
    # Augmented matrix [K | f]
    aug = [K[i][:] + [f[i]] for i in range(n)]

    # Forward elimination with partial pivoting
    for col in range(n):
        # Find pivot
        max_val = abs(aug[col][col])
        max_row = col
        for row in range(col + 1, n):
            if abs(aug[row][col]) > max_val:
                max_val = abs(aug[row][col])
                max_row = row

        if max_val < 1e-30:
            continue  # Skip singular pivot

        # Swap rows
        if max_row != col:
            aug[col], aug[max_row] = aug[max_row], aug[col]

        # Eliminate below
        pivot = aug[col][col]
        for row in range(col + 1, n):
            if abs(aug[row][col]) < 1e-30:
                continue
            factor = aug[row][col] / pivot
            for j in range(col, n + 1):
                aug[row][j] -= factor * aug[col][j]

    # Back substitution
    u = [0.0] * n
    for i in range(n - 1, -1, -1):
        if abs(aug[i][i]) < 1e-30:
            u[i] = 0.0
            continue
        u[i] = aug[i][n]
        for j in range(i + 1, n):
            u[i] -= aug[i][j] * u[j]
        u[i] /= aug[i][i]

    return u


def compute_element_stresses(structure, displacements):
    """
    Compute stress in each element from the displacement solution.
    Returns stress ratio (stress / yield) for each element.
    """
    nodes = structure["nodes"]
    elements = structure["elements"]
    results = []

    for elem in elements:
        ni = elem["node_i"]
        nj = elem["node_j"]
        n1 = nodes[ni]
        n2 = nodes[nj]

        mat = MATERIALS[elem["material"]]
        sec = SECTIONS[elem["section"]]
        E = mat["E"]
        A = sec["A"]
        I = sec["I"]
        fy = mat["fy"]
        L = element_length(n1, n2)
        theta = element_angle(n1, n2)

        # Extract element displacements
        dofs = [ni * 3, ni * 3 + 1, ni * 3 + 2, nj * 3, nj * 3 + 1, nj * 3 + 2]
        u_elem = [displacements[d] for d in dofs]

        # Transform to local coordinates
        T = transformation_matrix(theta)
        u_local = [0.0] * 6
        for i in range(6):
            for j in range(6):
                u_local[i] += T[i][j] * u_elem[j]

        # Axial stress: sigma_a = E * (u4 - u1) / L
        axial_strain = (u_local[3] - u_local[0]) / L
        axial_stress = E * abs(axial_strain)

        # Bending stress: sigma_b = E * c * kappa (approximate)
        # Using maximum curvature from beam deflections
        # kappa ≈ (6/L²)(v1 - v2) + (2/L)(2*theta1 + theta2)
        v1 = u_local[1]
        v2 = u_local[4]
        th1 = u_local[2]
        th2 = u_local[5]

        # Approximate max bending moment
        M_max = E * I * abs(6 * (v1 - v2) / (L * L) + 2 * (2 * th1 + th2) / L)

        # Bending stress (c ≈ sqrt(I/A) approximation for depth)
        c = math.sqrt(I / A) * 2  # approximate half-depth
        bending_stress = M_max * c / I if I > 0 else 0

        # Combined stress (von Mises simplified for uniaxial)
        total_stress = axial_stress + bending_stress

        # Stress ratio
        stress_ratio = total_stress / fy if fy > 0 else 0
        stress_ratio = min(stress_ratio, 2.0)  # Cap at 200%

        # Stress in MPa
        stress_mpa = total_stress / 1e6

        results.append({
            "id": elem["id"],
            "label": elem["label"],
            "type": elem["type"],
            "stress_mpa": round(stress_mpa, 3),
            "capacity_ratio": round(stress_ratio, 5),
            "axial_stress_mpa": round(axial_stress / 1e6, 3),
            "bending_stress_mpa": round(bending_stress / 1e6, 3),
            "yield_strength_mpa": round(fy / 1e6, 1),
            "status": "CRITICAL" if stress_ratio > 0.9 else "WARNING" if stress_ratio > 0.7 else "NOMINAL"
        })

    return results


def compute_node_displacements(structure, displacements):
    """Extract per-node displacement results."""
    nodes = structure["nodes"]
    results = []

    for node in nodes:
        nid = node["id"]
        dx = displacements[nid * 3]
        dy = displacements[nid * 3 + 1]
        rz = displacements[nid * 3 + 2]

        results.append({
            "id": nid,
            "x": node["x"],
            "y": node["y"],
            "dx": round(dx * 1000, 5),      # Convert to mm
            "dy": round(dy * 1000, 5),       # Convert to mm
            "rz": round(rz * 1000, 5),       # Convert to mrad
            "floor": node["floor"],
            "col": node["col"]
        })

    return results


def compute_global_metrics(element_stresses, node_disps, structure):
    """Compute summary telemetry metrics from the analysis."""
    max_stress_ratio = max(e["capacity_ratio"] for e in element_stresses)
    max_drift = max(abs(n["dx"]) for n in node_disps)
    max_deflection = max(abs(n["dy"]) for n in node_disps)

    # Lateral drift ratio (mm / floor height in mm)
    floor_height_mm = structure["floor_height"] * 1000
    drift_ratio = max_drift / floor_height_mm if floor_height_mm > 0 else 0

    # Approximate seismic oscillation (natural frequency)
    # T ≈ 0.1 * N (approximate for steel frames, N = number of floors)
    T_approx = 0.1 * structure["num_floors"]
    freq = 1.0 / T_approx if T_approx > 0 else 1.0

    # Core compression (approximate from maximum axial stress)
    max_axial = max(e["axial_stress_mpa"] for e in element_stresses)
    core_compression_gpa = max_axial / 1000.0

    return {
        "oscillation_hz": round(freq, 4),
        "lateral_drift_mm": round(max_drift, 3),
        "core_compression_gpa": round(core_compression_gpa, 4),
        "max_stress_ratio": round(max_stress_ratio, 5),
        "max_deflection_mm": round(max_deflection, 3),
        "drift_ratio": round(drift_ratio, 6),
        "critical_elements": sum(1 for e in element_stresses if e["status"] == "CRITICAL"),
        "warning_elements": sum(1 for e in element_stresses if e["status"] == "WARNING")
    }


def run_analysis(load_config):
    """
    Full FEA pipeline: Structure → K → f → u → stresses → metrics.
    Returns complete analysis results.
    """
    # 1. Create structure
    structure = create_default_structure()

    # 2. Assemble global stiffness
    K = assemble_global_stiffness(structure)

    # 3. Build force vector
    f = build_force_vector(structure, load_config)

    # 4. Apply boundary conditions
    K_mod, f_mod = apply_boundary_conditions(K, f, structure["supports"])

    # 5. Solve K·u = f (THE ABSOLUTE TRUTH)
    u = solve_system(K_mod, f_mod)

    # 6. Compute element stresses
    element_stresses = compute_element_stresses(structure, u)

    # 7. Compute node displacements
    node_disps = compute_node_displacements(structure, u)

    # 8. Compute global metrics
    metrics = compute_global_metrics(element_stresses, node_disps, structure)

    return {
        "status": "complete",
        "structure": {
            "nodes": structure["nodes"],
            "elements": [{"id": e["id"], "type": e["type"], "node_i": e["node_i"], "node_j": e["node_j"], "label": e["label"]} for e in structure["elements"]],
            "num_cols": structure["num_cols"],
            "num_floors": structure["num_floors"],
            "bay_width": structure["bay_width"],
            "floor_height": structure["floor_height"]
        },
        "element_stresses": element_stresses,
        "node_displacements": node_disps,
        "metrics": metrics,
        "load_config": load_config
    }


# ═══════════════════════════════════════════════════════════
# V&V: CANTILEVER BEAM VERIFICATION
# ═══════════════════════════════════════════════════════════
def verify_cantilever():
    """
    Verification test: Compare FEA result against analytical solution.
    Cantilever beam with point load P at free end.
    
    Theoretical deflection: δ = PL³ / (3EI)
    Theoretical rotation:   θ = PL² / (2EI)
    
    Must match to 5 decimal places or FAIL.
    """
    # Test parameters
    L = 3.0       # 3 meters
    P = 10000.0   # 10 kN
    E = 200e9     # 200 GPa (steel)
    A = 57.03e-4  # m² (W14x30)
    I = 291e-8    # m⁴ (W14x30)

    # Theoretical solutions
    delta_theory = P * L**3 / (3 * E * I)     # meters
    theta_theory = P * L**2 / (2 * E * I)     # radians

    # FEA solution (single element cantilever)
    # Node 0: fixed (x=0), Node 1: free (x=L), load P downward at node 1
    k_local = local_stiffness_2d_frame(E, A, I, L)

    # For horizontal beam, T = identity
    # Apply BC: fix DOFs 0,1,2 (node 0)
    # Free DOFs: 3,4,5 (node 1)
    # Load: f[4] = -P (downward)

    # Reduce to free DOFs
    K_ff = [[k_local[i][j] for j in [3, 4, 5]] for i in [3, 4, 5]]
    f_f = [0.0, -P, 0.0]

    # Solve 3x3 system
    u_f = solve_system(K_ff, f_f)

    delta_fea = abs(u_f[1])  # vertical displacement
    theta_fea = abs(u_f[2])  # rotation

    # Check to 5 decimal places
    delta_match = round(delta_theory, 5) == round(delta_fea, 5)
    theta_match = round(theta_theory, 5) == round(theta_fea, 5)

    return {
        "test": "Cantilever Beam V&V",
        "parameters": {"L": L, "P": P, "E": E, "A": A, "I": I},
        "theoretical": {
            "deflection_m": round(delta_theory, 8),
            "rotation_rad": round(theta_theory, 8)
        },
        "fea_computed": {
            "deflection_m": round(delta_fea, 8),
            "rotation_rad": round(theta_fea, 8)
        },
        "deflection_error": abs(delta_theory - delta_fea),
        "rotation_error": abs(theta_theory - theta_fea),
        "deflection_pass": delta_match,
        "rotation_pass": theta_match,
        "overall_pass": delta_match and theta_match,
        "tolerance": "5 decimal places"
    }


# ═══════════════════════════════════════════════════════════
# CLI INTERFACE
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python fea_engine.py '<json_config>'"}))
        sys.exit(1)

    try:
        config = json.loads(sys.argv[1])
        command = config.get("command", "analyze")

        if command == "verify":
            result = verify_cantilever()
        elif command == "analyze":
            load_config = {
                "type": config.get("type", "gravity"),
                "magnitude": config.get("magnitude", 1.0)
            }
            result = run_analysis(load_config)
        elif command == "structure":
            structure = create_default_structure()
            result = {
                "nodes": structure["nodes"],
                "elements": [{"id": e["id"], "type": e["type"], "node_i": e["node_i"], "node_j": e["node_j"], "label": e["label"]} for e in structure["elements"]],
                "supports": structure["supports"],
                "num_cols": structure["num_cols"],
                "num_floors": structure["num_floors"]
            }
        else:
            result = {"error": f"Unknown command: {command}"}

        print(json.dumps(result))

    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

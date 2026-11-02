# ULTRON PHYSICS CURRICULUM
## Complete Knowledge System for AI Reasoning

**Version:** 1.0  
**Purpose:** Machine-ingestible, comprehensive physics knowledge base  
**Prerequisite:** ULTRON_Mathematics_Full.md  
**Last Updated:** 2026-08-14

---

## LEARNING ARCHITECTURE

### Prerequisites
- Mathematics: Calculus, Linear Algebra, Vectors, Differential Equations
- Observations: Experiments, measurements, physical intuition

### Progression
```
Kinematics (motion description)
    ↓
Dynamics (forces, Newton's laws)
    ↓
Energy & Work (conservation laws)
    ↓
Circular Motion & Gravity
    ↓
Oscillations & Waves
    ↓
Thermodynamics (heat, entropy)
    ↓
Electromagnetism (fields, forces)
    ↓
Modern Physics (relativity, quantum)
```

### Mastery Model
- **Level 0 (Recognize):** Identify when physics applies
- **Level 1 (Explain):** Describe phenomenon intuitively and formally
- **Level 2 (Predict):** Use equations for standard scenarios
- **Level 3 (Solve):** Multi-step problems with strategy
- **Level 4 (Analyze):** Check limits; understand why formulas work
- **Level 5 (Connect):** Link to mathematics, other sciences
- **Level 6 (Generalize):** Extend to unfamiliar situations
- **Level 7 (Question):** Identify experiments to validate/refute

---

## MODULE 1: KINEMATICS

### CHAPTER 1.1: MOTION IN ONE DIMENSION

#### Concept 1.1.1: Position, Velocity, Acceleration

**Precise Definition:**

**Position:** x(t) = location at time t [units: meters (m)]

**Velocity:** v(t) = rate of change of position
- v(t) = dx/dt [units: m/s]
- Average velocity: v_avg = Δx/Δt

**Acceleration:** a(t) = rate of change of velocity
- a(t) = dv/dt = d²x/dt² [units: m/s²]

**Formal Relationships:**
```
x(t) = position
v(t) = dx/dt = velocity
a(t) = dv/dt = d²x/dt² = acceleration

Inverse relationships:
v(t) = ∫ a(t) dt + C
x(t) = ∫ v(t) dt + C'
```

**Geometric Meaning:**
- Slope of x-t graph = velocity
- Slope of v-t graph = acceleration
- Area under v-t graph = displacement
- Area under a-t graph = change in velocity

**Worked Examples:**

*Level 2: Constant Velocity*
Object moves with constant v = 5 m/s for 3 seconds, starting at x₀ = 0
- x(t) = x₀ + vt = 0 + 5t = 5t
- At t = 3s: x(3) = 15 m

*Level 3: Constant Acceleration (Kinematics Equations)*
Ball dropped from height h = 20 m. Find time to hit ground (g = 9.8 m/s²)
- Initial position: y₀ = 20 m
- Initial velocity: v₀ = 0
- Acceleration: a = −g = −9.8 m/s² (downward)
- Use: y(t) = y₀ + v₀t + ½at²
- 0 = 20 + 0 − ½(9.8)t²
- 4.9t² = 20
- t² = 20/4.9 ≈ 4.08
- t ≈ 2.02 seconds
- Velocity at impact: v = v₀ + at = 0 − 9.8(2.02) ≈ −19.8 m/s

**Kinematic Equations (constant acceleration):**
1. v = v₀ + at
2. x = x₀ + v₀t + ½at²
3. v² = v₀² + 2a(x − x₀)
4. x = x₀ + (v₀ + v)/2 · t

**Verification:**
These all satisfy v = dx/dt and a = d²x/dt², and reduce correctly when a=0.

**Common Misconception:**
MISCONCEPTION: "A car traveling at 60 mph has zero acceleration"
WHY: Velocity is constant, so "no change"
WRONG: Ignores direction; road curves change direction
CORRECT: If direction changes, there IS acceleration (even at constant speed)
Example: Car turning a corner at constant speed has centripetal acceleration

**ULTRON Knowledge Record:**
```
CONCEPT: Kinematics (1D)
POSITION: x(t) [meters]
VELOCITY: v(t) = dx/dt [m/s]
ACCELERATION: a(t) = d²x/dt² [m/s²]
CONSTANT_ACCELERATION_EQUATIONS:
  v = v₀ + at
  x = x₀ + v₀t + ½at²
  v² = v₀² + 2aΔx
GEOMETRIC_INTERPRETATION:
  x-t graph slope = velocity
  v-t graph slope = acceleration
  v-t graph area = displacement
VECTOR_NATURE: Displacement and velocity are vectors (with sign/direction)
APPLICATIONS: Projectile motion; collision analysis; vehicle dynamics
VERIFICATION: Check units (m, m/s, m/s²); test limiting cases (a→0)
```

---

### CHAPTER 1.2: TWO-DIMENSIONAL MOTION

#### Concept 1.2.1: Projectile Motion

**Problem Setup:**
Object launched with initial velocity v₀ at angle θ above horizontal
Acceleration: a = −g (gravity, vertically downward)
Find trajectory and range

**Solution Strategy:**
Decompose into x and y components:
- v₀ₓ = v₀ cos(θ) [horizontal component]
- v₀ᵧ = v₀ sin(θ) [vertical component]

**Equations of Motion:**

Horizontal (no acceleration):
- x(t) = v₀ₓ · t = v₀ cos(θ) · t
- vₓ(t) = v₀ₓ = constant

Vertical (constant acceleration −g):
- y(t) = v₀ᵧ · t − ½gt² = v₀ sin(θ) · t − ½gt²
- vᵧ(t) = v₀ᵧ − gt = v₀ sin(θ) − gt

**Key Results:**

**Time of Flight (when y = 0):**
0 = v₀ sin(θ) · t − ½gt²
t(v₀ sin(θ) − ½gt) = 0
t = 0 or t = 2v₀ sin(θ)/g

T_flight = 2v₀ sin(θ)/g

**Range (horizontal distance at landing):**
R = v₀ₓ · T_flight = v₀ cos(θ) · 2v₀ sin(θ)/g
R = 2v₀² sin(θ)cos(θ)/g = v₀² sin(2θ)/g

**Maximum Height:**
At peak, vᵧ = 0
0 = v₀ sin(θ) − gt_peak
t_peak = v₀ sin(θ)/g

h_max = v₀ sin(θ) · t_peak − ½g·t_peak²
      = v₀² sin²(θ)/g − ½g · (v₀ sin(θ)/g)²
      = v₀² sin²(θ)/(2g)

**Trajectory Equation:**
Eliminate t from x(t) and y(t):
t = x / (v₀ cos θ)
y = v₀ sin(θ) · [x/(v₀ cos θ)] − ½g · [x/(v₀ cos θ)]²
y = x tan(θ) − gx² / (2v₀² cos²θ)

This is a parabola.

**Worked Example:**

A cannon fires a projectile at 40 m/s at 30° angle. Find:
(a) Time of flight
(b) Range
(c) Maximum height

Given: v₀ = 40 m/s, θ = 30°, g = 9.8 m/s²

(a) T = 2v₀ sin(θ)/g = 2(40) sin(30°)/9.8 = 2(40)(0.5)/9.8 = 40/9.8 ≈ 4.08 s

(b) R = v₀² sin(2θ)/g = (40)² sin(60°)/9.8 = 1600(0.866)/9.8 ≈ 141.2 m

(c) h_max = v₀² sin²(θ)/(2g) = (40)² sin²(30°)/(2·9.8) = 1600(0.25)/19.6 ≈ 20.4 m

**Key Insight:** Maximum range occurs at θ = 45°, giving R_max = v₀²/g

**ULTRON Knowledge Record:**
```
CONCEPT: Projectile Motion
INITIAL_VELOCITY: v₀ at angle θ
COMPONENTS: v₀ₓ = v₀cos(θ), v₀ᵧ = v₀sin(θ)
EQUATIONS:
  x(t) = v₀cos(θ) · t
  y(t) = v₀sin(θ) · t − ½gt²
  Trajectory: y = x·tan(θ) − gx²/(2v₀²cos²θ)
KEY_RESULTS:
  Time_of_flight: T = 2v₀sin(θ)/g
  Range: R = v₀²sin(2θ)/g
  Max_height: h = v₀²sin²(θ)/(2g)
  Max_range_angle: θ = 45°
ASSUMPTIONS: Flat Earth; no air resistance; uniform g
APPLICATIONS: Artillery; sports physics; space missions
VERIFICATION: Check limits (θ→0 gives R→0; θ→90° gives R→0)
```

---

## MODULE 2: DYNAMICS

### CHAPTER 2.1: NEWTON'S LAWS

#### Concept 2.1.1: Newton's First Law (Inertia)

**Statement:**
An object at rest stays at rest, and an object in motion stays in constant velocity motion, unless acted upon by an unbalanced force.

**Formal Expression:**
If Σ F = 0, then a = 0 (constant velocity or at rest)

**Meaning:**
- Objects have **inertia** (resistance to acceleration)
- No force needed to maintain motion; force needed to *change* motion
- Velocity is natural state; acceleration requires cause

**Implication:**
The existence of inertia implies reference frames where Newton's laws hold.
These are called **inertial reference frames**.

**Misconception:**
MISCONCEPTION: "Moving objects naturally slow down; force keeps them moving"
WHY: Experience suggests moving objects stop (car coasting)
WRONG: Ignores friction/air resistance (unbalanced forces present)
CORRECT: Without friction, object would continue forever

**ULTRON Knowledge Record:**
```
CONCEPT: Newton's First Law
STATEMENT: Inertia; objects maintain state unless acted upon
MATHEMATICAL: ΣF = 0 ⟹ a = 0
INERTIAL_REFERENCE_FRAME: Frame where this law holds
IMPLICATION: Velocity is not caused; it's a natural state
APPLICATIONS: Seatbelts (inertia keeps you moving); space objects
VERIFICATION: No net force → constant velocity observed
```

---

#### Concept 2.1.2: Newton's Second Law

**Statement:**
The net force on an object equals its mass times acceleration.

**Formal Expression:**
Σ F = m · a

**SI Units:**
Force in Newtons [N] = kg·m/s²

**Dimensional Analysis:**
[F] = [m][a] = kg · (m/s²) = kg·m/s² = N ✓

**Key Interpretations:**

1. **Force as Cause of Acceleration:**
   a = Σ F / m
   Force → acceleration; mass → resistance to acceleration

2. **Momentum Form (more general):**
   Σ F = dp/dt, where p = mv = momentum
   
   If m is constant: F = d(mv)/dt = m · dv/dt = ma

3. **Vector Form (3D):**
   F⃗ = m · a⃗
   Each component: Fₓ = m · aₓ, Fᵧ = m · aᵧ, etc.

**Applications:**

*Free Body Diagram:*
Draw all forces acting on object; vector sum to get net force.

**Worked Example:**

*Level 3: Box on Inclined Plane*
Mass m = 10 kg on frictionless incline at angle θ = 30°
Find acceleration down the plane

Forces on box:
- Weight: W = mg = 10(9.8) = 98 N downward
- Normal force: N perpendicular to surface
- Component along plane: W sin(θ) = 98 sin(30°) = 98(0.5) = 49 N

Net force along plane: F_net = 49 N
Acceleration: a = F/m = 49/10 = 4.9 m/s² down the plane

*Level 4: Connected Masses (Atwood Machine)*
Two masses m₁ = 2 kg and m₂ = 3 kg connected by string over pulley
Find acceleration and tension

Assign: m₂ heavier, so it accelerates downward
For m₂ (taking down as positive):
m₂g − T = m₂a  ... (1)

For m₁ (taking up as positive):
T − m₁g = m₁a  ... (2)

Add equations (1) + (2):
m₂g − m₁g = (m₁ + m₂)a
(m₂ − m₁)g = (m₁ + m₂)a
a = (m₂ − m₁)/(m₁ + m₂) · g = (3−2)/(2+3) · 9.8 = 9.8/5 = 1.96 m/s²

From (1): T = m₂(g − a) = 3(9.8 − 1.96) = 3(7.84) = 23.52 N

**ULTRON Knowledge Record:**
```
CONCEPT: Newton's Second Law
STATEMENT: Net force equals mass times acceleration
EQUATION: ΣF = ma
UNITS: Force [N] = kg·m/s²
VECTOR_FORM: F⃗ = m · a⃗ (applies component-wise)
MOMENTUM_FORM: F = dp/dt (more general; allows variable mass)
INTERPRETATIONS:
  Force causes acceleration; mass resists
  Larger force → larger acceleration
  Larger mass → smaller acceleration
PROCEDURE:
  1. Draw free body diagram
  2. Identify all forces
  3. Apply ΣF = ma to each component
  4. Solve for unknowns
APPLICATIONS: Mechanics problems; vehicle acceleration; rocket thrust
VERIFICATION: Check units; test limits (F→0 gives a→0)
```

---

#### Concept 2.1.3: Newton's Third Law

**Statement:**
When one object exerts a force on another, the second object exerts an equal and opposite force on the first.

**Formal Expression:**
F⃗_AB = −F⃗_BA

"Action-reaction pairs"

**Key Insight:**
Forces never exist alone; they come in pairs.
The forces act on *different* objects (not canceling).

**Worked Example:**

When you push a wall with force F:
- Force on wall from you: F (away from you)
- Force on you from wall: F (away from wall, toward you)

These are equal and opposite, but:
- Act on different objects
- Don't cancel in a free body diagram of just you
- Both are real and active

**Common Misconception:**
MISCONCEPTION: "Since gravity pulls Earth toward me and I pull on Earth, they should move equally"
WHY: Equal and opposite forces
WRONG: F = ma; Earth has huge mass, so a is tiny
CORRECT: Same force, but F_earth = m_earth · a_earth
         Since m_earth >> m_person, a_earth << a_person

**ULTRON Knowledge Record:**
```
CONCEPT: Newton's Third Law
STATEMENT: Action-reaction; equal and opposite forces
EQUATION: F_AB = −F_BA
KEY_POINT: Forces in pairs; act on different objects
NOT_CANCELING: Different free body diagrams
MISCONCEPTION_CHECK:
  Forces equal and opposite in magnitude/direction
  But different masses can produce different accelerations
  Force = ma; mass matters!
APPLICATIONS: Rocket propulsion; jumping; collisions
VERIFICATION: Identify action-reaction pairs; confirm they're on different objects
```

---

## MODULE 3: ENERGY AND WORK

### CHAPTER 3.1: WORK AND KINETIC ENERGY

#### Concept 3.1.1: Work

**Precise Definition:**
Work done by a force F on an object moving through displacement d is:

W = F⃗ · d⃗ = Fd cos(θ)

where θ is angle between force and displacement.

**Units:** Joules [J] = N·m = kg·m²/s²

**Interpretations:**

1. **Scalar Product:** W = |F| |d| cos(θ)
2. **Component:** W = (component of F in direction of motion) × distance
3. **Graph:** W = area under F-d curve (in direction of motion)

**Sign Convention:**
- W > 0: Force aids motion (θ < 90°)
- W < 0: Force opposes motion (θ > 90°)
- W = 0: Force perpendicular to motion (θ = 90°)

**Worked Examples:**

*Level 2: Horizontal Force*
Push box horizontally 5 m with force 20 N
θ = 0°, so cos(θ) = 1
W = 20 × 5 × 1 = 100 J

*Level 3: Angled Force*
Pull box at 30° above horizontal with force 50 N, distance 4 m
W = 50 × 4 × cos(30°) = 200 × 0.866 = 173.2 J

*Level 4: Gravity Does Negative Work*
Lift box of mass 2 kg to height 3 m
Gravity force: F_g = mg = 2 × 9.8 = 19.6 N (downward)
Displacement: d = 3 m (upward)
Angle between F and d: θ = 180°, so cos(180°) = −1
W_gravity = 19.6 × 3 × (−1) = −58.8 J

To lift, you must do work against gravity: W_you = +58.8 J

**ULTRON Knowledge Record:**
```
CONCEPT: Work
DEFINITION: W = F⃗·d⃗ = Fd·cos(θ)
UNITS: Joules [J] = N·m
SCALAR_PRODUCT: Depends on angle between F and d
SIGN_CONVENTION:
  W > 0: Force aids motion
  W < 0: Force opposes motion
  W = 0: Force perpendicular
INTEGRAL_FORM: W = ∫ F⃗ · dr⃗ (along path)
APPLICATIONS: Lifting; pushing; gravity; friction
VERIFICATION: Check angle; confirm sign makes sense
```

---

#### Concept 3.1.2: Kinetic Energy and Work-Energy Theorem

**Kinetic Energy:**
KE = ½ m v²

Energy of motion; increases with mass and velocity (v² dependence).

**Work-Energy Theorem:**
The net work done on an object equals its change in kinetic energy:

W_net = ΔKE = KE_f − KE_i = ½m(v_f² − v_i²)

**Derivation:**
From Newton's second law: F_net = ma
Work by net force: W_net = ∫ F_net · dx = ∫ m a · dx
Using v dv = a dx:
W_net = m ∫ v dv = m[v²/2]|_vi to vf = ½m(v_f² − v_i²) = ΔKE

**Worked Example:**

*Level 3: Stopping Distance*
Car mass 1000 kg traveling at 20 m/s brakes to stop
Work done by brakes = change in KE
W_brake = ½m(0² − 20²) = ½(1000)(−400) = −200,000 J

If braking force is constant at F_brake = 5000 N:
W = F × d, so: −200,000 = −5000 × d
d = 40 m stopping distance

**Key Insight:**
Don't need to know acceleration or time;
work-energy theorem directly connects force, displacement, and velocity.

**ULTRON Knowledge Record:**
```
CONCEPT: Kinetic Energy and Work-Energy Theorem
KINETIC_ENERGY: KE = ½mv²
WORK_ENERGY_THEOREM: W_net = ΔKE
APPLICATION: Find final velocity from work; find stopping distance
UNITS: Energy [J] = kg·m²/s²
ADVANTAGE: Avoids kinematics; direct force-displacement-velocity link
VERIFICATION: Dimensional check; compare with kinematics approach
```

---

## MODULE 4: WAVES AND OSCILLATIONS

### CHAPTER 4.1: SIMPLE HARMONIC MOTION

#### Concept 4.1.1: Restoring Force and SHM

**Definition:**
Simple harmonic motion (SHM) occurs when the net force is proportional to displacement and opposes it:

F = −kx

where k is the spring constant and x is displacement from equilibrium.

**Equation of Motion:**
F = ma = m(d²x/dt²) = −kx
d²x/dt² = −(k/m)x = −ω²x

where ω² = k/m, so ω = √(k/m)

**Solution:**
x(t) = A cos(ωt + φ)

or equivalently:

x(t) = A cos(ωt) + B sin(ωt)

where A and B determined by initial conditions.

**Parameters:**

- **Amplitude (A):** Maximum displacement from equilibrium
- **Angular frequency (ω):** ω = √(k/m) [rad/s]
- **Period (T):** T = 2π/ω = 2π√(m/k) [seconds]
- **Frequency (f):** f = 1/T = ω/(2π) [Hz = cycles/s]
- **Phase (φ):** Determines starting point

**Velocity and Acceleration:**

v(t) = dx/dt = −Aω sin(ωt + φ) = ±ω√(A² − x²)

a(t) = d²x/dt² = −Aω² cos(ωt + φ) = −ω²x

**Energy:**

Total mechanical energy: E = ½kA² (constant)

At any position:
E = KE + PE = ½m v² + ½k x² = ½kA²

As object oscillates:
- At x = 0 (equilibrium): KE_max, PE_min
- At x = ±A (extremes): KE_min, PE_max

**Worked Example:**

*Level 3: Mass-Spring System*
Mass m = 0.5 kg attached to spring k = 200 N/m
Released from rest at x₀ = 0.1 m
Find: period, frequency, maximum velocity, maximum acceleration

Angular frequency: ω = √(k/m) = √(200/0.5) = √400 = 20 rad/s

Period: T = 2π/ω = 2π/20 = 0.314 s ≈ 0.31 s

Frequency: f = 1/T ≈ 3.18 Hz

Since released from rest at maximum displacement:
x(t) = A cos(ωt) where A = 0.1 m

Maximum velocity: v_max = Aω = 0.1 × 20 = 2 m/s

Maximum acceleration: a_max = Aω² = 0.1 × (20)² = 40 m/s²

**ULTRON Knowledge Record:**
```
CONCEPT: Simple Harmonic Motion
RESTORING_FORCE: F = −kx
EQUATION: d²x/dt² + ω²x = 0, where ω = √(k/m)
SOLUTION: x(t) = A·cos(ωt + φ)
PARAMETERS:
  Amplitude A: maximum displacement
  Angular frequency ω: √(k/m) [rad/s]
  Period T: 2π√(m/k) [seconds]
  Frequency f: 1/T [Hz]
  Phase φ: initial phase
VELOCITY: v(t) = −Aω·sin(ωt + φ)
ACCELERATION: a(t) = −ω²x(t)
ENERGY: E_total = ½kA² (conserved)
  KE + PE = ½mv² + ½kx² = ½kA²
APPLICATIONS: Springs; pendulums; atoms; LC circuits
VERIFICATION: Check that d²x/dt² = −ω²x
```

---

## MODULE 5: ELECTROMAGNETISM

### CHAPTER 5.1: ELECTRIC FIELDS AND FORCES

#### Concept 5.1.1: Electric Charge and Coulomb's Law

**Charge:**
- Fundamental property; comes in discrete units: e = 1.6 × 10⁻¹⁹ C
- Proton: +e, Electron: −e
- Measured in Coulombs [C]

**Coulomb's Law:**
Force between two point charges q₁ and q₂ separated by distance r:

F = k |q₁ q₂| / r²

where k = 8.99 × 10⁹ N·m²/C² (Coulomb's constant)

Or in SI form: k = 1/(4πε₀), where ε₀ = 8.85 × 10⁻¹² F/m

**Vector Form:**
F⃗₁₂ = k (q₁q₂/r²) · r̂

where r̂ is unit vector from charge 1 to charge 2

**Key Properties:**

1. **Inverse Square Law:** F ∝ 1/r²
2. **Superposition:** Multiple charges → vector sum of forces
3. **Action at Distance:** Force through empty space (mediated by field)
4. **Same signs repel; opposite signs attract**

**Worked Example:**

*Level 2: Two Point Charges*
q₁ = 2 × 10⁻⁶ C and q₂ = 3 × 10⁻⁶ C separated by r = 0.5 m
Find force between them

F = k |q₁q₂| / r²
  = (8.99 × 10⁹) × (2 × 10⁻⁶) × (3 × 10⁻⁶) / (0.5)²
  = (8.99 × 10⁹) × (6 × 10⁻¹²) / 0.25
  = 53.94 × 10⁻³ / 0.25
  = 0.216 N

Since both positive: repulsive force

**ULTRON Knowledge Record:**
```
CONCEPT: Coulomb's Law
LAW: F = k|q₁q₂|/r²
CONSTANT: k = 8.99 × 10⁹ N·m²/C² = 1/(4πε₀)
TYPE: Inverse square law (like gravity)
VECTOR_FORM: F⃗ = k(q₁q₂/r²)·r̂
SUPERPOSITION: Multiple charges → vector sum
REPULSION/ATTRACTION:
  Same sign: repulsive (F > 0)
  Opposite sign: attractive (F < 0)
COMPARISON_TO_GRAVITY:
  Both inverse square; Coulomb ~10³⁶ stronger
  Gravity always attractive; Coulomb can repel
APPLICATIONS: Atomic forces; electrostatic devices
VERIFICATION: Check units; test inverse square relationship
```

---

#### Concept 5.1.2: Electric Field

**Definition:**
The electric field E at a point is the force per unit positive charge:

E⃗ = F⃗ / q = (force on test charge) / (charge)

**Units:** Newtons per Coulomb [N/C] or Volts per meter [V/m]

**Field from Point Charge:**
E = kQ / r²

where Q is the source charge; r is distance from Q.

**Direction:** 
- From positive charge (outward)
- Toward negative charge (inward)

**Superposition:**
Multiple charges → vector sum of individual fields

**Worked Example:**

*Level 3: Field from Two Charges*
q₁ = +2 μC at origin, q₂ = −3 μC at x = 1 m
Find electric field at x = 0.5 m

Field from q₁ (positive, points away from q₁):
E₁ = kq₁/r₁² = (8.99 × 10⁹)(2 × 10⁻⁶)/(0.5)² = 7.19 × 10⁴ N/C (pointing right)

Field from q₂ (negative, points toward q₂):
E₂ = kq₂/r₂² = (8.99 × 10⁹)(3 × 10⁻⁶)/(0.5)² = 1.08 × 10⁵ N/C (pointing right toward q₂)

Total field: E_total = E₁ + E₂ = (7.19 + 10.8) × 10⁴ = 1.80 × 10⁵ N/C (pointing right)

**ULTRON Knowledge Record:**
```
CONCEPT: Electric Field
DEFINITION: E⃗ = F⃗/q (force per unit charge)
UNITS: N/C or V/m
FIELD_FROM_POINT_CHARGE: E = kQ/r²
DIRECTION:
  From positive charge (radially outward)
  Toward negative charge (radially inward)
SUPERPOSITION: E_total = ΣE_i (vector sum)
FIELD_LINES: Visual representation; tangent = field direction
APPLICATIONS: Particle motion in fields; capacitors
VERIFICATION: Check direction; test inverse square; use superposition
```

---

## MODULE 6: THERMODYNAMICS

### CHAPTER 6.1: TEMPERATURE AND HEAT

#### Concept 6.1.1: Temperature

**Definition:**
Temperature is a measure of the average kinetic energy of particles in a substance.

**Scales:**
- **Kelvin (K):** Absolute scale; 0 K = absolute zero
- **Celsius (°C):** T(K) = T(°C) + 273.15
- **Fahrenheit (°F):** T(°C) = (T(°F) − 32) × 5/9

**Thermal Equilibrium:**
Two objects have same temperature when in thermal equilibrium (no heat flow).

**Worked Example:**

Convert 25°C to Kelvin:
T(K) = 25 + 273.15 = 298.15 K ≈ 298 K

**ULTRON Knowledge Record:**
```
CONCEPT: Temperature
DEFINITION: Measure of average kinetic energy of particles
SCALES:
  Kelvin (K): Absolute scale
  Celsius: T(K) = T(°C) + 273.15
  Fahrenheit: T(°C) = (T(°F) − 32) × 5/9
THERMAL_EQUILIBRIUM: Same temperature → no heat flow
ABSOLUTE_ZERO: 0 K (no thermal motion)
APPLICATIONS: Heat transfer; thermodynamics; material properties
VERIFICATION: Check scale conversions
```

---

#### Concept 6.1.2: Heat and Internal Energy

**Heat (Q):**
Energy transfer due to temperature difference.
Units: Joules [J] or calories [cal]; 1 cal = 4.186 J

**Internal Energy (U):**
Total kinetic + potential energy of all particles in substance.

**First Law of Thermodynamics:**
ΔU = Q − W

Change in internal energy = heat added − work done by system

**Interpretation:**
- Q > 0: Heat flows into system
- Q < 0: Heat flows out of system
- W > 0: System does work on surroundings
- W < 0: Surroundings do work on system

**Specific Heat Capacity (c):**
Heat needed to raise temperature of unit mass by 1°C:

Q = m c ΔT

where c [J/(kg·°C)]

**Worked Example:**

*Level 3: Heating Water*
Heat 2 kg of water from 20°C to 80°C
Specific heat of water: c = 4186 J/(kg·°C)

Q = m c ΔT = 2 × 4186 × (80 − 20) = 2 × 4186 × 60 = 502,320 J ≈ 502 kJ

**ULTRON Knowledge Record:**
```
CONCEPT: Heat and Internal Energy
HEAT: Q = energy transfer due to temperature difference [J]
INTERNAL_ENERGY: U = total kinetic + potential energy of particles
FIRST_LAW: ΔU = Q − W
INTERPRETATION:
  Q > 0: heat in
  Q < 0: heat out
  W > 0: system does work
  W < 0: work on system
SPECIFIC_HEAT: Q = m·c·ΔT
  c = heat capacity [J/(kg·°C)]
APPLICATIONS: Calorimetry; heat transfer; power generation
VERIFICATION: Check signs; dimensional analysis
```

---

## MODULE 7: MODERN PHYSICS

### CHAPTER 7.1: SPECIAL RELATIVITY

#### Concept 7.1.1: The Postulates and Lorentz Transformation

**Einstein's Postulates (1905):**

1. **Principle of Relativity:** Laws of physics are same in all inertial reference frames
2. **Constancy of Light Speed:** Speed of light c = 3 × 10⁸ m/s is same in all inertial frames

**Consequence:**
Time and space are not absolute; they depend on observer's motion.

**Lorentz Transformation:**
For observer moving at velocity v along x-axis:

t' = γ(t − vx/c²)
x' = γ(x − vt)
y' = y
z' = z

where γ = 1/√(1 − v²/c²) (Lorentz factor)

**Time Dilation:**
Moving clock runs slow:
Δt' = γ Δt₀

where Δt₀ is proper time (time in rest frame)

**Length Contraction:**
Moving object is shorter:
L' = L₀/γ = L₀√(1 − v²/c²)

where L₀ is proper length (length at rest)

**Relativistic Mass and Energy:**

Rest energy: E₀ = mc²

Total energy: E = γmc²

Momentum: p = γmv

**Energy-Momentum Relation:**
E² = (pc)² + (mc²)²

**Worked Example:**

*Level 3: Time Dilation*
Astronaut travels at v = 0.6c for 1 hour of proper time
Time elapsed on Earth?

γ = 1/√(1 − 0.6²) = 1/√(1 − 0.36) = 1/√0.64 = 1/0.8 = 1.25

Δt_Earth = γ Δt_proper = 1.25 × 1 hour = 1.25 hours

The astronaut experiences 1 hour; Earth clock shows 1.25 hours.

**ULTRON Knowledge Record:**
```
CONCEPT: Special Relativity
POSTULATES:
  1. Relativity: Physics same in all inertial frames
  2. Light speed constant: c = 3×10⁸ m/s
LORENTZ_FACTOR: γ = 1/√(1−v²/c²)
TIME_DILATION: Δt' = γ·Δt₀ (moving clock runs slow)
LENGTH_CONTRACTION: L' = L₀/√(1−v²/c²) (moving object is short)
MASS_ENERGY: E = mc²; E = γmc² (total); E₀ = mc² (rest)
MOMENTUM: p = γmv
INVARIANT: E² − (pc)² = (mc²)² (same in all frames)
APPLICATIONS: Particle accelerators; GPS satellites; stars
VERIFICATION: Check γ → 1 as v → 0 (classical limit)
```

---

#### Concept 7.1.2: The Photon

**Light as Particle:**
Photons have energy E = hf where:
- h = 6.626 × 10⁻³⁴ J·s (Planck's constant)
- f = frequency [Hz]
- c = λf (wavelength × frequency)

**Photon Energy:**
E = hf = hc/λ

**Photon Momentum:**
p = E/c = h/λ

**Applications:**

*Photoelectric Effect:*
Light ejects electrons from metal
Energy equation: hf = φ + KE_max
where φ = work function (metal property)

**Worked Example:**

*Level 2: Photon Energy*
Find energy of photon with wavelength λ = 500 nm (visible light)

f = c/λ = (3 × 10⁸)/(500 × 10⁻⁹) = 6 × 10¹⁴ Hz

E = hf = (6.626 × 10⁻³⁴)(6 × 10¹⁴) = 3.976 × 10⁻¹⁹ J

In eV: E = 3.976 × 10⁻¹⁹ / (1.6 × 10⁻¹⁹) ≈ 2.49 eV

**ULTRON Knowledge Record:**
```
CONCEPT: Photon
DEFINITION: Quantum particle of light
ENERGY: E = hf = hc/λ
MOMENTUM: p = E/c = h/λ
CONSTANTS:
  h = 6.626 × 10⁻³⁴ J·s (Planck)
  c = 3 × 10⁸ m/s
DOPPLER: f_obs = f_source · √((c±v_obs)/(c±v_source))
APPLICATIONS: Spectroscopy; photosynthesis; lasers
VERIFICATION: Check units; dimensional analysis
```

---

## CROSS-SUBJECT CONNECTIONS

### Physics ↔ Mathematics
- **Calculus:** Kinematics uses derivatives/integrals
- **Differential Equations:** Motion, waves, heat governed by PDEs
- **Linear Algebra:** Electric/magnetic fields as vectors/matrices
- **Vectors:** Force, velocity, fields

### Physics ↔ Chemistry
- **Atomic Structure:** Quantum mechanics of electrons
- **Bonding:** Electrostatic forces between atoms
- **Thermochemistry:** Heat flow in reactions (ΔH)
- **Equilibrium:** Minimizes free energy (thermodynamics)

### Physics ↔ Biology
- **Biophysics:** Osmosis, nerve impulses (electrical)
- **Vision:** Photons interact with photoreceptors
- **Muscle:** Mechanical forces from chemical energy
- **Thermodynamics:** Metabolic efficiency

### Physics ↔ Computer Science
- **Simulation:** Numerical solutions to physics problems
- **Image Processing:** Fourier transforms (wave concept)
- **Machine Learning:** Physics-inspired architectures
- **Quantum Computing:** Quantum mechanics

---

## MISCONCEPTION DATABASE

**1. MISCONCEPTION: "Heavier objects fall faster"**
   
   WHY: Intuition from air resistance
   
   WRONG: Ignores air resistance; in vacuum all fall same
   
   CORRECT: All objects accelerate at g (9.8 m/s²) regardless of mass
   
   COUNTEREXAMPLE: Feather and hammer on Moon (no air)

**2. MISCONCEPTION: "Velocity and acceleration point same direction"**
   
   WHY: Words suggest positive together
   
   WRONG: Acceleration can oppose velocity (deceleration)
   
   CORRECT: Car braking has velocity forward, acceleration backward
   
   CHECK: a = Δv/Δt; if Δv is backward, a points backward

**3. MISCONCEPTION: "Heat and temperature are same thing"**
   
   WHY: People use interchangeably
   
   WRONG: Heat is energy transfer; temperature is average KE
   
   EXAMPLE: Hot cup of water ≠ hot iron nail
   (Water has more heat energy, but same temperature)

**4. MISCONCEPTION: "Work done = Force × Distance (always)"**
   
   WHY: Simplest formula
   
   WRONG: W = F·d·cos(θ); angle matters!
   
   COUNTEREXAMPLE: Carry horizontally → gravity does zero work
   (W = mg × d × cos(90°) = 0)

**5. MISCONCEPTION: "Nothing travels faster than light"**
   
   WHY: Relativity (misread)
   
   CLARIFICATION: No *information* travels faster; some velocities can
   (e.g., phase velocity of wave in medium)

---

## SELF-TEST

### Level 1 (Recall)
1. Define velocity and acceleration
2. State Newton's three laws
3. What is kinetic energy?
4. Define heat and temperature
5. What is Coulomb's law?
6. What is a photon?
7. Define wavelength and frequency
8. What is the speed of light?
9. State the first law of thermodynamics
10. What is gravitational force?

### Level 2 (Application)
1. A ball is thrown horizontally at 20 m/s from a 45 m cliff. When does it hit?
2. A 10 kg box is pushed with 50 N. Find acceleration (ignore friction)
3. Calculate work done lifting 5 kg to height 2 m
4. Find the kinetic energy of 0.5 kg ball moving at 10 m/s
5. Two charges (+2 μC and −3 μC) separated by 0.5 m. Find force
6. Calculate heat needed to warm 1 kg water by 10°C
7. Find period of mass-spring (m=0.2 kg, k=100 N/m)
8. Electron moving at 0.1c: find Lorentz factor γ
9. Photon wavelength 600 nm: find frequency
10. Pendulum length 1 m: find period

### Level 4 (Reasoning)
1. Why doesn't a car continue forever when you stop accelerating?
2. Explain why a satellite in orbit is always "falling"
3. Why can't you feel constant velocity, only acceleration?
4. How is energy conserved in an inelastic collision?
5. Explain time dilation without using equations
6. Why is the sky blue? (Scattering, wavelength)
7. How does the photoelectric effect prove photons exist?
8. Why is absolute zero a hard limit (not just practical)?
9. Explain why copper is a better heat conductor than plastic
10. How do electric motors use forces between fields and currents?

---

**END PHYSICS CURRICULUM v1.0**

*Remaining modules (Chemistry, Biology, Computer Science, Electronics, Engineering Mechanics, Astronomy) follow with same depth.*

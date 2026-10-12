"""OpenRocket backend: OpenRocket's own Java core, driven from Python through JPype.

One JVM per Python process (a JPype limit), started on first use. It uses the Java
runtime bundled with the OpenRocket install, so no separate JDK is needed.
Install location: $OPENROCKET_HOME, default C:\\Program Files\\OpenRocket.

Lake 1 uses this to prove OpenRocket reads our files the way we do. Lake 2 adds sims.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOME = Path(r"C:\Program Files\OpenRocket")

# .ork field name -> OpenRocket getter, per OpenRocket component class.
DIMENSION_GETTERS = {
    "NoseCone": {"length": "getLength", "aftradius": "getAftRadius", "thickness": "getThickness"},
    "BodyTube": {"length": "getLength", "radius": "getOuterRadius", "thickness": "getThickness"},
    "TrapezoidFinSet": {
        "fincount": "getFinCount",
        "rootchord": "getRootChord",
        "tipchord": "getTipChord",
        "height": "getHeight",
        "sweeplength": "getSweep",
        "thickness": "getThickness",
    },
}


class OpenRocketUnavailable(RuntimeError):
    """No usable OpenRocket install, or jpype1 is missing."""


@dataclass(frozen=True)
class Install:
    home: Path
    jar: Path
    jvm: Path
    version: str  # from the jar name, e.g. "24.12"

    @property
    def backend(self) -> str:
        return f"OpenRocket {self.version}"


@dataclass(frozen=True)
class MotorData:
    designation: str
    digest: str  # identifies the exact thrust curve
    total_impulse: float  # N*s
    burn_time: float  # s, OpenRocket's estimate
    average_thrust: float  # N
    launch_mass: float  # kg
    burnout_mass: float  # kg


@dataclass(frozen=True)
class ComponentData:
    kind: str  # OpenRocket class name, e.g. "TrapezoidFinSet"
    name: str
    mass: float  # kg, this component alone
    dimensions: dict[str, float] = field(default_factory=dict)  # keyed by .ork field name


@dataclass(frozen=True)
class LoadedRocket:
    backend: str
    warnings: list[str]
    components: list[ComponentData]
    dry_mass: float  # kg, structure without motors
    launch_mass: float  # kg, with motors
    motors: list[MotorData]

    def component(self, name: str) -> ComponentData:
        matches = [c for c in self.components if c.name == name.strip()]
        if len(matches) != 1:
            raise KeyError(f"{len(matches)} OpenRocket components named {name!r}")
        return matches[0]


def find_install(home: str | Path | None = None) -> Install:
    # An empty OPENROCKET_HOME (e.g. an unset Claude Desktop field) means the default.
    home = Path(home or os.environ.get("OPENROCKET_HOME") or DEFAULT_HOME)
    jars = sorted((home / "jar").glob("OpenRocket-*.jar"))
    jvm = home / "jre" / "bin" / "server" / "jvm.dll"
    if not jars or not jvm.exists():
        raise OpenRocketUnavailable(f"no OpenRocket install with a bundled Java at {home}")
    jar = jars[-1]
    return Install(home, jar, jvm, jar.stem.removeprefix("OpenRocket-"))


_running: Install | None = None
_start_lock = threading.Lock()  # the MCP server calls in from worker threads


def start(home: str | Path | None = None) -> Install:
    """Start the JVM and OpenRocket's core, once per process. Takes ~5 s (motor database)."""
    with _start_lock:
        return _start(home)


def _start(home: str | Path | None) -> Install:
    global _running
    if _running is not None:
        return _running
    try:
        import jpype
    except ImportError as error:
        raise OpenRocketUnavailable("jpype1 missing: pip install -e .[openrocket]") from error
    install = find_install(home)
    if jpype.isJVMStarted():
        raise OpenRocketUnavailable("a JVM without OpenRocket is already running in this process")

    # The JVM raises and handles access violations on purpose while starting up. Python's
    # faulthandler (pytest turns it on) would print them as "fatal" even though nothing failed.
    import faulthandler

    faulthandler_was_on = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        jpype.startJVM(
            str(install.jvm),
            "-Djava.awt.headless=true",
            classpath=[str(install.jar)],
            convertStrings=True,
        )
    finally:
        if faulthandler_was_on:
            faulthandler.enable()
    # Java never writes to our stdout: the MCP stdio server owns it (stray bytes break the
    # protocol), and CLI output stays clean when redirected.
    system = jpype.JClass("java.lang.System")
    system.setOut(system.err)
    level = jpype.JClass("ch.qos.logback.classic.Level")
    jpype.JClass("org.slf4j.LoggerFactory").getLogger("ROOT").setLevel(level.WARN)

    plugin_module = jpype.JClass("info.openrocket.core.plugin.PluginModule")
    core = jpype.JClass("info.openrocket.core.startup.OpenRocketCore")
    # Pass PluginModule explicitly. JPype resolves a bare initialize() to the varargs
    # overload with zero modules, and Guice then fails on Set<RocketSubstitutor>.
    core.initialize(plugin_module())
    _running = install
    return install


def load(path: str | Path) -> LoadedRocket:
    """Load an .ork with OpenRocket's own loader and snapshot what it read."""
    install = start()
    import jpype

    java_file = jpype.JClass("java.io.File")(str(Path(path).resolve()))
    loader = jpype.JClass("info.openrocket.core.file.GeneralRocketLoader")(java_file)
    rocket = loader.load().getRocket()
    config = rocket.getSelectedConfiguration()
    mass_calculator = jpype.JClass("info.openrocket.core.masscalc.MassCalculator")

    return LoadedRocket(
        backend=install.backend,
        warnings=[str(w) for w in loader.getWarnings()],
        components=[_component_data(c) for c in rocket],
        dry_mass=float(mass_calculator.calculateStructure(config).getMass()),
        launch_mass=float(mass_calculator.calculateLaunch(config).getMass()),
        motors=[_motor_data(mc.getMotor()) for mc in config.getActiveMotors()],
    )


@dataclass(frozen=True)
class ComponentAero:
    name: str
    cp_x: float  # m from the nose tip
    cn_alpha: float  # normal-force coefficient slope, per rad


@dataclass(frozen=True)
class StaticAnalysis:
    """What OpenRocket's design view shows: RocketPanel.updateExtras, OpenRocket 24.12."""

    backend: str
    mach: float
    cg_x: float  # m from the nose tip, with motors (MassCalculator.calculateLaunch)
    cg_burnout_x: float  # m, motors burnt out (calculateBurnout). Max velocity happens here.
    cp_x: float  # m from the nose tip, worst case over roll angle (Barrowman getWorstCP)
    length: float  # m
    max_diameter: float  # m, the caliber reference
    mass_with_motors: float  # kg
    mass_without_motors: float  # kg
    components: list[ComponentAero]
    thrust_curve: list[tuple[float, float]]  # (s, N) of the selected motor
    warnings: list[str]

    @property
    def calibers(self) -> float:
        return (self.cp_x - self.cg_x) / self.max_diameter

    @property
    def burnout_calibers(self) -> float:
        """Stability with the burnout CG: the right one at max velocity / max Mach."""
        return (self.cp_x - self.cg_burnout_x) / self.max_diameter

    @property
    def percent_of_length(self) -> float:
        return 100 * (self.cp_x - self.cg_x) / self.length


def static_analysis(path: str | Path, mach: float = 0.3) -> StaticAnalysis:
    """CG, CP and stability exactly as OpenRocket's design view computes them (AOA 0, no roll)."""
    install = start()
    import jpype

    java_file = jpype.JClass("java.io.File")(str(Path(path).resolve()))
    rocket = (
        jpype.JClass("info.openrocket.core.file.GeneralRocketLoader")(java_file).load().getRocket()
    )
    config = rocket.getSelectedConfiguration()
    conditions = jpype.JClass("info.openrocket.core.aerodynamics.FlightConditions")(config)
    conditions.setMach(mach)
    conditions.setAOA(0.0)
    conditions.setRollRate(0.0)
    warnings = jpype.JClass("info.openrocket.core.logging.WarningSet")()
    calculator = jpype.JClass("info.openrocket.core.aerodynamics.BarrowmanCalculator")()
    mass_calculator = jpype.JClass("info.openrocket.core.masscalc.MassCalculator")
    symmetric = jpype.JClass("info.openrocket.core.rocketcomponent.SymmetricComponent")

    cp = calculator.getWorstCP(config, conditions, warnings)
    launch = mass_calculator.calculateLaunch(config)
    diameters = [
        2 * max(float(c.getForeRadius()), float(c.getAftRadius()))
        for c in config.getCoreComponents()
        if isinstance(c, symmetric)
    ]
    forces = calculator.getForceAnalysis(config, conditions, warnings)
    # OpenRocket stores each component's CN-alpha in the weight of its CP coordinate.
    components = [
        ComponentAero(str(c.getName()).strip(), float(f.getCP().x), float(f.getCP().weight))
        for c, f in forces.items()
        if not isinstance(c, jpype.JClass("info.openrocket.core.rocketcomponent.Rocket"))
        and float(f.getCP().weight) != 0.0
    ]
    motors = [mc.getMotor() for mc in config.getActiveMotors()]
    curve = (
        list(
            zip(
                map(float, motors[0].getTimePoints()),
                map(float, motors[0].getThrustPoints()),
                strict=True,  # a length mismatch is a data error: fail, never truncate
            )
        )
        if motors
        else []
    )
    return StaticAnalysis(
        backend=install.backend,
        mach=mach,
        cg_x=float(launch.getCM().x),
        cg_burnout_x=float(mass_calculator.calculateBurnout(config).getCM().x),
        cp_x=float(cp.x),
        length=float(config.getLength()),
        max_diameter=max(diameters),
        mass_with_motors=float(launch.getMass()),
        mass_without_motors=float(mass_calculator.calculateStructure(config).getMass()),
        components=components,
        thrust_curve=curve,
        warnings=[str(w) for w in warnings],
    )


def _component_data(c) -> ComponentData:
    kind = str(c.getClass().getSimpleName())
    getters = DIMENSION_GETTERS.get(kind, {})
    return ComponentData(
        kind=kind,
        name=str(c.getName()).strip(),
        mass=float(c.getComponentMass()),
        dimensions={tag: float(getattr(c, getter)()) for tag, getter in getters.items()},
    )


def _motor_data(motor) -> MotorData:
    return MotorData(
        designation=str(motor.getDesignation()),
        digest=str(motor.getDigest()),
        total_impulse=float(motor.getTotalImpulseEstimate()),
        burn_time=float(motor.getBurnTimeEstimate()),
        average_thrust=float(motor.getAverageThrustEstimate()),
        launch_mass=float(motor.getLaunchMass()),
        burnout_mass=float(motor.getBurnoutMass()),
    )


# ---------- simulations (Lake 2) ----------

# Time series exported from every run: Ogive name -> OpenRocket FlightDataType constant.
SERIES = {
    "time": "TYPE_TIME",
    "altitude": "TYPE_ALTITUDE",
    "velocity": "TYPE_VELOCITY_TOTAL",
    "vertical_velocity": "TYPE_VELOCITY_Z",
    "acceleration": "TYPE_ACCELERATION_TOTAL",
    "mach": "TYPE_MACH_NUMBER",
    "stability": "TYPE_STABILITY",
    "cg": "TYPE_CG_LOCATION",
    "cp": "TYPE_CP_LOCATION",
    "mass": "TYPE_MASS",
    "thrust": "TYPE_THRUST_FORCE",
    "drag": "TYPE_DRAG_FORCE",
    "cd": "TYPE_DRAG_COEFF",
    "aoa": "TYPE_AOA",
    "east": "TYPE_POSITION_X",
    "north": "TYPE_POSITION_Y",
    "wind": "TYPE_WIND_VELOCITY",
    "pressure": "TYPE_AIR_PRESSURE",
    "speed_of_sound": "TYPE_SPEED_OF_SOUND",
}


@dataclass(frozen=True)
class LaunchConditions:
    """The conditions a sim was run under. Snapshot of SimulationOptions."""

    rod_length: float  # m
    rod_angle: float  # rad
    latitude: float
    longitude: float
    altitude: float  # m MSL
    wind_average: float  # m/s
    wind_turbulence: float  # intensity, fraction
    temperature: float  # K at launch (used only when isa is False)
    pressure: float  # Pa at launch (used only when isa is False)
    isa: bool  # True = International Standard Atmosphere
    time_step: float  # s
    seed: int


@dataclass(frozen=True)
class FlightEvent:
    time: float  # s
    type: str  # OpenRocket's name, e.g. "Apogee", "Motor burnout"


@dataclass(frozen=True)
class FlightSummary:
    """OpenRocket's FlightData summary: the numbers the Flight simulations table shows."""

    apogee: float  # m AGL
    max_velocity: float  # m/s
    max_mach: float
    max_acceleration: float  # m/s^2
    time_to_apogee: float  # s
    flight_time: float  # s
    ground_hit_velocity: float  # m/s, TOTAL speed in OpenRocket 24.12 (not vertical)
    launch_rod_velocity: float  # m/s
    deployment_velocity: float  # m/s, at the last recovery device deployment
    optimum_delay: float  # s


@dataclass(frozen=True)
class FlightResult:
    backend: str
    file: str
    simulation: str  # sim name in the file
    conditions: LaunchConditions
    summary: FlightSummary
    events: list[FlightEvent]
    warnings: list[str]
    series: dict[str, list[float]]  # keys from SERIES; all the same length

    @property
    def seed(self) -> int:
        return self.conditions.seed

    def event_time(self, type_name: str) -> float | None:
        for e in self.events:
            if e.type == type_name:
                return e.time
        return None


def list_simulations(path: str | Path) -> list[str]:
    start()
    return [str(s.getName()) for s in _load_document(path).getSimulations()]


def simulate(
    path: str | Path, simulation: str | None = None, seed: int | None = None
) -> FlightResult:
    """Run one of the file's simulations headless, exactly as OpenRocket would.

    simulation: sim name in the file; default is the first one.
    seed: the random seed for wind turbulence. Same file + same seed = identical result.
    None keeps the seed stored in the file, so a re-run reproduces what OpenRocket last ran.
    """
    install = start()
    import jpype

    path = Path(path)
    doc = _load_document(path)
    sims = {str(s.getName()): s for s in doc.getSimulations()}
    if not sims:
        raise ValueError(f"{path.name} has no simulations")
    if simulation is None:
        simulation = next(iter(sims))
    if simulation not in sims:
        raise KeyError(f"no simulation named {simulation!r} in {path.name}. Known: {list(sims)}")
    sim = sims[simulation]
    options = sim.getOptions()
    if seed is not None:
        _set_seed(options, int(seed))

    try:
        sim.simulate()
    except jpype.JException as error:
        raise RuntimeError(f"OpenRocket could not simulate {simulation!r}: {error}") from error

    data = sim.getSimulatedData()
    branch = data.getBranch(0)
    types = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
    series = {}
    for name, constant in SERIES.items():
        values = branch.get(getattr(types, constant))
        series[name] = [float(v) for v in values] if values is not None else []
    return FlightResult(
        backend=install.backend,
        file=str(path),
        simulation=simulation,
        conditions=_conditions(options),
        summary=FlightSummary(
            apogee=float(data.getMaxAltitude()),
            max_velocity=float(data.getMaxVelocity()),
            max_mach=float(data.getMaxMachNumber()),
            max_acceleration=float(data.getMaxAcceleration()),
            time_to_apogee=float(data.getTimeToApogee()),
            flight_time=float(data.getFlightTime()),
            ground_hit_velocity=float(data.getGroundHitVelocity()),
            launch_rod_velocity=float(data.getLaunchRodVelocity()),
            deployment_velocity=float(data.getDeploymentVelocity()),
            optimum_delay=float(data.getOptimumDelay()),
        ),
        events=[FlightEvent(float(e.getTime()), str(e.getType())) for e in branch.getEvents()],
        warnings=[str(w) for w in sim.getSimulatedWarnings()],
        series=series,
    )


def _set_seed(options, seed: int) -> None:
    """Make the seed take effect. OpenRocket 24.12 stores it but never passes it to the wind model.

    SimulationOptions builds its PinkNoiseWindModel once, in the constructor, with a seed from
    new Random(). setRandomSeed() only updates a field that the simulation never reads, so two
    freshly loaded documents give different wind for the same seed. The wind model's seed is
    final, so we rebuild the model with our seed, copy its settings, and swap it in.
    """
    import jpype

    options.setRandomSeed(seed)
    wind_type = str(options.getWindModelType())
    if wind_type != "AVERAGE":
        raise NotImplementedError(f"seeding the {wind_type} wind model isn't supported yet")
    old = options.getAverageWindModel()
    new = jpype.JClass("info.openrocket.core.models.wind.PinkNoiseWindModel")(seed)
    new.setAverage(old.getAverage())
    new.setDirection(old.getDirection())
    new.setStandardDeviation(old.getStandardDeviation())
    field = options.getClass().getDeclaredField("averageWindModel")
    field.setAccessible(True)
    field.set(options, new)


def _load_document(path: str | Path):
    import jpype

    java_file = jpype.JClass("java.io.File")(str(Path(path).resolve()))
    return jpype.JClass("info.openrocket.core.file.GeneralRocketLoader")(java_file).load()


def _conditions(options) -> LaunchConditions:
    return LaunchConditions(
        rod_length=float(options.getLaunchRodLength()),
        rod_angle=float(options.getLaunchRodAngle()),
        latitude=float(options.getLaunchLatitude()),
        longitude=float(options.getLaunchLongitude()),
        altitude=float(options.getLaunchAltitude()),
        wind_average=float(options.getWindSpeedAverage()),
        wind_turbulence=float(options.getWindTurbulenceIntensity()),
        temperature=float(options.getLaunchTemperature()),
        pressure=float(options.getLaunchPressure()),
        isa=bool(options.isISAAtmosphere()),
        time_step=float(options.getTimeStep()),
        seed=int(options.getRandomSeed()),
    )

"""OpenRocket backend: OpenRocket's own Java core, driven from Python through JPype.

One JVM per Python process (a JPype limit), started on first use.

Where OpenRocket is: $OPENROCKET_HOME, either an install folder or the OpenRocket-*.jar file
itself. Unset or empty, Ogive looks in the usual install places for this OS (DEFAULT_HOMES).
Which Java: the runtime bundled with the install if it has one (the Windows, macOS and Linux
installers all ship one), else $JAVA_HOME, else JPype's default. OpenRocket 24.12 needs Java 17+.

Lake 1 uses this to prove OpenRocket reads our files the way we do. Lake 2 adds sims.
"""

from __future__ import annotations

import math
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOMES = {
    "win32": [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "OpenRocket"],
    "darwin": [Path("/Applications/OpenRocket.app"), Path.home() / "Applications/OpenRocket.app"],
    "linux": [Path.home() / "OpenRocket", Path("/opt/OpenRocket"), Path("/opt/openrocket")],
}
# The JVM library inside a Java home, per OS layout.
JVM_LIBRARIES = ("bin/server/jvm.dll", "lib/server/libjvm.so", "lib/server/libjvm.dylib")
MIN_JAVA = 17  # OpenRocket 24.12's class files are Java 17

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
    """Find OpenRocket and a Java 17+ to run it, or say where it looked (OpenRocketUnavailable).

    `home` (default $OPENROCKET_HOME) is an install folder or an OpenRocket-*.jar. Unset, empty, or
    an unfilled Claude Desktop placeholder ("${user_config...}") means: search DEFAULT_HOMES.
    """
    given = str(home or os.environ.get("OPENROCKET_HOME") or "").strip()
    if given.startswith("${"):
        given = ""
    if given:
        candidates = [Path(given).expanduser()]
    else:
        candidates = DEFAULT_HOMES.get(sys.platform, DEFAULT_HOMES["linux"])
    for candidate in candidates:
        jar = _find_jar(candidate)
        if jar is not None:
            break
    else:
        looked = ", ".join(str(c) for c in candidates)
        raise OpenRocketUnavailable(
            f"no OpenRocket install or OpenRocket-*.jar at {looked}. Install OpenRocket 24.12, "
            "or set OPENROCKET_HOME to its folder or to OpenRocket-24.12.jar"
        )
    # A bundled Java only counts inside an install folder: next to a loose jar (say in
    # Downloads) any JDK found would be a stranger's.
    jvm = (_search(candidate, _is_jvm_library) if candidate.is_dir() else None) or _system_jvm()
    _check_java(jvm)
    match = re.fullmatch(r"OpenRocket-(.+)\.jar", jar.name)
    root = candidate if candidate.is_dir() else jar.parent
    return Install(root, jar, jvm, match.group(1) if match else "unknown version")


def _find_jar(path: Path) -> Path | None:
    if path.is_file():
        return path if path.suffix == ".jar" else None
    if not path.is_dir():
        return None
    jars = sorted((path / "jar").glob("OpenRocket-*.jar"))  # Windows and Linux installers
    if jars:
        return jars[-1]
    return _search(path, lambda p: re.fullmatch(r"OpenRocket-.+\.jar", p.name) is not None)


def _search(root: Path, wanted, max_depth: int = 9) -> Path | None:
    """First file under root (breadth first, sorted, at most max_depth folders down) that is wanted.

    Bounded so a wrong OPENROCKET_HOME (a whole drive) fails fast instead of crawling it. Nine
    covers the macOS app bundle, whose Java sits about eight folders down.
    """
    level = [root]
    for _ in range(max_depth + 1):
        below = []
        for folder in level:
            try:
                entries = sorted(folder.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir() and not entry.is_symlink():
                    below.append(entry)
                elif wanted(entry):
                    return entry
        level = below
    return None


def _is_jvm_library(path: Path) -> bool:
    return any(path.as_posix().endswith("/" + library) for library in JVM_LIBRARIES)


def _system_jvm() -> Path:
    java_home = os.environ.get("JAVA_HOME", "").strip()
    if java_home:
        for library in JVM_LIBRARIES:
            if (Path(java_home) / library).is_file():
                return Path(java_home) / library
        raise OpenRocketUnavailable(f"JAVA_HOME is {java_home}, but there's no Java runtime in it")
    try:
        import jpype

        return Path(jpype.getDefaultJVMPath())
    except ImportError as error:
        raise OpenRocketUnavailable("jpype1 missing: pip install -e .[openrocket]") from error
    except Exception as error:  # JPype's JVMNotFoundException and friends
        raise OpenRocketUnavailable(
            f"this OpenRocket has no bundled Java, and no Java {MIN_JAVA}+ was found "
            f"({error}). Install one (for example Temurin {MIN_JAVA}) or set JAVA_HOME"
        ) from error


def java_version(jvm: Path) -> str | None:
    """JAVA_VERSION from the `release` file of the Java home that holds this JVM library."""
    for folder in list(jvm.parents)[:4]:
        release = folder / "release"
        if release.is_file():
            found = re.search(r'JAVA_VERSION="([^"]+)"', release.read_text(errors="replace"))
            return found.group(1) if found else None
    return None


def _check_java(jvm: Path) -> None:
    version = java_version(jvm)
    if version is None:
        return  # no release file: let the JVM start say if it's too old
    found = re.match(r"(?:1\.)?(\d+)", version)  # "1.8.0_292" is Java 8, "17.0.20" is 17
    if found is None:
        return
    major = int(found.group(1))
    if major < MIN_JAVA:
        raise OpenRocketUnavailable(
            f"OpenRocket needs Java {MIN_JAVA} or newer; the Java at {jvm} is {version}. "
            f"Install Java {MIN_JAVA}+ and set JAVA_HOME to it"
        )


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
            # Small machines (3.5 GB, Claude Desktop open): with the defaults (G1) the start
            # can fail with "paging file too small". Serial GC, a small starting heap and
            # handing free heap back cut peak commit for a judge run from 727 to 431 MB
            # (2026-10-12, v2). Flight results are identical to the last digit.
            "-XX:+UseSerialGC",
            "-Xms64m",
            "-XX:MinHeapFreeRatio=10",
            "-XX:MaxHeapFreeRatio=30",
            classpath=[str(install.jar)],
            convertStrings=True,
        )
    finally:
        # Only Windows gets it back. On Linux and macOS the JVM uses SIGSEGV for its own work
        # (null checks, safepoints); faulthandler would take that signal over and the first
        # harmless fault would abort the process (seen on CI, 2026-11-01). Windows uses
        # vectored exception handling instead of signals, so there it's safe.
        if faulthandler_was_on and sys.platform == "win32":
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


@dataclass(frozen=True)
class Outline:
    """One drawn shape of the side view, in metres: x from the nose tip, y up from the axis."""

    role: str  # "body", "fin", "internal" or "motor"
    kind: str  # OpenRocket class name, e.g. "NoseCone", or "Motor"
    name: str
    comment: str  # the component's comment in OpenRocket ("" if none)
    mass: float  # kg, this component alone as OpenRocket computes it
    points: list[tuple[float, float]]  # closed polygon


@dataclass(frozen=True)
class FinGeometry:
    name: str
    count: int
    root_chord: float  # m
    tip_chord: float  # m
    height: float  # m (span)
    sweep: float  # m, leading-edge sweep length
    front_x: float  # m from the nose tip to the root leading edge


@dataclass(frozen=True)
class SideView:
    """A to-scale side view of the selected configuration, every number from OpenRocket's model.

    Fins are projected the way OpenRocket's own side view draws them: each fin instance's span is
    scaled by the cosine of its roll angle: a 3-fin set shows one full fin up, two half fins down.
    """

    backend: str
    length: float  # m
    max_radius: float  # m
    outlines: list[Outline]
    fins: list[FinGeometry]
    motors: list[str]  # designations
    static: StaticAnalysis
    not_drawn: list[str]  # components the side view doesn't draw yet, "Name (Kind)"
    # The same geometry for a 3D view (JSON-ready, metres): bodies of revolution as (x, r) profiles,
    # fin sets as one outline plus per-instance (x, root radius, roll angle), parts and motors as
    # cylinders on their own axis offsets.
    model: dict = field(default_factory=dict)


BODY_SAMPLES = 48  # radius samples along a nose cone or transition


def side_view(path: str | Path) -> SideView:
    """Geometry for a side-view drawing, from OpenRocket's loaded model of the file."""
    install = start()
    import jpype

    static = static_analysis(path)
    rocket = _load_document(path).getRocket()
    config = rocket.getSelectedConfiguration()
    symmetric = jpype.JClass("info.openrocket.core.rocketcomponent.SymmetricComponent")
    finset = jpype.JClass("info.openrocket.core.rocketcomponent.FinSet")
    trapezoid = jpype.JClass("info.openrocket.core.rocketcomponent.TrapezoidFinSet")
    mass_object = jpype.JClass("info.openrocket.core.rocketcomponent.MassObject")
    ring = jpype.JClass("info.openrocket.core.rocketcomponent.RingComponent")
    assembly = jpype.JClass("info.openrocket.core.rocketcomponent.ComponentAssembly")
    rocket_class = jpype.JClass("info.openrocket.core.rocketcomponent.Rocket")

    outlines, fins, not_drawn = [], [], []
    model: dict = {"bodies": [], "fin_sets": [], "parts": [], "motors": []}
    for c in rocket:
        if isinstance(c, (rocket_class, assembly)):
            continue  # containers (rocket, stages) have no shape of their own
        name, kind = str(c.getName()).strip(), str(c.getClass().getSimpleName())
        comment = str(c.getComment() or "").strip()
        mass = float(c.getComponentMass())
        location = c.getComponentLocations()[0]
        x0 = float(location.x)
        if isinstance(c, symmetric):
            length = float(c.getLength())
            samples = 2 if kind == "BodyTube" else BODY_SAMPLES
            xs = [length * i / samples for i in range(samples + 1)]
            upper = [(x0 + x, float(c.getRadius(x))) for x in xs]
            lower = [(x, -y) for x, y in reversed(upper)]
            outlines.append(Outline("body", kind, name, comment, mass, upper + lower))
            model["bodies"].append(
                {
                    "name": name,
                    "kind": kind,
                    "comment": comment,
                    "mass": mass,
                    "profile": [[x, r] for x, r in upper],
                }
            )
        elif isinstance(c, finset):
            points = [(float(p.x), float(p.y)) for p in c.getFinPoints()]
            instances = []
            for loc in c.getComponentLocations():
                angle = math.atan2(float(loc.z), float(loc.y))
                root = math.hypot(float(loc.y), float(loc.z))
                projected = [
                    (float(loc.x) + px, (root + py) * math.cos(angle)) for px, py in points
                ]
                outlines.append(Outline("fin", kind, name, comment, mass, projected))
                instances.append([float(loc.x), root, angle])
            model["fin_sets"].append(
                {
                    "name": name,
                    "kind": kind,
                    "comment": comment,
                    "mass": mass,
                    "thickness": float(c.getThickness()),
                    "points": [list(pt) for pt in points],
                    "instances": instances,
                }
            )
            if isinstance(c, trapezoid):
                fins.append(
                    FinGeometry(
                        name,
                        int(c.getFinCount()),
                        float(c.getRootChord()),
                        float(c.getTipChord()),
                        float(c.getHeight()),
                        float(c.getSweep()),
                        x0,
                    )
                )
        elif isinstance(c, (mass_object, ring)):
            length = float(c.getLength())
            radius = float(c.getRadius() if isinstance(c, mass_object) else c.getOuterRadius())
            y0 = float(location.y)
            box = [
                (x0, y0 + radius),
                (x0 + length, y0 + radius),
                (x0 + length, y0 - radius),
                (x0, y0 - radius),
            ]
            outlines.append(Outline("internal", kind, name, comment, mass, box))
            model["parts"].append(
                {
                    "name": name,
                    "kind": kind,
                    "comment": comment,
                    "mass": mass,
                    "x0": x0,
                    "length": length,
                    "radius": radius,
                    "y": y0,
                    "z": float(location.z),
                }
            )
        else:
            not_drawn.append(f"{name} ({kind})")

    designations = []
    for mc in config.getActiveMotors():
        motor, mount = mc.getMotor(), mc.getMount()
        offset = mount.getMotorPosition(config.getId())
        x0 = float(mount.getComponentLocations()[0].x) + float(offset.x)
        length, radius = float(motor.getLength()), float(motor.getDiameter()) / 2
        box = [(x0, radius), (x0 + length, radius), (x0 + length, -radius), (x0, -radius)]
        designation = str(motor.getDesignation())
        launch_mass = float(motor.getLaunchMass())
        outlines.append(Outline("motor", "Motor", designation, "", launch_mass, box))
        model["motors"].append(
            {"name": designation, "mass": launch_mass, "x0": x0, "length": length, "radius": radius}
        )
        designations.append(designation)

    body = [o for o in outlines if o.role == "body"]
    return SideView(
        backend=install.backend,
        length=max(x for o in body for x, _ in o.points),
        max_radius=max(abs(y) for o in body for _, y in o.points),
        outlines=outlines,
        fins=fins,
        motors=designations,
        static=static,
        not_drawn=not_drawn,
        model=model,
    )


@dataclass(frozen=True)
class MotorCurve:
    """One thrust curve in OpenRocket's motor database."""

    manufacturer: str  # as an .ork stores it, e.g. "Cesaroni Technology"
    designation: str  # e.g. "282H399-12A"
    common_name: str  # e.g. "H399"
    total_impulse: float  # N*s, OpenRocket's integration of this curve
    average_thrust: float  # N
    diameter: float  # m
    length: float  # m
    digest: str
    delays: list[float]  # s, the manufacturer's standard delays
    case: str
    type: str  # as an .ork stores it, e.g. "reload"


def find_motors(designation: str) -> list[MotorCurve]:
    """Every curve in OpenRocket's database whose designation matches, ignoring the delay suffix.

    "282H399" matches "282H399-12A"; a full "282H399-12A" matches only that.
    """
    start()
    import jpype

    wanted = designation.strip().upper()
    base = wanted.split("-")[0]
    database = jpype.JClass("info.openrocket.core.startup.Application").getMotorSetDatabase()
    curves = []
    for motor_set in database.getMotorSets():
        for m in motor_set.getMotors():
            name = str(m.getDesignation()).upper()
            if name == wanted or (wanted == base and name.split("-")[0] == base):
                curves.append(
                    MotorCurve(
                        manufacturer=str(m.getManufacturer().getSimpleName()),
                        designation=str(m.getDesignation()),
                        common_name=str(m.getCommonName()),
                        total_impulse=float(m.getTotalImpulseEstimate()),
                        average_thrust=float(m.getAverageThrustEstimate()),
                        diameter=float(m.getDiameter()),
                        length=float(m.getLength()),
                        digest=str(m.getDigest()),
                        delays=[float(d) for d in m.getStandardDelays()],
                        case=str(m.getCaseInfo()),
                        type=str(m.getMotorType().name()).lower(),
                    )
                )
    return curves


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
    "theta": "TYPE_ORIENTATION_THETA",  # rad, zenith angle of the rocket's axis
    "phi": "TYPE_ORIENTATION_PHI",  # rad, azimuth of the rocket's axis
    "roll_rate": "TYPE_ROLL_RATE",  # rad/s
}


@dataclass(frozen=True)
class LaunchConditions:
    """The conditions a sim was run under. Snapshot of SimulationOptions."""

    rod_length: float  # m
    rod_angle: float  # rad
    rod_direction: float  # rad, OpenRocket's launch rod direction
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
    source: str = ""  # the component that caused it, e.g. the recovery device deployed


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
        events=[
            FlightEvent(
                float(e.getTime()),
                str(e.getType()),
                str(e.getSource().getName()) if e.getSource() is not None else "",
            )
            for e in branch.getEvents()
        ],
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
        rod_direction=float(options.getLaunchRodDirection()),
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

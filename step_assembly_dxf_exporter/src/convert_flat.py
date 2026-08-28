"""
Extracts the cuttable profile from a flat part: the outer boundary wire plus
any inner wires (holes/cutouts) of the dominant planar face identified by
classify.py.

Returns geometry in a simple, DXF-agnostic intermediate form (lists of 2D
polyline points and arc/circle primitives) so dxf_writer.py doesn't need to
know anything about OpenCASCADE, and so this module could in principle feed
other output formats later.
"""

from dataclasses import dataclass
import math

from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_WIRE, TopAbs_REVERSED
from OCC.Core.TopoDS import TopoDS_Shape, topods
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepTools import BRepTools_WireExplorer, breptools
from OCC.Core.BRep import BRep_Tool
from OCC.Core.GeomAbs import GeomAbs_Line, GeomAbs_Circle
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.gp import gp_Pnt, gp_Dir
from OCC.Core.GCPnts import GCPnts_QuasiUniformDeflection


@dataclass
class LineSeg:
    p1: tuple  # (x, y)
    p2: tuple


@dataclass
class ArcSeg:
    center: tuple
    radius: float
    start_angle_deg: float
    end_angle_deg: float


@dataclass
class CircleSeg:
    center: tuple
    radius: float


@dataclass
class FlatProfile:
    plane_origin: tuple      # 3D point, for reference/traceability
    plane_normal: tuple
    outer_loop: list         # list of LineSeg / ArcSeg
    hole_loops: list[list]   # list of loops, each a list of LineSeg / ArcSeg / [CircleSeg]


TOL = 1e-6

# Deflection tolerance for tessellating non-line, non-circle curves (splines,
# ellipses, Bezier curves -- common in text/logo/emblem features) into a
# sequence of straight segments. Same units as the model (mm, for most
# fixture-plate STEP files). Smaller = more accurate but more DXF segments;
# 0.01mm is a reasonable default for laser/CNC-scale features. If a part has
# especially fine or especially large curved features, this may need tuning.
TESSELLATION_DEFLECTION = 0.01


def _tessellate_to_2d_segments(curve_adaptor, first_p: float, last_p: float,
                                project_fn, deflection: float = TESSELLATION_DEFLECTION) -> list:
    """
    Approximates any curve type (spline, ellipse, Bezier, etc.) as a chain
    of straight LineSeg segments by sampling points along it with adaptive
    deflection-based spacing (more points where the curve bends more).

    This replaces naively connecting just the edge's two endpoints with a
    single straight line -- which silently turns any curved feature that
    isn't a perfect circular arc (a very common case for text/logo/emblem
    outlines, which are frequently built from splines even when they look
    like simple rounded shapes) into a crude polygon.
    """
    try:
        discretizer = GCPnts_QuasiUniformDeflection(curve_adaptor, deflection, first_p, last_p)
        if discretizer.IsDone() and discretizer.NbPoints() >= 2:
            n = discretizer.NbPoints()
            pts_3d = [discretizer.Value(i) for i in range(1, n + 1)]
        else:
            pts_3d = [curve_adaptor.Value(first_p), curve_adaptor.Value(last_p)]
    except Exception:
        pts_3d = [curve_adaptor.Value(first_p), curve_adaptor.Value(last_p)]

    pts_2d = [project_fn(p) for p in pts_3d]
    return [LineSeg(p1=a, p2=b) for a, b in zip(pts_2d, pts_2d[1:])]


def _outward_normal(face) -> "gp_Dir":
    """Get the face's true outward normal, accounting for topological
    orientation (a REVERSED face has its geometric surface normal pointing
    into the solid, not out of it)."""
    surf = BRepAdaptor_Surface(face, True)
    plane = surf.Plane()
    normal = plane.Axis().Direction()
    if face.Orientation() == TopAbs_REVERSED:
        normal = normal.Reversed()
    return normal


def _find_dominant_planar_face(shape: TopoDS_Shape, up_axis: "gp_Dir" = None):
    """
    Finds the planar face to extract the cut profile from.

    IMPORTANT: direction is decided BEFORE area, not after. An earlier
    version of this function picked by largest area first and only used
    facing direction as a tiebreak for exact ties -- which silently breaks
    the moment the two faces have genuinely different areas, e.g. a
    counterbore or larger clearance hole on one side removes more material
    from that face than the other. That's an extremely common case, and it
    meant the "pick the top face" logic never actually ran for any
    front/back-asymmetric part -- area alone decided it, regardless of
    which way the part was facing.

    Correct approach: first find the plate's two "cap" faces (planar faces
    whose normal is nearly parallel to up_axis, one pointing each way, large
    enough to plausibly be a main face rather than a chamfer/edge sliver).
    Between those two, pick the one actually facing up_axis. Area only
    breaks ties among faces already agreed to be on the correct side (e.g.
    a stepped/multi-level top).
    """
    if up_axis is None:
        up_axis = gp_Dir(0, 0, 1)

    candidates = []  # (area, face, surf, normal)
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = topods.Face(exp.Current())
        surf = BRepAdaptor_Surface(face, True)
        if surf.GetType() == GeomAbs_Plane:
            props = GProp_GProps()
            brepgprop.SurfaceProperties(face, props)
            area = props.Mass()
            normal = _outward_normal(face)
            candidates.append((area, face, surf, normal))
        exp.Next()

    if not candidates:
        raise RuntimeError("No planar face found on shape classified as flat.")

    max_area = max(c[0] for c in candidates)

    # "Cap" faces: nearly parallel (or anti-parallel) to up_axis, and not a
    # tiny sliver relative to the largest planar face (excludes edge
    # chamfers / small side faces that happen to be flat).
    ALIGN_TOL = 0.9          # cos(~25 degrees)
    AREA_FRACTION = 0.15
    cap_candidates = [
        c for c in candidates
        if abs(c[3].Dot(up_axis)) >= ALIGN_TOL and c[0] >= AREA_FRACTION * max_area
    ]

    if not cap_candidates:
        # Nothing lines up with up_axis at all -- likely a genuinely
        # inclined part with no meaningful "top" in this convention. Fall
        # back to largest area rather than failing outright.
        cap_candidates = candidates

    # Direction first: prefer faces actually facing up_axis (positive dot).
    facing = [c for c in cap_candidates if c[3].Dot(up_axis) > 0]
    pool = facing if facing else cap_candidates

    # Area only as a tiebreak within the chosen direction.
    _, face, surf, _ = max(pool, key=lambda c: c[0])

    return face, surf


def _project_to_plane_2d(pnt: gp_Pnt, origin: gp_Pnt, x_dir, y_dir):
    dx = pnt.X() - origin.X()
    dy = pnt.Y() - origin.Y()
    dz = pnt.Z() - origin.Z()
    u = dx * x_dir.X() + dy * x_dir.Y() + dz * x_dir.Z()
    v = dx * y_dir.X() + dy * y_dir.Y() + dz * y_dir.Z()
    return (u, v)


def _arc_angles_from_geometry(center2d, p_start2d, p_end2d, p_mid2d) -> tuple:
    """
    Determine DXF-correct (start_angle, end_angle) for an ARC purely from
    the actual 2D geometry (start point, end point, and a point partway
    along the arc), rather than trusting OpenCASCADE's raw curve parameter
    as an angle.

    Why this is necessary: BRepAdaptor_Curve's parameter for a circular
    edge is an angle *in the circle's own local coordinate frame*
    (gp_Circ's XDirection/YDirection/Axis), which is generally NOT aligned
    with the 2D drawing frame (x_dir, y_dir) this module projects into.
    Using the raw parameter directly produces angles that are correct in
    magnitude but can point the wrong way once reinterpreted in the
    drawing frame -- which reads as "arcs are mirrored/inverted" while
    circles (which have no direction) look completely fine. Sampling the
    actual midpoint sidesteps the frame mismatch entirely: DXF's ARC
    convention (sweep counterclockwise from start_angle to end_angle) is
    satisfied by construction once we pick whichever ordering puts the
    midpoint's angle on the CCW arc between them.
    """
    import math

    def ang(p):
        return math.degrees(math.atan2(p[1] - center2d[1], p[0] - center2d[0])) % 360.0

    a_start = ang(p_start2d)
    a_end = ang(p_end2d)
    a_mid = ang(p_mid2d)

    span = (a_end - a_start) % 360.0
    pos = (a_mid - a_start) % 360.0

    if span > 1e-6 and pos <= span + 1e-6:
        return a_start, a_end
    else:
        return a_end, a_start


def _wire_to_loop(wire, origin, x_dir, y_dir) -> list:
    """Walk a wire's edges in order, projecting each to 2D plane coordinates."""
    loop = []
    wire_exp = BRepTools_WireExplorer(wire)
    while wire_exp.More():
        edge = wire_exp.Current()
        curve_adaptor = BRepAdaptor_Curve(edge)
        ctype = curve_adaptor.GetType()

        first_p, last_p = curve_adaptor.FirstParameter(), curve_adaptor.LastParameter()
        p_start = curve_adaptor.Value(first_p)
        p_end = curve_adaptor.Value(last_p)
        p1 = _project_to_plane_2d(p_start, origin, x_dir, y_dir)
        p2 = _project_to_plane_2d(p_end, origin, x_dir, y_dir)

        if ctype == GeomAbs_Circle:
            circ = curve_adaptor.Circle()
            center3d = circ.Location()
            center2d = _project_to_plane_2d(center3d, origin, x_dir, y_dir)
            radius = circ.Radius()
            full_circle = abs((last_p - first_p) - 2 * math.pi) < 1e-4
            if full_circle:
                loop.append(CircleSeg(center=center2d, radius=radius))
            else:
                mid_p = (first_p + last_p) / 2.0
                p_mid = curve_adaptor.Value(mid_p)
                p_mid2d = _project_to_plane_2d(p_mid, origin, x_dir, y_dir)
                start_ang, end_ang = _arc_angles_from_geometry(center2d, p1, p2, p_mid2d)
                loop.append(ArcSeg(center=center2d, radius=radius,
                                    start_angle_deg=start_ang, end_angle_deg=end_ang))
        else:
            if ctype == GeomAbs_Line:
                loop.append(LineSeg(p1=p1, p2=p2))
            else:
                # Any other curve type -- spline, ellipse, Bezier, etc.
                # (common for text/logo/emblem outlines). Tessellate into
                # multiple straight segments rather than crudely connecting
                # just the two endpoints, which would collapse a curved
                # feature into a straight edge (e.g. turning a rounded slot
                # into a plain rectangle).
                loop.extend(_tessellate_to_2d_segments(
                    curve_adaptor, first_p, last_p,
                    lambda p: _project_to_plane_2d(p, origin, x_dir, y_dir),
                ))

        wire_exp.Next()

    return loop


def extract_flat_profile(shape: TopoDS_Shape, up_axis: gp_Dir = None) -> FlatProfile:
    """
    up_axis: which global direction counts as "up" when choosing between a
    flat part's two parallel faces (see _find_dominant_planar_face). Defaults
    to global +Z, the standard convention for parts modeled lying flat on a
    table/cutting bed. Pass gp_Dir(0, 0, -1) to flip the convention for an
    assembly modeled upside-down relative to that default.
    """
    face, surf = _find_dominant_planar_face(shape, up_axis=up_axis)
    plane = surf.Plane()
    origin = plane.Location()
    x_dir = plane.XAxis().Direction()
    y_dir = plane.YAxis().Direction()
    normal = plane.Axis().Direction()

    outer_wire = breptools.OuterWire(face)

    outer_loop = _wire_to_loop(outer_wire, origin, x_dir, y_dir)

    hole_loops = []
    wire_exp = TopExp_Explorer(face, TopAbs_WIRE)
    while wire_exp.More():
        wire = topods.Wire(wire_exp.Current())
        if not wire.IsSame(outer_wire):
            hole_loops.append(_wire_to_loop(wire, origin, x_dir, y_dir))
        wire_exp.Next()

    return FlatProfile(
        plane_origin=(origin.X(), origin.Y(), origin.Z()),
        plane_normal=(normal.X(), normal.Y(), normal.Z()),
        outer_loop=outer_loop,
        hole_loops=hole_loops,
    )

"""
For parts that aren't flat stock, generate a true orthographic projection
via OpenCASCADE's hidden-line-removal algorithm (HLRBRep_Algo) -- the same
underlying technique CAD packages use for 2D drawing views generated from
3D models (e.g. SolidWorks drawing views, Fusion's "Create Drawing").

Produces visible + hidden edge sets for a chosen view direction, pulling
from all of HLR's real-geometry edge categories (sharp edges, tangent-face
edges, and curved-surface silhouettes -- see the comments in project_shape
for why all three matter). Default export uses only the visible-edge set
flattened to 2D, which is the usual expectation for a DXF meant for
reference/cutting rather than a full engineering drawing with hidden-line
dashes; hidden edges are still returned in case the caller wants a
"front + top + side" style multi-view drawing with dashed hidden lines on
separate DXF layers.
"""

from dataclasses import dataclass
from enum import Enum
import math

from OCC.Core.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCC.Core.HLRAlgo import HLRAlgo_Projector
from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.GeomAbs import GeomAbs_Circle, GeomAbs_Line

from .convert_flat import LineSeg, ArcSeg, CircleSeg, _arc_angles_from_geometry, _tessellate_to_2d_segments


class ViewDirection(Enum):
    FRONT = "front"     # eye in front of the part (+Y side)
    TOP = "top"          # eye above the part (viewed from above)
    BOTTOM = "bottom"     # eye below the part (viewed from below)
    RIGHT = "right"        # eye to the right of the part (+X side)


# IMPORTANT: HLRAlgo_Projector's axis direction is NOT the "look" direction
# from the eye into the scene (the intuitive assumption) -- it's the
# direction FROM the scene TOWARD the eye. This is confirmed by OpenCASCADE's
# own forum guidance for building a projector from camera parameters:
# `aBackDir = -theProjector->Direction()` is used as the HLR axis, where
# `theProjector->Direction()` is the camera's forward/look vector. So the
# HLR axis is the *back* direction relative to the camera's view -- i.e. it
# points at the eye. Getting this backwards doesn't error out; it silently
# swaps which side's geometry counts as "visible", which is a much harder
# bug to notice than a crash.
_VIEW_DIRS = {
    ViewDirection.FRONT: gp_Dir(0, 1, 0),
    ViewDirection.TOP: gp_Dir(0, 0, 1),
    ViewDirection.BOTTOM: gp_Dir(0, 0, -1),
    ViewDirection.RIGHT: gp_Dir(1, 0, 0),
}


@dataclass
class ProjectionResult:
    view: ViewDirection
    visible: list       # LineSeg / ArcSeg / CircleSeg, already in 2D view coords
    hidden: list


def _edges_to_segments(shape: TopoDS_Shape) -> list:
    segs = []
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        edge = exp.Current()
        curve_adaptor = BRepAdaptor_Curve(edge)
        first_p, last_p = curve_adaptor.FirstParameter(), curve_adaptor.LastParameter()
        p_start = curve_adaptor.Value(first_p)
        p_end = curve_adaptor.Value(last_p)

        if curve_adaptor.GetType() == GeomAbs_Circle:
            circ = curve_adaptor.Circle()
            center = circ.Location()
            radius = circ.Radius()
            full_circle = abs((last_p - first_p) - 2 * math.pi) < 1e-4
            if full_circle:
                segs.append(CircleSeg(center=(center.X(), center.Y()), radius=radius))
            else:
                mid_p = (first_p + last_p) / 2.0
                p_mid = curve_adaptor.Value(mid_p)
                start_ang, end_ang = _arc_angles_from_geometry(
                    (center.X(), center.Y()),
                    (p_start.X(), p_start.Y()),
                    (p_end.X(), p_end.Y()),
                    (p_mid.X(), p_mid.Y()),
                )
                segs.append(ArcSeg(center=(center.X(), center.Y()), radius=radius,
                                    start_angle_deg=start_ang, end_angle_deg=end_ang))
        else:
            if curve_adaptor.GetType() == GeomAbs_Line:
                segs.append(LineSeg(p1=(p_start.X(), p_start.Y()), p2=(p_end.X(), p_end.Y())))
            else:
                # Spline, ellipse, Bezier, etc. -- tessellate rather than
                # collapsing to a single straight segment (see convert_flat.py).
                segs.extend(_tessellate_to_2d_segments(
                    curve_adaptor, first_p, last_p,
                    lambda p: (p.X(), p.Y()),
                ))

        exp.Next()
    return segs


def _collect_segments(*shapes) -> list:
    """Runs _edges_to_segments across multiple HLR output compounds and
    concatenates the results, skipping any that are null/empty (HLR leaves
    a category's compound null when a shape has no edges of that type,
    which is the normal case, not an error)."""
    segs = []
    for shape in shapes:
        if shape is not None and not shape.IsNull():
            segs.extend(_edges_to_segments(shape))
    return segs


def project_shape(shape: TopoDS_Shape, view: ViewDirection = ViewDirection.FRONT) -> ProjectionResult:
    direction = _VIEW_DIRS[view]
    # HLR needs a full coordinate system for the projector, not just a
    # direction -- the reference "up" vector must not be parallel to the
    # view direction, which rules out the default (0,0,1) for any view
    # that's itself along Z.
    up = gp_Dir(0, 1, 0) if view in (ViewDirection.TOP, ViewDirection.BOTTOM) else gp_Dir(0, 0, 1)
    ax = gp_Ax2(gp_Pnt(0, 0, 0), direction, up)
    projector = HLRAlgo_Projector(ax)

    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(projector)
    algo.Update()
    algo.Hide()

    hlr_shapes = HLRBRep_HLRToShape(algo)

    # HLR classifies edges into several distinct buckets, and only reading
    # VCompound/HCompound ("sharp" edges -- real topological edges with a
    # genuine discontinuity) silently drops two other real geometry types:
    #   - OutLineVCompound/HCompound: the *silhouette* outline of curved
    #     surfaces (cylinders, cones, spheres, fillets) where the surface
    #     curves away from the viewer. There's no real topological edge
    #     there at all -- HLR computes it -- which is exactly why round
    #     features were coming out with missing boundary lines.
    #   - Rg1LineV/HCompound: edges between tangent-continuous (G1) faces,
    #     e.g. where a fillet blends smoothly into a flat face.
    # Deliberately NOT including IsoLineV/HCompound -- those are arbitrary
    # isoparametric lines added for visualization only, not real edges of
    # the part, and would add incorrect extra lines to a cutting DXF.
    visible_segs = _collect_segments(
        hlr_shapes.VCompound(),
        hlr_shapes.Rg1LineVCompound(),
        hlr_shapes.RgNLineVCompound(),
        hlr_shapes.OutLineVCompound(),
    )
    hidden_segs = _collect_segments(
        hlr_shapes.HCompound(),
        hlr_shapes.Rg1LineHCompound(),
        hlr_shapes.RgNLineHCompound(),
        hlr_shapes.OutLineHCompound(),
    )

    return ProjectionResult(view=view, visible=visible_segs, hidden=hidden_segs)
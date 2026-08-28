"""
Decides, per unique part, whether to route it through the flat-pattern
extraction path or the hidden-line-removal projection path.

Heuristic (deliberately simple and inspectable, since the agent layer will
review/override borderline cases rather than trusting a black box):

1. Bounding-box aspect ratio: if the smallest dimension is much smaller than
   the other two (default: < 8% of the max in-plane dimension), the part is
   "thin" -- a strong sheet-metal/flat-stock signal.
2. Planar-face dominance: sum the area of all planar faces whose normal is
   parallel (within tolerance) to the thin axis, and compare to the total
   bounding-box footprint area. If one dominant planar face covers most of
   the footprint, the part is confidently flat.

Parts that are thin but don't have one dominant flat face (e.g. a bent
sheet-metal bracket with two panels at an angle) are flagged as
"ambiguous" rather than forced down either path -- these are exactly the
cases the agent layer should surface to the user, since real unfolding
requires a flat-pattern/unfold algorithm this tool doesn't implement
(see README limitations).
"""

from dataclasses import dataclass
from enum import Enum

from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopoDS import TopoDS_Shape, topods
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop

THIN_RATIO_THRESHOLD = 0.08     # thin-axis dim / max in-plane dim
DOMINANT_FACE_COVERAGE = 0.6    # fraction of footprint one planar face must cover


class PartClass(Enum):
    FLAT = "flat"
    PROJECTION = "projection"
    AMBIGUOUS = "ambiguous"


@dataclass
class Classification:
    part_class: PartClass
    thin_axis: int | None      # 0/1/2 for x/y/z, or None
    confidence: str            # "high" / "medium" / "low" -- for the agent layer to weigh
    reason: str


def _bbox_dims(shape: TopoDS_Shape):
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    return [xmax - xmin, ymax - ymin, zmax - zmin]


def classify_part(shape: TopoDS_Shape) -> Classification:
    dims = _bbox_dims(shape)
    thin_axis = dims.index(min(dims))
    thin_dim = dims[thin_axis]
    in_plane_dims = [d for i, d in enumerate(dims) if i != thin_axis]
    max_in_plane = max(in_plane_dims) if in_plane_dims else 0.0

    if max_in_plane == 0:
        return Classification(PartClass.AMBIGUOUS, None, "low", "Degenerate bounding box.")

    ratio = thin_dim / max_in_plane
    if ratio > THIN_RATIO_THRESHOLD:
        return Classification(
            PartClass.PROJECTION, None, "high",
            f"Roughly cubic/chunky part (thin-axis ratio {ratio:.2f}); not flat stock.",
        )

    # Thin part -- check for one dominant planar face aligned to the thin axis
    footprint_area = 1.0
    for i, d in enumerate(dims):
        if i != thin_axis:
            footprint_area *= d

    best_face_area = 0.0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = topods.Face(exp.Current())
        surf = BRepAdaptor_Surface(face, True)
        if surf.GetType() == GeomAbs_Plane:
            props = GProp_GProps()
            brepgprop.SurfaceProperties(face, props)
            area = props.Mass()
            if area > best_face_area:
                best_face_area = area
        exp.Next()

    coverage = best_face_area / footprint_area if footprint_area > 0 else 0.0

    if coverage >= DOMINANT_FACE_COVERAGE:
        return Classification(
            PartClass.FLAT, thin_axis, "high",
            f"Thin part (ratio {ratio:.2f}) with a dominant planar face covering {coverage:.0%} of footprint.",
        )

    return Classification(
        PartClass.AMBIGUOUS, thin_axis, "low",
        f"Thin part (ratio {ratio:.2f}) but no single dominant planar face (best coverage {coverage:.0%}) "
        "-- may be bent/folded sheet metal needing unfold, not a flat blank.",
    )

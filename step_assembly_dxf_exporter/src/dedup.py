"""
Collapses repeated instances of the same part into one unique part + an
instance count -- mirroring what SolidWorks/Inventor do when they export a
BOM or batch DXFs from an assembly (20 identical brackets -> 1 DXF, not 20).

Approach: compute a cheap, transform-independent geometric signature per
shape (volume, surface area, bounding-box dimensions sorted, face/edge/
vertex counts). Two shapes with matching signatures within tolerance are
treated as the same part. This is a heuristic, not a proof of exact
congruence -- it will occasionally merge two genuinely different parts that
happen to share mass properties (rare in practice) or fail to merge two
parts that differ only by a feature too small to move the signature beyond
tolerance. For most laser/sheet-metal assemblies this is more than accurate
enough; if you need guaranteed-exact matching, add a fallback exact-shape
comparison (BRepAlgoAPI_Common / bounding volume hierarchy diff) for any
signatures that collide.
"""

from dataclasses import dataclass, field

from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
from OCC.Core.TopoDS import TopoDS_Shape

from .step_reader import PartInstance

ROUND_DP = 3  # decimal places for signature rounding -- tune per source CAD tolerance


@dataclass
class UniquePart:
    name: str
    shape: TopoDS_Shape
    instance_count: int = 1
    all_names_seen: list[str] = field(default_factory=list)


def _count_subshapes(shape: TopoDS_Shape, kind) -> int:
    exp = TopExp_Explorer(shape, kind)
    n = 0
    while exp.More():
        n += 1
        exp.Next()
    return n


def _signature(shape: TopoDS_Shape) -> tuple:
    vol_props = GProp_GProps()
    brepgprop.VolumeProperties(shape, vol_props)
    volume = vol_props.Mass()

    surf_props = GProp_GProps()
    brepgprop.SurfaceProperties(shape, surf_props)
    area = surf_props.Mass()

    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    dims = sorted([round(xmax - xmin, ROUND_DP),
                   round(ymax - ymin, ROUND_DP),
                   round(zmax - zmin, ROUND_DP)])

    n_faces = _count_subshapes(shape, TopAbs_FACE)
    n_edges = _count_subshapes(shape, TopAbs_EDGE)
    n_verts = _count_subshapes(shape, TopAbs_VERTEX)

    return (
        round(volume, ROUND_DP),
        round(area, ROUND_DP),
        tuple(dims),
        n_faces,
        n_edges,
        n_verts,
    )


def deduplicate(instances: list[PartInstance]) -> list[UniquePart]:
    buckets: dict[tuple, UniquePart] = {}

    for inst in instances:
        sig = _signature(inst.shape)
        if sig in buckets:
            buckets[sig].instance_count += 1
            buckets[sig].all_names_seen.append(inst.name)
        else:
            buckets[sig] = UniquePart(
                name=inst.name,
                shape=inst.shape,
                instance_count=1,
                all_names_seen=[inst.name],
            )

    return list(buckets.values())

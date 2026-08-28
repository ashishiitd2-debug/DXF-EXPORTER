"""
Writes the DXF-agnostic intermediate geometry (LineSeg/ArcSeg/CircleSeg from
convert_flat.py / convert_projection.py) out to an actual DXF file using
ezdxf.

All coordinates are in millimeters (see the note in write_projection_dxf on
why the DXF's $INSUNITS header is explicitly set to MM rather than left at
ezdxf's unitless default).

Layer convention:
  OUTLINE  - outer profile / visible edges
  HOLES    - inner hole loops (flat parts)
  HIDDEN   - hidden edges (projection views, opt-in only), dashed linetype
"""

import ezdxf

from .convert_flat import FlatProfile, LineSeg, ArcSeg, CircleSeg
from .convert_projection import ProjectionResult


def _add_loop(msp, loop, layer: str):
    for seg in loop:
        if isinstance(seg, LineSeg):
            msp.add_line(seg.p1, seg.p2, dxfattribs={"layer": layer})
        elif isinstance(seg, ArcSeg):
            msp.add_arc(
                center=seg.center,
                radius=seg.radius,
                start_angle=seg.start_angle_deg,
                end_angle=seg.end_angle_deg,
                dxfattribs={"layer": layer},
            )
        elif isinstance(seg, CircleSeg):
            msp.add_circle(center=seg.center, radius=seg.radius, dxfattribs={"layer": layer})
        else:
            raise TypeError(f"Unknown segment type: {type(seg)}")


def write_flat_profile_dxf(profile: FlatProfile, out_path: str):
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = ezdxf.units.MM  # see note in write_projection_dxf below
    doc.layers.add(name="OUTLINE", color=7)
    doc.layers.add(name="HOLES", color=1)
    msp = doc.modelspace()

    _add_loop(msp, profile.outer_loop, "OUTLINE")
    for hole in profile.hole_loops:
        _add_loop(msp, hole, "HOLES")

    doc.saveas(out_path)


def write_projection_dxf(result: ProjectionResult, out_path: str, include_hidden: bool = False):
    """
    include_hidden: whether to also write occluded (hidden-line) edges to a
    dashed HIDDEN layer. Default False -- for a cutting DXF you almost
    always want ONLY the geometry actually visible from that side; most DXF
    viewers show all layers by default, so including hidden edges makes an
    otherwise-opaque part look "transparent" (front and back features
    overlaid). Set True only when producing a documentation-style
    engineering drawing where dashed hidden lines are meant to be shown.
    """
    doc = ezdxf.new(dxfversion="R2010")
    # CRITICAL: all coordinates from step_reader.py / convert_flat.py /
    # convert_projection.py are in millimeters (confirmed by diagnostic --
    # OpenCASCADE's STEP import already normalizes to mm). But ezdxf.new()
    # defaults to an UNITLESS header ($INSUNITS = 0). Some CAD tools
    # (Fusion 360 in particular) default an unitless DXF import to METERS,
    # not millimeters -- so correct mm coordinates on disk get silently
    # reinterpreted as meters on read-back, a 1000x inflation. Declaring the
    # unit explicitly here is what fixes that regardless of which tool
    # opens the file.
    doc.units = ezdxf.units.MM
    doc.layers.add(name="OUTLINE", color=7)
    msp = doc.modelspace()

    _add_loop(msp, result.visible, "OUTLINE")

    if include_hidden:
        doc.layers.add(name="HIDDEN", color=8, linetype="DASHED")
        if "DASHED" not in doc.linetypes:
            doc.linetypes.add("DASHED", pattern="A,.5,-.25")
        _add_loop(msp, result.hidden, "HIDDEN")

    doc.saveas(out_path)

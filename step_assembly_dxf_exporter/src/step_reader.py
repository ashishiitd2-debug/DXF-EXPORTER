"""
Reads a STEP file's *assembly structure* (not just raw geometry) using
OpenCASCADE's XCAF document layer.

Plain `STEPControl_Reader` gives you a flattened TopoDS_Shape and loses the
product tree (component names, instance transforms, which solids are
repeated instances of the same part). `STEPCAFControl_Reader` reads into an
XCAF `TDocStd_Document`, which preserves that structure -- it's the same
representation SolidWorks/Inventor operate on internally when they open an
assembly STEP file.

IMPORTANT: this module's document-setup and traversal pattern is modeled
directly on pythonocc-core's own maintained reference implementation
(`read_step_file_with_names_colors` in src/Extend/DataExchange.py of the
pythonocc-core repo), not on older OCCT C++ tutorials. Two things matter here
and cost real debugging time to discover:

1. `TDocStd_Document` is constructed with a plain Python string, and NOT
   wrapped in `TCollection_ExtendedString`, and NOT created via
   `XCAFApp_Application.NewDocument()`. Going through the Application layer
   is a common pattern in C++ OCCT tutorials, but in pythonocc it's a known
   source of a `Standard_NullObject` abort that isn't even catchable as a
   Python exception (it terminates the process directly via libc++abi).
2. A reference/instance label's shape is NOT already positioned by its
   instance transform when you call `GetShape()` on it. You have to walk the
   assembly tree yourself, accumulate each level's `TopLoc_Location` via
   `GetLocation()`, and apply the composed transform with
   `BRepBuilderAPI_Transform` when you hit an actual simple shape. Skipping
   this doesn't crash -- it silently returns every instance sitting at the
   origin, which is a much worse bug to catch later.

Output of this module is a flat list of PartInstance objects: one per
*occurrence* in the assembly (so a bracket used 20 times yields 20 entries,
each already positioned by its accumulated instance transform).
Deduplication into unique parts happens downstream in dedup.py.
"""

import os
import sys
from dataclasses import dataclass, field

from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Core.TDF import TDF_LabelSequence, TDF_Label
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform


def _dbg(msg: str):
    # Left in deliberately (cheap, stderr-only): if something upstream of
    # this ever regresses again with a silent C++ abort, this is the fastest
    # way to see exactly how far execution got.
    print(f"[step_reader] {msg}", file=sys.stderr, flush=True)


@dataclass
class PartInstance:
    """One occurrence of a solid in the assembly tree, already positioned
    by its accumulated instance transform."""
    name: str
    shape: TopoDS_Shape
    label_entry: str
    parent_path: list = field(default_factory=list)


def read_step_assembly(step_path: str) -> list[PartInstance]:
    """
    Reads a STEP file and returns every leaf solid occurrence in the
    assembly, flattened, with names and transforms resolved.

    Works identically whether the STEP file is a single part (returns a
    list of length 1) or a deep multi-level assembly.
    """
    if not os.path.isfile(step_path):
        raise FileNotFoundError(f"STEP file not found: {step_path}")

    _dbg("constructing TDocStd_Document")
    doc = TDocStd_Document("pythonocc-xcaf-doc")

    _dbg("getting ShapeTool")
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)

    _dbg(f"calling ReadFile on {step_path!r}")
    status = reader.ReadFile(step_path)
    _dbg(f"ReadFile status = {status}")
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Failed to read STEP file: {step_path}")

    _dbg("calling Transfer")
    transfer_ok = reader.Transfer(doc)
    _dbg(f"Transfer done, ok = {transfer_ok}")
    if not transfer_ok:
        raise RuntimeError(f"Failed to transfer STEP contents into XCAF document: {step_path}")

    instances: list[PartInstance] = []
    locs: list[TopLoc_Location] = []
    unnamed_counter = {"n": 0}

    def next_fallback_name() -> str:
        unnamed_counter["n"] += 1
        return f"Part_{unnamed_counter['n']:03d}"

    def compose_location() -> TopLoc_Location:
        loc = TopLoc_Location()
        for l in locs:
            loc = loc.Multiplied(l)
        return loc

    def get_name(lab: TDF_Label) -> str:
        name = lab.GetLabelName()
        return name if name else next_fallback_name()

    def walk(lab: TDF_Label, parent_path: list[str]):
        name = get_name(lab)

        if shape_tool.IsAssembly(lab):
            comps = TDF_LabelSequence()
            shape_tool.GetComponents(lab, comps)
            for i in range(comps.Length()):
                comp_label = comps.Value(i + 1)
                if shape_tool.IsReference(comp_label):
                    ref_label = TDF_Label()
                    shape_tool.GetReferredShape(comp_label, ref_label)
                    loc = shape_tool.GetLocation(comp_label)
                    locs.append(loc)
                    walk(ref_label, parent_path + [name])
                    locs.pop()
                else:
                    walk(comp_label, parent_path + [name])

        elif shape_tool.IsSimpleShape(lab):
            shape = shape_tool.GetShape(lab)
            if shape is not None and not shape.IsNull():
                loc = compose_location()
                positioned_shape = BRepBuilderAPI_Transform(shape, loc.Transformation()).Shape()
                instances.append(PartInstance(
                    name=name,
                    shape=positioned_shape,
                    label_entry=str(lab.Tag()),
                    parent_path=parent_path,
                ))

        else:
            # Neither assembly nor simple shape (e.g. an empty/compound
            # label with sub-labels but no direct shape) -- recurse into
            # its components defensively rather than silently dropping it.
            comps = TDF_LabelSequence()
            shape_tool.GetComponents(lab, comps)
            for i in range(comps.Length()):
                walk(comps.Value(i + 1), parent_path + [name])

    free_labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_labels)
    _dbg(f"GetFreeShapes done, count = {free_labels.Length()}")

    for i in range(free_labels.Length()):
        walk(free_labels.Value(i + 1), [])

    if not instances:
        raise RuntimeError(
            f"No solids found in {step_path}. The file may be empty, "
            "contain only surfaces/curves, or use STEP features this reader doesn't traverse."
        )

    _dbg(f"total leaf instances found = {len(instances)}")
    return instances

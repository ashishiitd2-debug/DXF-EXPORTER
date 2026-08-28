#!/usr/bin/env python
"""
Diagnostic script -- NOT part of the main pipeline. Run this standalone to
figure out exactly where a 1000x (metre vs millimetre) scaling discrepancy
is coming from.

Usage:
    python diagnose_units.py "/path/to/file.step"

It checks, in order:
  1. The STEP file's own declared length unit (raw text search -- no OCCT
     needed for this part, so it works even if something upstream is broken).
  2. The bounding box size using a plain STEPControl_Reader (no XCAF/assembly
     handling -- the simplest possible read path).
  3. The current value of the "xstep.cascade.unit" setting that governs
     OpenCASCADE's automatic unit normalization on import.
  4. The bounding box size using the same STEPCAFControl_Reader + XCAF path
     this project's step_reader.py uses.

If (2) and (4) disagree by ~1000x, the bug is specifically in how the XCAF
path handles units. If they agree with each other but both disagree with the
real-world part size by 1000x, the bug is in the global unit setting itself
(affecting every reader), not specific to XCAF.
"""

import sys
import re

if len(sys.argv) != 2:
    print("Usage: python diagnose_units.py <path/to/file.step>")
    sys.exit(1)

step_path = sys.argv[1]

print("=" * 70)
print("STEP 1: Raw text search for declared length unit in the file")
print("=" * 70)
with open(step_path, "r", errors="ignore") as f:
    text = f.read()

unit_lines = [
    line for line in text.splitlines()
    if re.search(r"(LENGTH_UNIT|SI_UNIT|CONVERSION_BASED_UNIT|NAMED_UNIT)", line, re.IGNORECASE)
]
if unit_lines:
    for line in unit_lines[:15]:
        print(line.strip())
    if len(unit_lines) > 15:
        print(f"... ({len(unit_lines) - 15} more unit-related lines)")
else:
    print("No unit declaration lines found via text search (unusual -- "
          "the file may use a non-standard header, or units are implicit).")

print()
print("=" * 70)
print("STEP 2: Bounding box via plain STEPControl_Reader (no XCAF)")
print("=" * 70)
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.Interface import Interface_Static

print(f'Interface_Static "xstep.cascade.unit" = {Interface_Static.CVal("xstep.cascade.unit")}')

reader = STEPControl_Reader()
status = reader.ReadFile(step_path)
if status != IFSelect_RetDone:
    print("Plain STEPControl_Reader failed to read the file.")
else:
    reader.TransferRoots()
    n = reader.NbShapes()
    print(f"NbShapes = {n}")
    if n > 0:
        shape = reader.Shape(1)
        bbox = Bnd_Box()
        brepbndlib.Add(shape, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        print(f"Bounding box of shape 1: "
              f"dx={xmax - xmin:.3f}  dy={ymax - ymin:.3f}  dz={zmax - zmin:.3f}")
        print("(if these numbers look like real-world mm for your part, "
              "this path is correct; if they're 1000x too big/small, the "
              "global unit setting itself is the problem)")

print()
print("=" * 70)
print("STEP 3: Bounding box via STEPCAFControl_Reader + XCAF (this project's path)")
print("=" * 70)
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Core.TDF import TDF_LabelSequence

doc = TDocStd_Document("diagnostic-doc")
shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

caf_reader = STEPCAFControl_Reader()
caf_reader.SetColorMode(True)
caf_reader.SetNameMode(True)
caf_reader.SetLayerMode(True)

status = caf_reader.ReadFile(step_path)
if status != IFSelect_RetDone:
    print("STEPCAFControl_Reader failed to read the file.")
else:
    caf_reader.Transfer(doc)
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    print(f"Free shape labels at root: {free_shapes.Length()}")

    if free_shapes.Length() > 0:
        first_label = free_shapes.Value(1)
        shape = shape_tool.GetShape(first_label)
        if shape is not None and not shape.IsNull():
            bbox = Bnd_Box()
            brepbndlib.Add(shape, bbox)
            xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
            print(f"Bounding box of first free shape: "
                  f"dx={xmax - xmin:.3f}  dy={ymax - ymin:.3f}  dz={zmax - zmin:.3f}")

print()
print("=" * 70)
print("Compare the dx/dy/dz numbers from STEP 2 and STEP 3 above against")
print("the real-world size of your part (or the correct Fusion dimensions).")
print("=" * 70)

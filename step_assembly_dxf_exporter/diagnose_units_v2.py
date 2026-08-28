#!/usr/bin/env python
"""
Diagnostic v2 -- NOT part of the main pipeline. Run from the project root
(same folder as cli.py) so the `src` package resolves.

Usage:
    python diagnose_units_v2.py "/path/to/file.step"

Uses this project's REAL read_step_assembly() (the exact code path the
pipeline uses) and prints the bounding box of every leaf part it finds,
BEFORE any dedup/classify/convert/DXF-writing happens. This tells us
whether a specific part is already wrong immediately after STEP reading
(pointing at a per-component unit-context bug in step_reader.py) or whether
it's still correct here (pointing at a bug further downstream in
convert_flat.py / convert_projection.py / dxf_writer.py instead).
"""

import sys

if len(sys.argv) != 2:
    print("Usage: python diagnose_units_v2.py <path/to/file.step>")
    sys.exit(1)

step_path = sys.argv[1]

from src.step_reader import read_step_assembly
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib

instances = read_step_assembly(step_path)
print(f"Found {len(instances)} leaf instance(s) via read_step_assembly()\n")
print(f"{'name':35s} {'dx (mm)':>14s} {'dy (mm)':>14s} {'dz (mm)':>14s}   parent_path")
print("-" * 100)

for inst in instances:
    bbox = Bnd_Box()
    brepbndlib.Add(inst.shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    dx, dy, dz = xmax - xmin, ymax - ymin, zmax - zmin
    path = " / ".join(inst.parent_path) if inst.parent_path else "(root)"
    print(f"{inst.name:35s} {dx:14.3f} {dy:14.3f} {dz:14.3f}   {path}")

print()
print("Compare each part's dx/dy/dz above against its known real-world size")
print("(or the correct Fusion dimensions for that specific part).")

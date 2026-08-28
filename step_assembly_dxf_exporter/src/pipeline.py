"""
Orchestrates the full STEP -> DXF(s) -> zip flow:

  read_step_assembly -> deduplicate -> classify each unique part ->
  convert (flat or projection) -> write DXF -> zip everything + manifest.json

This is the function both the CLI and the MCP tool call. Kept deliberately
free of any LLM/agent logic -- deterministic and directly testable. The
agent layer (agent.py) wraps this and adds judgment on top.
"""

import json
import os
import shutil
import zipfile
from dataclasses import asdict, dataclass

from .step_reader import read_step_assembly
from .dedup import deduplicate, UniquePart
from .classify import classify_part, PartClass, Classification
from .convert_flat import extract_flat_profile
from .convert_projection import project_shape, ViewDirection
from .dxf_writer import write_flat_profile_dxf, write_projection_dxf
from .preview import render_dxf_preview
from OCC.Core.gp import gp_Dir


def _safe_filename(name: str, used: set) -> str:
    base = "".join(c if c.isalnum() or c in "-_." else "_" for c in name).strip("_") or "part"
    candidate = base
    i = 1
    while candidate in used:
        i += 1
        candidate = f"{base}_{i}"
    used.add(candidate)
    return candidate


@dataclass
class ManifestEntry:
    part_name: str
    filename: str
    instance_count: int
    classification: str
    classification_reason: str
    classification_confidence: str
    needs_review: bool
    preview_filename: str = None


def run_pipeline(step_path: str, out_dir: str, view: ViewDirection = ViewDirection.FRONT,
                  flip_flat_face: bool = False, single_face: bool = False,
                  include_hidden: bool = False, generate_previews: bool = True) -> dict:
    """
    Returns a dict with:
      - "manifest": list of ManifestEntry (as dicts)
      - "zip_path": path to the output zip
      - "parts_dir": path to the folder of raw DXFs (pre-zip)

    flip_flat_face: which way counts as "up" for thin/flat parts -- default
    (False) is global +Z, the standard convention for parts modeled lying
    flat on a table. Affects both which face single_face mode extracts from,
    and whether flat parts are projected from Top or Bottom.

    single_face: if True, flat/thin parts are exported using the older
    single-dominant-face wire extraction (only geometry lying exactly on
    one planar face) instead of the default full top/bottom orthographic
    projection. Default (False) matches what CAD packages like Fusion 360
    produce for a "top view" DXF: every edge visible from that direction,
    including secondary features (a boss, a stepped shoulder, a second
    part) that sit at a different height than the main face. Single-face
    mode is kept as a fallback/opt-in for the rare case where you want
    strictly the one face's own boundary and nothing else that happens to
    be visible above or below it.

    include_hidden: whether projected (non-single-face) DXFs also include
    occluded geometry on a dashed HIDDEN layer. Default False -- for a
    cutting DXF you almost always want only what's actually visible from
    that side; leaving hidden edges in makes an opaque part look
    "transparent" once opened in a viewer that shows all layers by default
    (front and back features overlaid). Set True only for a
    documentation-style engineering drawing.

    generate_previews: whether to also render a PNG quick-look image of
    each exported DXF (into out_dir/previews/), so parts can be visually
    sanity-checked without opening a separate CAD/CAM application. Default
    True. A preview-generation failure is non-fatal -- it's logged as a
    warning and the DXF itself is still produced and included normally.
    """
    up_axis = gp_Dir(0, 0, -1) if flip_flat_face else gp_Dir(0, 0, 1)
    thin_view = ViewDirection.BOTTOM if flip_flat_face else ViewDirection.TOP

    # Clear any leftover parts/previews from a PREVIOUS conversion into this
    # same out_dir before writing new ones. Without this, running the
    # pipeline repeatedly into the same --out folder (e.g. the CLI's default
    # ./output every time) silently accumulates DXFs/PNGs from every past
    # run alongside the current one -- easy to mistake for a bug in the
    # current conversion when it's actually stale files from an earlier,
    # unrelated STEP file never being cleaned up.
    os.makedirs(out_dir, exist_ok=True)
    parts_dir = os.path.join(out_dir, "parts")
    if os.path.isdir(parts_dir):
        shutil.rmtree(parts_dir)
    os.makedirs(parts_dir, exist_ok=True)
    previews_dir = os.path.join(out_dir, "previews")
    if os.path.isdir(previews_dir):
        shutil.rmtree(previews_dir)
    if generate_previews:
        os.makedirs(previews_dir, exist_ok=True)

    instances = read_step_assembly(step_path)
    unique_parts: list[UniquePart] = deduplicate(instances)

    manifest: list[ManifestEntry] = []
    used_filenames: set = set()

    for part in unique_parts:
        classification: Classification = classify_part(part.shape)
        fname_base = _safe_filename(part.name, used_filenames)
        filename = f"{fname_base}.dxf"
        out_path = os.path.join(parts_dir, filename)
        needs_review = False

        try:
            if classification.part_class == PartClass.FLAT:
                if single_face:
                    profile = extract_flat_profile(part.shape, up_axis=up_axis)
                    write_flat_profile_dxf(profile, out_path)
                else:
                    result = project_shape(part.shape, view=thin_view)
                    write_projection_dxf(result, out_path, include_hidden=include_hidden)
            elif classification.part_class == PartClass.PROJECTION:
                result = project_shape(part.shape, view=view)
                write_projection_dxf(result, out_path, include_hidden=include_hidden)
            else:
                # AMBIGUOUS -- still a thin part, just without one clearly
                # dominant flat face (e.g. bent sheet metal), so it uses the
                # same top/bottom convention as FLAT parts rather than the
                # arbitrary --view direction meant for genuinely bulky 3D
                # parts. Still flagged for review either way.
                result = project_shape(part.shape, view=thin_view)
                write_projection_dxf(result, out_path, include_hidden=include_hidden)
                needs_review = True
        except Exception as e:
            needs_review = True
            filename = None
            classification = Classification(
                classification.part_class, classification.thin_axis, "low",
                f"Conversion failed: {e}",
            )

        preview_filename = None
        if filename and generate_previews:
            try:
                preview_filename = f"{fname_base}.png"
                render_dxf_preview(out_path, os.path.join(previews_dir, preview_filename))
            except Exception as e:
                # Non-fatal: the DXF itself is still good even if we
                # couldn't render a preview image of it.
                preview_filename = None

        manifest.append(ManifestEntry(
            part_name=part.name,
            filename=filename,
            instance_count=part.instance_count,
            classification=classification.part_class.value,
            classification_reason=classification.reason,
            classification_confidence=classification.confidence,
            needs_review=needs_review,
            preview_filename=preview_filename,
        ))

    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump([asdict(m) for m in manifest], f, indent=2)

    zip_path = os.path.join(out_dir, "export.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in manifest:
            if entry.filename:
                zf.write(os.path.join(parts_dir, entry.filename), arcname=f"parts/{entry.filename}")
            if entry.preview_filename:
                zf.write(os.path.join(previews_dir, entry.preview_filename),
                          arcname=f"previews/{entry.preview_filename}")
        zf.write(manifest_path, arcname="manifest.json")

    return {
        "manifest": [asdict(m) for m in manifest],
        "zip_path": zip_path,
        "parts_dir": parts_dir,
        "previews_dir": previews_dir if generate_previews else None,
    }
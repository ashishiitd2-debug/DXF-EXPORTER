# STEP → DXF Assembly Exporter

Converts a STEP file (single part **or** full assembly) into one DXF per unique
part, packaged as a zip with a manifest. Handles a mix of flat/sheet-metal
parts and general 3D parts in the same assembly automatically.

## Why it's built this way

STEP assemblies are a tree of **instances** pointing at a smaller set of
**unique part definitions** (this is exactly how SolidWorks/Inventor/Fusion
represent them internally too). If you naively flatten the assembly into raw
geometry, you lose that structure and end up exporting the same bracket 20
times. So the pipeline reads the assembly via OpenCASCADE's XCAF document
layer (the same layer that preserves product structure, names, and instance
transforms), deduplicates identical parts by a geometric signature, and only
converts each *unique* part once.

Each part is independently classified as either:
- **flat / sheet-metal** → its planar profile (outer + inner hole wires) is
  extracted directly — this is what you actually want for laser/plasma/
  waterjet cutting, and avoids any projection distortion.
- **general 3D** → converted via true hidden-line-removal orthographic
  projection (front/top/side) into 2D edges.

## Pipeline stages (`src/`)

| Module | Responsibility |
|---|---|
| `step_reader.py` | Reads STEP via `STEPCAFControl_Reader`, walks the XCAF assembly tree, returns one `PartInstance` per occurrence (name, shape, transform, free/assembly label) |
| `dedup.py` | Computes a geometric signature (volume, surface area, bbox, face/edge counts) per shape and collapses repeated instances into unique parts + instance counts |
| `classify.py` | Decides flat-pattern vs projection per unique part, using bounding-box aspect ratio and planar-face-area heuristics |
| `convert_flat.py` | Extracts outer/inner wires from the dominant planar face of a flat part |
| `convert_projection.py` | Runs `HLRBRep_Algo` hidden-line-removal to get 2D projected edges for a 3D part |
| `dxf_writer.py` | Writes wires/edges to a DXF file via `ezdxf` (LWPOLYLINE/LINE/ARC/CIRCLE, sensible layers) |
| `pipeline.py` | Orchestrates all of the above end-to-end: STEP path in → zip path + manifest out |
| `agent.py` | Optional LLM layer: reviews classification decisions, flags ambiguous parts, writes a plain-language summary of what was exported |

## Installation

`pythonocc-core` is **not pip-installable** — it wraps the OpenCASCADE C++
libraries and needs conda:

```bash
conda create -n step2dxf -c conda-forge python=3.11 pythonocc-core=7.8.1
conda activate step2dxf
pip install -r requirements.txt
```

## CLI usage

```bash
python cli.py input_assembly.step --out ./output
# -> ./output/parts/*.dxf
# -> ./output/manifest.json
# -> ./output/export.zip
```

## As an MCP tool

`mcp_server.py` exposes `convert_step_to_dxf(step_path) -> zip_path` so any
agent (including a Claude-based one) can call this as a function rather than
you scripting the CLI directly.

## Known limitations / next steps

- Flat-part detection uses a bounding-box + planar-face-area heuristic; genuine
  sheet-metal STEP files (with `AP242`/sheet-metal PMI) could instead be
  detected from that metadata directly for higher accuracy — worth adding if
  your source files reliably carry it.
- Multi-planar parts (e.g. bent brackets with two distinct flat regions) are
  treated as one dominant face for now; folding/unfold logic for bent sheet
  metal is a real can of worms (this is what SolidWorks' Sheet Metal module
  and Fusion's Flat Pattern feature specifically compute) and is out of scope
  here — this tool assumes parts are already flat solids or fully 3D, not
  bent sheet metal needing unfolding.
- Shape-signature dedup is a heuristic; for exact-duplicate detection under
  floating-point noise, tolerance in `dedup.py` may need tuning per source CAD system.

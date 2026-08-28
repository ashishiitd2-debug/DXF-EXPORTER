#!/usr/bin/env python
"""
CLI entry point.

Easiest usage: drop one or more .step/.stp files into ./input/ (created
automatically the first time you run this), then just run:

    python cli.py

No path-typing needed -- one file in ./input/ gets converted straight to
./output/; multiple files each get their own ./output/<filename>/ folder so
they don't collide.

You can still pass an explicit path exactly as before, which skips the
input/ folder entirely:

    python cli.py input.step --out ./output
    python cli.py input.step --out ./output --agent --hint "all parts are laser-cut sheet metal"
    python cli.py input.step --out ./output --view top

Note: this auto-discovery behavior is local to this CLI script only. The
hosted web app (webapp/app.py) calls src/pipeline.py's run_pipeline()
directly with the uploaded file's path and never touches this file or the
input/ folder, so none of this affects it.
"""

import argparse
import sys
from pathlib import Path

from src.pipeline import run_pipeline
from src.convert_projection import ViewDirection

INPUT_DIR = Path("./input")
STEP_EXTENSIONS = (".step", ".stp")


def _discover_input_files() -> list[Path]:
    INPUT_DIR.mkdir(exist_ok=True)
    return sorted(
        p for p in INPUT_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in STEP_EXTENSIONS
    )


def _run_one(step_path: str, out_dir: str, view: ViewDirection, args) -> dict:
    if args.agent:
        from src.agent import run_agent_pipeline
        result = run_agent_pipeline(step_path, out_dir, user_hint=args.hint, view=view)
        print(result.summary)
        if result.flagged_parts:
            print("Flagged for review:")
            for item in result.flagged_parts:
                print(f"  - {item['part_name']}: {item['note']}")
        print(f"Zip: {result.zip_path}")
        return {"manifest": result.manifest, "zip_path": result.zip_path}
    else:
        result = run_pipeline(step_path, out_dir, view=view, flip_flat_face=args.flip_face,
                               single_face=args.single_face, include_hidden=args.include_hidden,
                               generate_previews=not args.no_preview)
        n_parts = len(result["manifest"])
        n_review = sum(1 for m in result["manifest"] if m["needs_review"])
        print(f"Exported {n_parts} unique part(s), {n_review} flagged for review.")
        print(f"Zip: {result['zip_path']}")
        if result.get("previews_dir"):
            print(f"Previews: {result['previews_dir']}  (PNG per part -- open these to eyeball the output)")
        return result


def main():
    parser = argparse.ArgumentParser(description="Convert a STEP file (part or assembly) to DXF(s).")
    parser.add_argument("step_file", nargs="?", default=None,
                         help="Path to input .step/.stp file. Omit this to auto-process "
                              "whatever's in ./input/ instead.")
    parser.add_argument("--out", default="./output", help="Output directory")
    parser.add_argument("--view", default="front", choices=["front", "top", "right"],
                         help="Projection view direction for non-flat parts")
    parser.add_argument("--flip-face", action="store_true",
                         help="Flip which side counts as \"up\" for flat/thin parts. Default picks "
                              "global +Z as up (viewed from above).")
    parser.add_argument("--single-face", action="store_true",
                         help="For flat/thin parts, extract only the single dominant face's own "
                              "geometry instead of a full top/bottom projection. Default (off) "
                              "matches Fusion 360-style top-view DXF export, capturing every edge "
                              "visible from above/below including secondary features at other heights.")
    parser.add_argument("--include-hidden", action="store_true",
                         help="Include occluded (hidden-line) geometry on a dashed HIDDEN layer. "
                              "Default (off) omits it, since most DXF viewers show all layers by "
                              "default and hidden edges make an opaque part look transparent. Turn "
                              "this on only for a documentation-style engineering drawing.")
    parser.add_argument("--no-preview", action="store_true",
                         help="Skip generating PNG quick-look previews of each exported DXF. "
                              "Previews are on by default so you can sanity-check the output "
                              "without opening a separate CAD/CAM application.")
    parser.add_argument("--agent", action="store_true",
                         help="Run the LLM review layer on top of the pipeline (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--hint", default=None,
                         help="Optional context for the agent layer, e.g. 'all parts are laser-cut sheet metal'")
    args = parser.parse_args()

    view = ViewDirection(args.view)

    if args.step_file:
        # Explicit path given -- unchanged behavior, no input/ folder involved.
        _run_one(args.step_file, args.out, view, args)
        return 0

    # No path given -- auto-discover from ./input/
    files = _discover_input_files()

    if not files:
        print(f"No .step/.stp files found in {INPUT_DIR}/.")
        print(f"Drop a STEP file into {INPUT_DIR}/ and run `python cli.py` again,")
        print("or pass a path directly: python cli.py path/to/file.step")
        return 1

    if len(files) == 1:
        print(f"Found 1 file in {INPUT_DIR}/: {files[0].name}")
        _run_one(str(files[0]), args.out, view, args)
        return 0

    # Multiple files -- process each into its own named subfolder so
    # outputs don't overwrite each other.
    print(f"Found {len(files)} files in {INPUT_DIR}/, processing each:")
    for f in files:
        print(f"  - {f.name}")
    print()

    total_parts = 0
    for f in files:
        sub_out = str(Path(args.out) / f.stem)
        print(f"--- {f.name} -> {sub_out} ---")
        result = _run_one(str(f), sub_out, view, args)
        total_parts += len(result.get("manifest", []))
        print()

    print(f"Done: {len(files)} file(s), {total_parts} total part(s) exported.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
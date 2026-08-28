"""
Exposes the STEP->DXF pipeline as an MCP tool, so any MCP-compatible agent
(a Claude-based agent, Claude Code, etc.) can call it directly rather than
you scripting the CLI by hand.

Run with:
    python mcp_server.py

Then point an MCP client at it (stdio transport by default).
"""

from mcp.server.fastmcp import FastMCP

from src.pipeline import run_pipeline
from src.convert_projection import ViewDirection

mcp = FastMCP("step-to-dxf")


@mcp.tool()
def convert_step_to_dxf(step_path: str, out_dir: str, view: str = "front") -> dict:
    """
    Convert a STEP file (single part or full assembly) into one DXF per
    unique part, zipped together with a manifest.

    Args:
        step_path: absolute path to the input .step/.stp file
        out_dir: directory to write parts/, manifest.json, and export.zip into
        view: projection view for non-flat parts -- "front", "top", or "right"

    Returns a dict with zip_path, parts_dir, and the manifest (one entry per
    unique part: name, filename, instance_count, classification, and whether
    it needs manual review).
    """
    result = run_pipeline(step_path, out_dir, view=ViewDirection(view))
    return {
        "zip_path": result["zip_path"],
        "parts_dir": result["parts_dir"],
        "manifest": result["manifest"],
    }


if __name__ == "__main__":
    mcp.run()

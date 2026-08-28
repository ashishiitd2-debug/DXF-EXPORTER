"""
Renders a quick-look PNG preview of an exported DXF file, using ezdxf's
built-in matplotlib rendering backend. This is NOT a full DXF viewer/editor
-- it's a fast, dependency-light way to visually sanity-check what got
exported without needing to open a separate CAD/CAM application.
"""

import ezdxf
from ezdxf.addons.drawing import matplotlib as ezdxf_mpl


def render_dxf_preview(dxf_path: str, png_path: str, dpi: int = 150):
    """
    Reads a DXF file back from disk and renders its modelspace to a PNG.

    Reading it back (rather than rendering directly from the in-memory
    ezdxf.Drawing right after writing) deliberately exercises the exact
    file that ends up on disk / in the zip -- catching any issue that only
    shows up in the saved file, not just the in-memory representation.
    """
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    ezdxf_mpl.qsave(msp, png_path, dpi=dpi)

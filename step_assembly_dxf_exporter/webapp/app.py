"""
Minimal web GUI for the STEP -> DXF pipeline: one page, drag-and-drop a
STEP file, get back preview thumbnails and a downloadable zip.

Does NOT modify src/pipeline.py at all -- this just wraps run_pipeline()
behind a small FastAPI app so a non-technical person can use it via a link,
with nothing installed on their end.

Run locally for testing:
    uvicorn webapp.app:app --host 0.0.0.0 --port 8000
(from the project root, with the step2dxf conda env active)

Then open http://localhost:8000 in a browser.

For real deployment, see webapp/README.md.
"""

import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

from src.pipeline import run_pipeline
from src.convert_projection import ViewDirection

app = FastAPI()

JOBS_DIR = Path(tempfile.gettempdir()) / "step2dxf_jobs"
JOBS_DIR.mkdir(exist_ok=True)

# In-memory job status. Fine for a single small instance shared with one or
# two people; if this ever needs to run across multiple server processes or
# survive restarts, swap this for a real store (e.g. a SQLite file or Redis).
JOBS: dict[str, dict] = {}

# Optional simple access gate. Set the ACCESS_CODE environment variable when
# running the server to require it; leave unset to allow anyone with the
# link (fine for testing, not recommended once the link is shared beyond
# one trusted person).
ACCESS_CODE = os.environ.get("ACCESS_CODE")

JOB_TTL_SECONDS = 2 * 60 * 60  # clean up job files after 2 hours


def _check_access(code: str | None):
    if ACCESS_CODE and code != ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Incorrect access code.")


def _cleanup_old_jobs():
    now = time.time()
    for job_dir in JOBS_DIR.iterdir():
        if job_dir.is_dir() and (now - job_dir.stat().st_mtime) > JOB_TTL_SECONDS:
            shutil.rmtree(job_dir, ignore_errors=True)
            JOBS.pop(job_dir.name, None)


def _run_conversion(job_id: str, step_path: str, out_dir: str):
    try:
        result = run_pipeline(step_path, out_dir, view=ViewDirection.FRONT)
        JOBS[job_id] = {
            "status": "done",
            "manifest": result["manifest"],
            "zip_path": result["zip_path"],
            "previews_dir": result.get("previews_dir"),
        }
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}


@app.get("/", response_class=HTMLResponse)
def index():
    gate_html = ""
    if ACCESS_CODE:
        gate_html = """
        <div class="field">
          <label for="accessCode">Access code</label>
          <input type="password" id="accessCode" class="glass" placeholder="Enter code" autocomplete="off">
        </div>
        """
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>STEP to DXF</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&family=Manrope:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0a0912;
      --text: #f3f1fb;
      --text-dim: rgba(243,241,251,0.66);
      --glass: rgba(255,255,255,0.045);
      --glass-hover: rgba(255,255,255,0.08);
      --glass-border: rgba(255,255,255,0.14);
      --violet: #8b5cf6;
      --blue: #38bdf8;
      --gradient: linear-gradient(135deg, var(--violet), var(--blue));
      --ok: #34d399;
      --error: #f87171;
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      min-height: 100vh;
      background-color: var(--bg);
      background-image:
        radial-gradient(640px circle at 12% 8%, rgba(139,92,246,0.32), transparent 60%),
        radial-gradient(720px circle at 88% 82%, rgba(56,189,248,0.24), transparent 60%),
        radial-gradient(500px circle at 90% 5%, rgba(139,92,246,0.14), transparent 55%);
      background-attachment: fixed;
      color: var(--text);
      font-family: 'Manrope', -apple-system, sans-serif;
    }}

    main {{
      max-width: 760px;
      margin: 0 auto;
      padding: 72px 24px 140px;
    }}

    .eyebrow {{
      font-family: 'IBM Plex Mono', monospace;
      font-size: 12px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      background: var(--gradient);
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
      margin-bottom: 14px;
      display: inline-block;
    }}

    h1 {{
      font-family: 'Sora', sans-serif;
      font-weight: 700;
      font-size: clamp(2.4rem, 6.4vw, 3.8rem);
      line-height: 1.05;
      letter-spacing: -0.02em;
      margin: 0 0 18px;
    }}

    .lede {{
      font-size: 1.05rem;
      line-height: 1.6;
      color: var(--text-dim);
      max-width: 42em;
      margin: 0 0 36px;
    }}

    .glass {{
      background: var(--glass);
      border: 1px solid var(--glass-border);
      border-radius: 20px;
      backdrop-filter: blur(20px) saturate(150%);
      -webkit-backdrop-filter: blur(20px) saturate(150%);
    }}

    .field {{ margin-bottom: 28px; }}
    .field label {{
      display: block;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--text-dim);
      margin-bottom: 8px;
    }}
    .field input {{
      width: 100%;
      max-width: 280px;
      padding: 12px 14px;
      color: var(--text);
      font-family: 'IBM Plex Mono', monospace;
      font-size: 14px;
      border-radius: 12px;
    }}
    .field input:focus-visible {{
      outline: 2px solid var(--blue);
      outline-offset: 1px;
    }}

    #drop {{
      padding: 56px 24px;
      text-align: center;
      cursor: pointer;
      border-style: dashed;
      border-width: 1.5px;
      border-color: rgba(139,92,246,0.4);
      transition: border-color 0.2s ease, background-color 0.2s ease, transform 0.15s ease;
    }}
    #drop:hover {{ border-color: rgba(139,92,246,0.7); background: var(--glass-hover); }}
    #drop.drag {{ border-color: var(--blue); background: rgba(56,189,248,0.08); transform: scale(1.005); }}
    #drop:focus-visible {{ outline: 2px solid var(--blue); outline-offset: 3px; }}

    #drop svg {{ display: block; margin: 0 auto 16px; opacity: 0.85; }}
    #drop .drop-label {{ color: var(--text-dim); font-size: 0.98rem; }}
    #drop .drop-sub {{
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-dim);
      margin-top: 10px;
      opacity: 0.7;
    }}

    input[type=file] {{ display: none; }}

    #status {{
      margin-top: 22px;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 13px;
      letter-spacing: 0.02em;
      color: var(--text-dim);
      min-height: 1.2em;
    }}
    #status .ok {{ color: var(--ok); }}
    #status .error {{ color: var(--error); }}

    .trace {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
    }}
    .trace svg rect {{
      stroke: var(--blue);
      stroke-width: 2;
      fill: none;
      stroke-dasharray: 6 5;
    }}
    @media (prefers-reduced-motion: no-preference) {{
      .trace svg rect {{ animation: dash 1.1s linear infinite; }}
    }}
    @keyframes dash {{ to {{ stroke-dashoffset: -22; }} }}

    #previews {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: 16px;
      margin-top: 28px;
    }}
    .part {{
      border-radius: 14px;
      padding: 10px;
    }}
    .part img {{
      width: 100%;
      display: block;
      border-radius: 8px;
      background: rgba(0,0,0,0.3);
    }}
    .part .name {{
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--text-dim);
      margin-top: 8px;
      word-break: break-word;
    }}

    #downloadBtn {{
      display: none;
      margin-top: 32px;
      padding: 14px 30px;
      font-family: 'Sora', sans-serif;
      font-weight: 600;
      font-size: 14px;
      letter-spacing: 0.02em;
      background: var(--gradient);
      color: #0a0912;
      border: none;
      border-radius: 999px;
      cursor: pointer;
      box-shadow: 0 8px 24px rgba(139,92,246,0.35);
      transition: box-shadow 0.2s ease, transform 0.15s ease;
    }}
    #downloadBtn:hover {{
      box-shadow: 0 10px 30px rgba(56,189,248,0.45);
      transform: translateY(-1px);
    }}
    #downloadBtn:focus-visible {{ outline: 2px solid var(--text); outline-offset: 2px; }}

    .titleblock {{
      position: fixed;
      right: 20px;
      bottom: 20px;
      display: flex;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      border-radius: 14px;
      overflow: hidden;
    }}
    .titleblock div {{
      padding: 8px 12px;
      border-right: 1px solid var(--glass-border);
    }}
    .titleblock div:last-child {{ border-right: none; }}
    .titleblock .k {{ color: var(--text-dim); display: block; margin-bottom: 3px; }}
    .titleblock .v {{
      background: var(--gradient);
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
      font-weight: 600;
    }}

    @media (max-width: 560px) {{
      main {{ padding: 48px 18px 120px; }}
      .titleblock {{ position: static; margin-top: 40px; width: fit-content; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">CAD &rarr; CNC export</div>
    <h1>STEP to DXF</h1>
    <p class="lede">Drop a STEP assembly. Get one cut-ready DXF per part back, previewed and zipped.</p>

    {gate_html}

    <div id="drop" class="glass" role="button" tabindex="0" aria-label="Choose or drop a STEP file">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M12 3v12M6 9l6-6 6 6M4 21h16" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <div class="drop-label">Drop a STEP file here, or click to choose one</div>
      <div class="drop-sub">.step &middot; .stp</div>
      <input type="file" id="fileInput" accept=".step,.stp">
    </div>

    <div id="status"></div>
    <div id="previews"></div>
    <button id="downloadBtn">Download ZIP</button>
  </main>

  <div class="titleblock glass">
    <div><span class="k">Dwg</span><span class="v" id="tbDwg">&mdash;</span></div>
    <div><span class="k">Date</span><span class="v" id="tbDate">&mdash;</span></div>
    <div><span class="k">Scale</span><span class="v">Noted</span></div>
    <div><span class="k">Rev</span><span class="v">A</span></div>
  </div>

<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('fileInput');
const statusEl = document.getElementById('status');
const previewsEl = document.getElementById('previews');
const downloadBtn = document.getElementById('downloadBtn');
const tbDate = document.getElementById('tbDate');
const tbDwg = document.getElementById('tbDwg');

const today = new Date();
tbDate.textContent = today.toISOString().slice(0,10);

drop.addEventListener('click', () => fileInput.click());
drop.addEventListener('keydown', e => {{
  if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); fileInput.click(); }}
}});
drop.addEventListener('dragover', e => {{ e.preventDefault(); drop.classList.add('drag'); }});
drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
drop.addEventListener('drop', e => {{
  e.preventDefault();
  drop.classList.remove('drag');
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
}});
fileInput.addEventListener('change', () => {{
  if (fileInput.files.length) handleFile(fileInput.files[0]);
}});

function accessCode() {{
  const el = document.getElementById('accessCode');
  return el ? el.value : '';
}}

function tracingHtml(label) {{
  return '<span class="trace"><svg width="18" height="18" viewBox="0 0 18 18">' +
    '<rect x="2" y="2" width="14" height="14" rx="2"/></svg>' + label + '</span>';
}}

async function handleFile(file) {{
  previewsEl.innerHTML = '';
  downloadBtn.style.display = 'none';
  statusEl.innerHTML = 'Uploading&hellip;';

  const formData = new FormData();
  formData.append('file', file);
  formData.append('access_code', accessCode());

  let uploadResp;
  try {{
    uploadResp = await fetch('/upload', {{ method: 'POST', body: formData }});
  }} catch (err) {{
    statusEl.innerHTML = '<span class="error">Upload failed: ' + err + '</span>';
    return;
  }}
  if (!uploadResp.ok) {{
    const detail = await uploadResp.text();
    statusEl.innerHTML = '<span class="error">Upload failed: ' + detail + '</span>';
    return;
  }}
  const {{ job_id }} = await uploadResp.json();
  tbDwg.textContent = job_id.slice(0, 8);
  statusEl.innerHTML = tracingHtml('Tracing parts&hellip; large assemblies take longer.');
  poll(job_id);
}}

async function poll(jobId) {{
  const resp = await fetch('/status/' + jobId);
  const data = await resp.json();
  if (data.status === 'processing') {{
    setTimeout(() => poll(jobId), 1500);
    return;
  }}
  if (data.status === 'error') {{
    statusEl.innerHTML = '<span class="error">Conversion failed: ' + data.error + '</span>';
    return;
  }}
  // done
  const nParts = data.manifest.length;
  const nReview = data.manifest.filter(m => m.needs_review).length;
  statusEl.innerHTML = '<span class="ok">Exported ' + nParts + ' part' + (nParts === 1 ? '' : 's') + '</span>' +
    (nReview ? ' &mdash; ' + nReview + ' need' + (nReview === 1 ? 's' : '') + ' a check.' : '.');

  for (const part of data.manifest) {{
    if (!part.preview_filename) continue;
    const div = document.createElement('div');
    div.className = 'part glass';
    div.innerHTML = '<img src="/preview/' + jobId + '/' + encodeURIComponent(part.preview_filename) + '" alt="' + part.part_name + ' preview">' +
      '<div class="name">' + part.part_name + '</div>';
    previewsEl.appendChild(div);
  }}

  downloadBtn.style.display = 'inline-block';
  downloadBtn.onclick = () => {{ window.location.href = '/download/' + jobId; }};
}}
</script>
</body>
</html>
"""


@app.post("/upload")
async def upload(file: UploadFile = File(...), access_code: str = Form(default="")):
    _check_access(access_code)
    _cleanup_old_jobs()

    if not file.filename.lower().endswith((".step", ".stp")):
        raise HTTPException(status_code=400, detail="Please upload a .step or .stp file.")

    job_id = uuid.uuid4().hex
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir()
    step_path = job_dir / file.filename
    with open(step_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    out_dir = job_dir / "output"
    JOBS[job_id] = {"status": "processing"}

    thread = threading.Thread(target=_run_conversion, args=(job_id, str(step_path), str(out_dir)))
    thread.start()

    return {"job_id": job_id}


@app.get("/status/{job_id}")
def status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job.")
    return job


@app.get("/preview/{job_id}/{filename}")
def preview(job_id: str, filename: str):
    job = JOBS.get(job_id)
    if job is None or job.get("status") != "done" or not job.get("previews_dir"):
        raise HTTPException(status_code=404, detail="Preview not available.")
    path = Path(job["previews_dir"]) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Preview file not found.")
    return FileResponse(path, media_type="image/png")


@app.get("/download/{job_id}")
def download(job_id: str):
    job = JOBS.get(job_id)
    if job is None or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Result not available.")
    zip_path = job.get("zip_path")
    if not zip_path or not Path(zip_path).is_file():
        raise HTTPException(status_code=404, detail="Zip file not found.")
    return FileResponse(zip_path, media_type="application/zip", filename="export.zip")

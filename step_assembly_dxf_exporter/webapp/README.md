# Web GUI

A one-page site: drop a STEP file, get preview thumbnails and a "Download
ZIP" button. Wraps `src/pipeline.py` as-is -- nothing about the core
pipeline changed, only this new layer on top of it.

## Test it locally first

Build and run must be from the **project root** (one level up from this
folder), since the Dockerfile needs `requirements.txt`, `src/`, and
`webapp/` all in its build context:

```bash
cd step_assembly_dxf_exporter
docker build -f webapp/Dockerfile -t step2dxf-web .
docker run -p 8000:8000 -e ACCESS_CODE=yourcode step2dxf-web
```

Then open `http://localhost:8000`. Drop a STEP file, watch it convert,
download the zip. `ACCESS_CODE` is optional -- omit `-e ACCESS_CODE=...`
to skip the access-code field entirely.

If you'd rather not use Docker for local testing, you can also just run it
directly inside your existing `step2dxf` conda env:

```bash
conda activate step2dxf
pip install -r webapp/requirements-web.txt
uvicorn webapp.app:app --host 0.0.0.0 --port 8000
```

## Deploying so someone else can reach it via a link

Any host that runs an arbitrary Docker container works. A few reasonable
options, roughly cheapest/simplest to most robust:

- **A small VPS** (DigitalOcean, Linode, Hetzner -- ~$5-6/month for a
  1-2GB droplet). Install Docker, `git clone` or `scp` this project over,
  run the same two `docker build` / `docker run` commands as above, and
  point a domain (or just share `http://your-server-ip:8000`) at it. Most
  control, least platform-specific setup.
- **Fly.io** / **Render** / **Railway** -- all support "deploy from a
  Dockerfile" directly from a git repo, with a free or cheap starter tier.
  Push this project to a private GitHub repo, connect it, point the
  platform at `webapp/Dockerfile`, set the `ACCESS_CODE` environment
  variable in their dashboard, deploy. No server management at all, but
  double-check the platform's free-tier RAM (pythonocc-core + a large
  assembly conversion can be memory-hungry -- 1GB+ is safer than 512MB)
  and request-timeout limits (conversion runs as a background thread and
  is polled, so it isn't blocked by typical HTTP request timeouts, but very
  large assemblies could still take a while).

**Keep the repo private** if you go the GitHub-connected route -- the
platforms above only need read access to build the Docker image, and a
private repo means the person you share the final link with never sees
the source, matching what you asked for.

## Notes on what this does and doesn't handle

- **One shared link, lightweight access control.** `ACCESS_CODE` is a
  single shared password checked on upload, not real per-user
  authentication. Fine for sharing with one or two trusted people; if this
  ever needs to serve a wider audience, put real auth in front of it.
- **Jobs are stored in memory + a temp folder**, cleaned up after 2 hours
  (see `JOB_TTL_SECONDS` in `app.py`). Restarting the server loses
  in-progress job status (the person would just need to re-upload). This
  is fine at the scale of "share with someone" -- if this ever needs to
  run across multiple server instances or survive restarts reliably, swap
  the in-memory `JOBS` dict for a real store (SQLite file, Redis, etc.).
- **No file size limit is enforced explicitly** beyond whatever the
  hosting platform itself caps requests at -- worth checking if you expect
  very large STEP files.

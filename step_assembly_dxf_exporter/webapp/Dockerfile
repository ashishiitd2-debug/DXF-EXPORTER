# Conda-forge base image, since pythonocc-core isn't pip-installable.
FROM condaforge/miniforge3:latest

WORKDIR /app

# Create the conda environment with pythonocc-core first (this is the slow,
# rarely-changing layer -- keeping it separate from the app code means
# Docker can cache it across rebuilds when you only change app.py).
RUN conda create -n step2dxf -c conda-forge python=3.11 pythonocc-core -y \
    && conda clean -afy

# Install pip dependencies (both the core pipeline's and the web app's)
# inside that environment.
COPY requirements.txt webapp/requirements-web.txt ./
RUN /opt/conda/envs/step2dxf/bin/pip install --no-cache-dir \
    -r requirements.txt -r requirements-web.txt

# Now copy the actual project code.
COPY src ./src
COPY webapp ./webapp

ENV PATH=/opt/conda/envs/step2dxf/bin:$PATH
EXPOSE 8000

# ACCESS_CODE is optional -- set it at deploy time (e.g. `docker run -e
# ACCESS_CODE=yourcode ...`) to require it on the upload page. Leave unset
# to allow anyone with the link.
CMD ["uvicorn", "webapp.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim

# OCP (cadquery-ocp) links libGL/X11 even for headless STEP+IGES reading —
# without these the import dies with "libGL.so.1: cannot open shared object
# file" and every IGES upload fails at the reader. python:3.12-slim has none.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libxext6 libxrender1 libsm6 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fails the build instead of the upload. This exact import is what broke.
RUN python -c "from OCP.IGESControl import IGESControl_Reader; from OCP.STEPControl import STEPControl_Reader"

COPY app ./app

# API:    uvicorn app.main:app --host 0.0.0.0 --port 8000
# Worker: celery -A app.worker worker --loglevel=info
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# syntax=docker/dockerfile:1

# Multi-stage build for the PyPSA PPA Explorer Streamlit app.
#
#   docker build -t pypsa-ppa-explorer .
#   docker run -p 8501:8501 pypsa-ppa-explorer
#
# For Google Cloud Run / Cloud Build see cloudbuild.yaml and
# docs/DEPLOYMENT.md.

# ---- Build stage: install the pinned runtime into a clean venv ----
# Python 3.13 matches .python-version. All requirements.txt pins ship
# manylinux cp313 wheels, so no compiler is needed here.
FROM python:3.13-slim AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ---- Runtime stage ----
FROM python:3.13-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp \
    # Container memory guardrails -- see docs/DEPLOYMENT.md section 5.
    PPA_WORKER_MEM_MB=1200 \
    PPA_RESERVE_MEM_MB=800

# Runtime system libs required by the scipy / highspy / netCDF4 wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Non-root user (10001 is the Cloud Run convention).
RUN useradd --uid 10001 --create-home appuser

WORKDIR /app

# Copy the whole repo so the relative layout (ppa/, ui/, data/) that the
# code resolves via Path(__file__) is preserved.
COPY --chown=10001:10001 . /app

USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fs http://127.0.0.1:8501/_stcore/health || exit 1

# Cloud Run injects PORT (set to 8501 in cloudbuild.yaml); fall back to 8501
# for a plain `docker run -p 8501:8501`.
CMD ["sh", "-c", "streamlit run streamlit_app.py --server.port \"${PORT:-8501}\" --server.address 0.0.0.0 --server.headless true"]

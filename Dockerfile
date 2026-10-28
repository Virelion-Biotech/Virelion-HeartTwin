FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    cmake \
    pkg-config \
    libopenblas-dev \
    liblapack-dev \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    libxrender1 \
    libxext6 \
    ffmpeg \
    libhdf5-dev \
    libfftw3-dev \
    libopenmpi-dev \
    openmpi-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -e . \
    && python -m pip install --no-cache-dir -r requirements-services.txt \
    && python -m compileall -q hearttwin

EXPOSE 10000
CMD ["sh", "-c", "exec hearttwin-web --host 0.0.0.0 --port \"${PORT:-10000}\" --workspace \"${HEARTTWIN_WORKSPACE:-/var/data/workspace}\" --public-host \"${RENDER_EXTERNAL_HOSTNAME:-localhost}\" --allow-origin \"https://${RENDER_EXTERNAL_HOSTNAME:-localhost}\""]

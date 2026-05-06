# syntax=docker/dockerfile:1
# Official Playwright Python image already contains Chromium, Xvfb and browser deps.
FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt

# Keep the Python layer separate so dependency rebuilds are cached independently from source code.
RUN python -m pip install --upgrade pip && \
    pip install -r /app/requirements.txt

COPY src /app/src
COPY balancer /app/balancer
COPY README.md /app/README.md

RUN mkdir -p /app/output /app/balancer-data

# The container entrypoint stays intentionally simple for Docker Compose usage.
CMD ["python", "src/main.py"]

FROM python:3-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gitgrab.py healthcheck.py .

RUN mkdir -p /var/log /data/repos

VOLUME ["/data/repos"]

ENV GIT_DIR=/data/repos \
    PULL_INTERVAL=3600

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
    CMD python3 /app/healthcheck.py

ENTRYPOINT ["python3", "gitgrab.py"]

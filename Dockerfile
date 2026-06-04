FROM python:3-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pull_repos.py .

RUN mkdir -p /var/log /home/user/git && \
    chmod 777 /var/log && \
    useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app /home/user/git

VOLUME ["/home/user/git"]

USER appuser

ENV GIT_DIR=/home/user/git \
    PULL_INTERVAL=3600

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
    CMD python3 -c "import os; exit(0 if os.path.exists('/app/pull_repos.py') else 1)"

ENTRYPOINT ["python3", "pull_repos.py"]

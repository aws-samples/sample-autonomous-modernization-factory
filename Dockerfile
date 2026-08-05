# Modernization Web App - FastAPI API service (ECS/Fargate)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code + frontend.
COPY src ./src
COPY frontend ./frontend

# Run as a non-root user (least privilege).
RUN useradd --system --uid 10001 --no-create-home appuser \
    && chown -R appuser /app
USER appuser

EXPOSE 8080

# Container health: the app serves the frontend at "/".
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/').status==200 else 1)"

# Serves the API and the static frontend. Settings come from environment
# variables (MF_ARTIFACTS_BUCKET, MF_RESULTS_BUCKET, MF_STATE_TABLE,
# MF_TRANSFORMER_PROJECT, AWS_REGION, ...).
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8080"]

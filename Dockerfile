FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Berlin

WORKDIR /app

# Install system dependencies (curl for healthcheck, tzdata for timezone)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .
RUN mkdir -p /app/data

# Expose Gateway Port
EXPOSE 8090

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f -H "X-Forwarded-For: 127.0.0.1" -H "X-API-Key: ${GATEWAY_API_KEY}" http://localhost:8090/health || exit 1
# NOTE: Dockerfile HEALTHCHECK runs without a shell; the compose-level
# CMD-SHELL healthcheck above is the effective one (env expansion works there).

# Run FastAPI via Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]

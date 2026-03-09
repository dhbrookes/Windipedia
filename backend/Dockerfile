# Backend Dockerfile
# Build: docker build -t windipedia-backend .
# Run:   docker run -p 8000:8000 --env-file ../.env windipedia-backend

FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer if requirements.txt unchanged)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create a non-root user for security
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

# Cloud Run passes PORT env var; default to 8000 for local use
CMD uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"

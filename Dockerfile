# ==============================================================================
# WALVOIL — IoT VISCOSITY MEASUREMENT & REPORTING SYSTEM
# Production Dockerfile for Linux Servers
# ==============================================================================

FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered standard I/O
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies:
# - libreoffice-calc: Headless Excel-to-PDF conversion engine
# - fonts-dejavu-core & fonts-liberation: Pixel-perfect font rendering matching Excel
# - psmisc: Provides 'fuser' utility for UDP port 63363 recovery
# - curl: Lightweight container health check utility
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-calc \
    fonts-dejavu-core \
    fonts-liberation \
    psmisc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory inside container
WORKDIR /app

# Copy dependency specifications first for optimal layer caching
COPY requirements.txt .

# Install Python dependencies (Gunicorn, Flask, OpenPyXL, Pillow)
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and template assets
COPY . .

# Create required persistent storage and logging directories
RUN mkdir -p /app/Reports /app/logs

# Expose Web Interface (TCP) and IoT Telemetry Receiver (UDP)
EXPOSE 5001/tcp
EXPOSE 5002/udp

# Container Healthcheck (pings internal diagnostics API every 30 seconds)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:5001/ping_status || exit 1

# Launch with Gunicorn:
# - 1 Worker: Preserves single-process binding of UDP port 63363
# - 8 Threads: Concurrent multi-threaded request processing for web users
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:5001", "--access-logfile", "-", "--error-logfile", "-", "app:app"]

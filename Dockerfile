# ==============================================================================
# WALVOIL — IoT VISCOSITY MEASUREMENT & REPORTING SYSTEM
# Production Dockerfile
# ==============================================================================

FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies:
# - libreoffice-calc: Headless Excel-to-PDF report generation
# - fonts-dejavu / fonts-liberation: Crisp font rendering in generated PDFs
# - psmisc: Provides 'fuser' utility for UDP port cleanup
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-calc \
    fonts-dejavu-core \
    fonts-liberation \
    psmisc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency specifications first for efficient layer caching
COPY requirements.txt .

# Install Python packages (gunicorn, flask, openpyxl)
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and template
COPY . .

# Create persistent storage directories
RUN mkdir -p /app/Reports

# Expose Web Interface (TCP) and IoT Telemetry Receiver (UDP)
EXPOSE 5001/tcp
EXPOSE 63363/udp

# Run with Gunicorn (1 worker required so single UDP listener binds cleanly)
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5001", "app:app"]

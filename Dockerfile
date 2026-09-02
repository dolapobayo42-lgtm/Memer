FROM python:3.11-slim

# Minimal, secure runtime image for the Deployer Reputation Tracker
WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Install OS-level packages needed to build some wheels (kept minimal)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies declared in requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create a non-root user and ensure runtime directories are writable
RUN useradd -m appuser || true \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

# Switch to the non-root user
USER appuser

# Run the application
CMD ["python", "main.py"]

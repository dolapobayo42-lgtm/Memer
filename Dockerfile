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

# Copy application source and run
COPY . .

# Use a non-root user for safety (optional but recommended)
RUN useradd -m appuser || true
USER appuser

CMD ["python", "main.py"]

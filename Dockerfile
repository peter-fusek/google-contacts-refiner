FROM python:3.13-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py instructions.md ./

# Cloud Run Job entry point
CMD ["python", "entrypoint.py"]

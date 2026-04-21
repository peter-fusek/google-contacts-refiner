FROM python:3.13-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py instructions.md ./
COPY code_tables/ code_tables/

# harvester/ is a PEP 420 namespace package (no __init__.py) that followup_scorer
# and crm_sync import from. Missed by `COPY *.py` — shipped empty in 3.33, which
# made Phase 4 + 5 silently fail with ModuleNotFoundError on every Cloud Run.
COPY harvester/ harvester/

# Cloud Run Job entry point
CMD ["python", "entrypoint.py"]

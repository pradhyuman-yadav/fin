# Shared image for the WAT tools (poller + dashboard).
FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source is bind-mounted in docker-compose for dev; copy as a fallback.
COPY . .

# tools/ import each other by module name (e.g. `from db import connect`),
# which works because scripts are launched as `python tools/<name>.py`.
CMD ["python", "tools/dashboard.py", "--host", "0.0.0.0", "--port", "8000"]

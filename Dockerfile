# Shared image for the WAT tools (poller + dashboard + signals).
# Multi-stage: install deps in a build stage, copy only the installed packages
# into a clean slim runtime (no pip cache, no build wheels).
FROM python:3.12-slim AS build
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
COPY --from=build /install /usr/local
COPY tools/ ./tools/
COPY config/ ./config/

# tools/ import each other by module name (e.g. `from db import connect`),
# which works because scripts are launched as `python tools/<name>.py`.
CMD ["python", "tools/dashboard.py", "--host", "0.0.0.0", "--port", "8000"]

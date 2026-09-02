FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# /data holds the SQLite DB — mount a named volume here so it survives
# container rebuilds/restarts.
VOLUME ["/data"]

# No default CMD/EXPOSE here on purpose: docker-compose.yml sets a
# different command (and port) for the collector vs the dashboard, since
# both run from this same image.

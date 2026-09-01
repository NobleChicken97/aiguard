# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Prevent Python from writing bytecode and buffering stdout (common Docker best practice).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# SYSTEM_PROMPT references the schema description via config, so all files are needed.
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

COPY . .

# Database volume lives outside the image so data survives container restarts.
RUN mkdir -p /app/data

EXPOSE 8000

# The web app is the production entrypoint. Uvicorn with a single worker is
# sufficient for a portfolio demo; scale horizontally or use gunicorn in
# real production.
CMD ["uvicorn", "webapp:app", "--host", "0.0.0.0", "--port", "8000"]

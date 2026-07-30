FROM python:3.12-slim

WORKDIR /app

# Install OS-level deps for psycopg2
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default: run the poller
CMD ["python", "run_ingestion.py"]

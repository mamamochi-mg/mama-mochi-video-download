FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates unzip curl \
    && curl -fsSL https://dl.deno.land/release/v2.3.0/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
    && unzip -q /tmp/deno.zip -d /tmp \
    && install -m 0755 /tmp/deno /usr/local/bin/deno \
    && rm -rf /tmp/deno /tmp/deno.zip /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY storage.py .
COPY .env.example .

CMD ["python", "main.py"]

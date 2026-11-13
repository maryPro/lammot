# LammotBot — production image (Railway builds this automatically)

# Deno: the JavaScript runtime yt-dlp needs for YouTube (since yt-dlp 2025.11)
FROM denoland/deno:bin-2.7.7 AS deno

FROM python:3.12-slim-bookworm

COPY --from=deno /deno /usr/local/bin/deno

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # caches that Deno and yt-dlp write; /tmp is writable for the app user
    DENO_DIR=/tmp/deno \
    XDG_CACHE_HOME=/tmp/cache \
    DATABASE_PATH=/data/lammot.db

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# The app runs as an unprivileged user (see docker-entrypoint.sh)
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh \
 && groupadd --system --gid 10001 lammot \
 && useradd --system --uid 10001 --gid lammot --no-create-home --shell /usr/sbin/nologin lammot

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

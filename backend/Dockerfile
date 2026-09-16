FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 improver \
    && useradd --system --uid 10001 --gid improver --home-dir /app improver

WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/requirements.txt ./
RUN python -m pip install --upgrade pip==25.2 \
    && python -m pip install -r requirements.txt

COPY backend/src ./src
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY browser-extension /extension
RUN python /extension/build.py --output /tmp/ai-secretary-extension.zip --backend-web /app/src/improver/web
RUN python -m pip install --no-deps .

RUN mkdir -p /data/attachments && chown -R improver:improver /app /data
USER improver

ENV PATH="/app/.local/bin:${PATH}"

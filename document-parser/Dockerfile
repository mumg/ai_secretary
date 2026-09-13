FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10002 parser \
    && useradd --system --uid 10002 --gid parser --home-dir /app parser

WORKDIR /app
COPY pyproject.toml main.py ./
RUN python -m pip install --upgrade pip==25.2 \
    && python -m pip install .

USER parser
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.lock requirements-build.lock ./
RUN pip install --no-cache-dir --no-deps -r requirements-build.lock -r requirements.lock

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --no-build-isolation --no-deps . && pip check

USER app
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn pacemaker_registry.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

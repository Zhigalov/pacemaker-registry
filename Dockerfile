FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

USER app
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn pacemaker_registry.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

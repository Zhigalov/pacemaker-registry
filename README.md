# Реестр пейсмейкеров

Первый инкремент приложения: статическая HTML-страница, liveness/readiness
эндпоинты, PostgreSQL для локальной разработки и контейнер для деплоя.

На странице `/add` можно вставить публичную ссылку на результат участника
RussiaRunning. Backend получает имя, мероприятие, дистанцию, chip time, темп и
контрольные точки. Данные пока только показываются на странице и не сохраняются.

## Локальный запуск через Docker

```bash
docker compose up --build
```

После запуска:

- страница: <http://localhost:8080/>
- liveness: <http://localhost:8080/health>
- PostgreSQL readiness: <http://localhost:8080/ready>

## Запуск без Docker

Нужны Python 3.12+ и `uv`:

```bash
uv sync --extra dev
cp .env.example .env
uv run uvicorn pacemaker_registry.main:app --reload
```

## Тесты

```bash
uv run --extra dev pytest
```

`DATABASE_URL` не нужен для загрузки главной страницы, но `/ready` вернёт 503,
пока соединение с PostgreSQL не настроено.

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md specialist.toml ./
COPY src ./src
COPY alembic.ini ./

RUN python -m pip install --upgrade pip \
    && python -m pip install .

EXPOSE 8000

CMD ["fastapi", "run", "src/booking_bot/main.py", "--host", "0.0.0.0", "--port", "8000"]

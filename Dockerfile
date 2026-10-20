FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home /app app

# Dependencias exactas y verificadas por hash (requirements.lock, generado con uv desde
# pyproject.toml): cada build instala lo mismo. Incluye un pip actualizado.
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

COPY pyproject.toml ./
COPY src ./src
# pip no hace falta en ejecución: se desinstala para no dejar en la imagen sus librerías
# internas (pip/_vendor), que los escáneres reportan aparte.
RUN pip install --no-deps . && pip uninstall -y pip

COPY alembic.ini ./
COPY migrations ./migrations
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER app
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]

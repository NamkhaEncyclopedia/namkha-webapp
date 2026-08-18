FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

RUN pip install poetry==2.4.1

WORKDIR /srv
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-ansi


FROM python:3.13-slim

# Only the project venv – Poetry itself and its dependency tree stay in the
# builder. pyvenv.cfg points at /usr/local/bin/python3.13, present in both stages.
COPY --from=builder /srv/.venv /srv/.venv
ENV PATH="/srv/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 namkha
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
USER namkha

EXPOSE 8080
# One worker only: no --workers here. The resolved time zone, the session
# tokens and the result handles all live in this process's memory, so a second
# worker would be handed forms it never issued a ticket or a token for.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]

FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /uvx /bin/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

RUN groupadd --gid 10001 medipet \
    && useradd --uid 10001 --gid medipet --create-home medipet \
    && mkdir /app \
    && chown medipet:medipet /app

WORKDIR /app

COPY --chown=medipet:medipet pyproject.toml uv.lock ./

USER medipet
RUN uv sync --frozen --no-dev

COPY --chown=medipet:medipet . .

EXPOSE 8000

CMD ["uvicorn", "medipet.delivery.http:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PRODKIT_API_HOST=0.0.0.0

RUN pip install --no-cache-dir uv==0.11.21
WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY packages ./packages
RUN uv sync --frozen --no-dev --all-packages

COPY schemas ./schemas
COPY deploy ./deploy

RUN useradd --create-home --uid 10001 prodkit && chown -R prodkit:prodkit /app
USER prodkit

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "prodkit-control-api"]

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first: application edits do not invalidate this layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY owl_mind ./owl_mind

# Non-root. No secrets are baked in; configuration arrives through the
# environment at run time.
RUN useradd --create-home --uid 10001 owlmind \
    && chown -R owlmind:owlmind /app
USER owlmind

EXPOSE 8080

CMD ["uvicorn", "owl_mind.api.main:app", "--host", "0.0.0.0", "--port", "8080"]

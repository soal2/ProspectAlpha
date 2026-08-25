FROM python:3.12

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY configs ./configs
COPY scripts ./scripts
COPY skills ./skills
COPY src ./src

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -e ".[pipeline]" \
    && python -m pip install --no-deps opencv-python-headless \
    && chmod +x scripts/start_render.sh

EXPOSE 8000

CMD ["uvicorn", "riskagent.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

ARG PYTHON_IMAGE=docker.arvancloud.ir/python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 bot

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=bot:bot bot.py ./

USER bot

CMD ["python", "bot.py"]

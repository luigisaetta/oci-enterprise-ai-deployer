# Author: L. Saetta
# Version: 0.9.0
# Last modified: 2026-05-10
# License: MIT

FROM docker:27-cli AS docker-cli

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

COPY requirements.txt pyproject.toml README.md LICENSE ./
COPY enterprise_ai_deployment ./enterprise_ai_deployment
COPY oci_ai_deploy.py ./oci_ai_deploy.py

RUN python -m pip install --upgrade pip \
    && python -m pip install -e .

EXPOSE 8100

CMD ["python", "-m", "uvicorn", "enterprise_ai_deployment.api:app", "--host", "0.0.0.0", "--port", "8100"]

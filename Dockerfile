FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin app

COPY --chown=app:app fritz_lan_dns ./fritz_lan_dns

RUN mkdir -p /app/data /app/db /app/dns \
    && chown app:app /app/data /app/db /app/dns

USER app

VOLUME ["/app/data", "/app/db", "/app/dns"]
EXPOSE 8346

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('WEB_PORT', '8346'), timeout=5)"

CMD ["python", "-m", "fritz_lan_dns", "web"]

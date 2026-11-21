# ImpactText: one small container. The database and its secrets live in /data (mount a disk there).
FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 IMPACT_DB=/data/impact.db
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY eval ./eval
RUN mkdir -p /data && useradd --uid 10001 --no-create-home impact && chown impact /data
USER impact
EXPOSE 8000
# --proxy-headers: behind the host's HTTPS proxy, use the visitor's real address for sign-in limits
# (otherwise everyone shares the proxy's address and one person's typos lock out the whole team)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]

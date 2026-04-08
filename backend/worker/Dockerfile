FROM python:3.12-slim

WORKDIR /app
COPY backend/worker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

CMD ["python", "-m", "backend.worker.run_worker"]

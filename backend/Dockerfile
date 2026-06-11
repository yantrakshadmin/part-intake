FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# API:    uvicorn app.main:app --host 0.0.0.0 --port 8000
# Worker: celery -A app.worker worker --loglevel=info
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

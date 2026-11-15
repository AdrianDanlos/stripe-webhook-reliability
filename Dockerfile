FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi uvicorn stripe
COPY app.py .
ENV DB_PATH=/data/events.db
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

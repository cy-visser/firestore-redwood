FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY sync_churn_to_firestore.py ./

# Run Reverse-ETL sync pipeline by default
ENTRYPOINT ["python", "sync_churn_to_firestore.py"]

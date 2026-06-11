FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY Backend ./Backend
COPY Frontend ./Frontend

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "Backend.app:app"]

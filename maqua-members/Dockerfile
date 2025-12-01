FROM python:3.11-slim

WORKDIR /app

# Install app dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt gunicorn

# Copy application code
COPY . /app

# Runtime env
ENV FLASK_APP=app.py \
    FLASK_DEBUG=0 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Taipei

EXPOSE 4015

# Run with Gunicorn for production-like stability
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:4015", "app:app"]
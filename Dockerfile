FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update     && apt-get install -y --no-install-recommends        build-essential        libpq-dev        libpango-1.0-0        libpangoft2-1.0-0        libjpeg62-turbo        libopenjp2-7        libffi-dev        shared-mime-info     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

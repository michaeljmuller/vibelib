FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

COPY bootstrap/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ ./common/
COPY bootstrap/*.py ./

CMD ["python", "bootstrap.py"]

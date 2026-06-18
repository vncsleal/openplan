FROM python:3.14-slim

WORKDIR /app

COPY services/telemetry/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python3", "run_api.py"]

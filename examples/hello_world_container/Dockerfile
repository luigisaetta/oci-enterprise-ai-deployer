# Author: L. Saetta
# Version: 0.1.0
# Last modified: 2026-04-30
# License: MIT

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app.py /app/app.py

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]

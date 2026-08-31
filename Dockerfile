FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install --with-deps chromium
COPY backend /app/backend
WORKDIR /app/backend
ENV DATA_FILE=/data/data.json
ENV BROWSER_PROFILE=/data/browser-profile
VOLUME ["/data"]
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8000"]

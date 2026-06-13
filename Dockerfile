FROM python:3.12-slim

WORKDIR /app
COPY app/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./

ENV PORT=8080
ENV PANSO_ROOT=/data

EXPOSE 8080
CMD ["python", "tools_api.py"]

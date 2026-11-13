FROM python:3.15-slim
RUN pip install --no-cache-dir uvicorn==0.32.1
WORKDIR /app
COPY app.py .
USER 65532
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

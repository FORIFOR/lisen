FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY content ./content
COPY scripts ./scripts
# Audio is generated on macOS (`say`) before building the image; containers can't synthesize speech.
COPY media ./media
ENV LISTENFLOW_DATA_DIR=/data
VOLUME /data
EXPOSE 8765
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]

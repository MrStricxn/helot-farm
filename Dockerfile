FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends android-tools-adb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY helot/ helot/

EXPOSE 5000
ENV HOST=0.0.0.0 PORT=5000
CMD ["python", "-m", "helot.server"]

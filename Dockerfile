FROM python:3.11-slim

# System deps: Xvfb + draw.io CLI for PNG export
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    wget \
    libgbm1 \
    libgtk-3-0 \
    libasound2 \
    libxss1 \
    && rm -rf /var/lib/apt/lists/*

# Install draw.io CLI (headless diagram export)
ARG DRAWIO_VERSION=24.2.5
RUN wget -q "https://github.com/jgraph/drawio-desktop/releases/download/v${DRAWIO_VERSION}/drawio-amd64-${DRAWIO_VERSION}.deb" \
    -O /tmp/drawio.deb \
    && dpkg -i /tmp/drawio.deb || apt-get install -f -y \
    && rm /tmp/drawio.deb

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /tmp/diagrams

EXPOSE 8080

CMD ["uvicorn", "drawing_agent_server:app", "--host", "0.0.0.0", "--port", "8080"]

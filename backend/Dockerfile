FROM python:3.11-slim

WORKDIR /app

# Dependências mínimas essenciais do sistema para decodificação de imagem/vídeo e streaming de áudio
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Diretório para persistência de snapshots, gravações e banco SQLite
RUN mkdir -p snapshots recordings data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y \
    python3-tk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia e instala as dependências do Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código do projeto
COPY . .

# --- ADICIONE ESTA LINHA ABAIXO ---
# Ela força o gRPC a se compilar usando a versão exata de dentro do container
RUN python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. sdwb.proto

CMD ["python", "main.py"]
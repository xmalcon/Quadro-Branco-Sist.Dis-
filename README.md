# Shared Distributed Write Board (SDWB)

Implementacao em Python com gRPC, Tkinter e coordenador migrante.

## Estrutura

- `main.py`: CLI de entrada.
- `sdwb.proto`: contrato das mensagens e servicos RPC.
- `sdwb_pb2.py` e `sdwb_pb2_grpc.py`: stubs gerados pelo `grpc_tools.protoc`.
- `sdwb_app/backend/name_service.py`: servico de nomes.
- `sdwb_app/backend/coordinator.py`: coordenador do quadro, exclusao mutua e 2PC.
- `sdwb_app/frontend/client_app.py`: interface Tkinter e callback gRPC do cliente.
- `docs/`: documentacao de apoio do trabalho.
- `Dockerfile` e `docker-compose.yml`: simulacao distribuida com containers.

## Dependencias locais

```powershell
pip install -r requirements.txt
```

O Tkinter normalmente ja acompanha a instalacao do Python no Windows.

## Execucao local

Em um terminal, inicie o servico de nomes:

```powershell
python main.py name --port 5000
```

Em outros terminais, abra os clientes:

```powershell
python main.py client --name-service 127.0.0.1:5000
python main.py client --name-service 127.0.0.1:5000
```

No primeiro cliente, clique em `Criar novo quadro`. Esse cliente tambem hospeda o
coordenador e registra seu endereco no servico de nomes. Nos demais, clique em
`Ingressar`.

Para maquinas diferentes na rede:

```powershell
python main.py client --name-service IP_DO_SERVICO_DE_NOMES:5000 --advertise-ip IP_DESTA_MAQUINA
```

## Execucao com Docker

No Windows, instale e inicie um servidor X11, por exemplo VcXsrv, com acesso TCP
habilitado. O Compose usa `DISPLAY=host.docker.internal:0.0` por padrao.

Suba o servico de nomes e tres clientes, cada cliente em seu proprio container:

```powershell
docker-compose up --build names client1 client2 client3
```


```

Cada cliente anuncia seu hostname Docker (`client1`, `client2`, `client3`) no servico
de nomes. Assim, os containers se comunicam pela rede bridge `sdwb`, simulando hosts
separados.

Para testar morte do coordenador, crie o quadro no `client1` e derrube o container:

```powershell
docker-compose stop client1
```

Os demais clientes detectam a falha e elegem o participante com maior prioridade local.

## Funcionalidades atendidas

- Descoberta via servico de nomes, sem IP fixo do coordenador nos clientes.
- Entrada dinamica com sincronizacao do estado atual do quadro.
- Desenho de linhas e quadrados.
- Selecao de objeto para alterar cor ou remover.
- Duas cores disponiveis na interface.
- Replicacao das atualizacoes para todos os clientes do quadro.
- Exclusao mutua por objeto para alteracao de cor e remocao.
- Transacao simples em duas fases: `PrepareAction` e `CommitAction`/`AbortAction`.
- Deteccao de falha do coordenador por snapshot periodico.
- Eleicao estilo Valentao simplificada: o cliente de maior prioridade vira coordenador
  e atualiza o servico de nomes.

## Regenerar o proto

```powershell
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. sdwb.proto
```

import threading
from concurrent import futures

import grpc

import sdwb_pb2 as pb
import sdwb_pb2_grpc as rpc


class NameDiscoveryServicer(rpc.NameDiscoveryServiceServicer):
    def __init__(self):
        self.boards = {}
        self.lock = threading.RLock()

    def RegisterCoordinator(self, request, context):
        with self.lock:
            self.boards[request.board.nome_servico] = request.board
        print(
            f"[names] {request.board.nome_servico} -> "
            f"{request.board.ip}:{request.board.porta}",
            flush=True,
        )
        return pb.RegisterResponse(success=True, message="registered")

    def GetBoards(self, request, context):
        with self.lock:
            return pb.BoardListResponse(boards=list(self.boards.values()))


def serve_name_service(port):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    rpc.add_NameDiscoveryServiceServicer_to_server(NameDiscoveryServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    print(f"Servico de nomes em 0.0.0.0:{port}", flush=True)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(0)

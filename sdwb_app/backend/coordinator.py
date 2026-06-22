import threading
from dataclasses import dataclass

import grpc

import sdwb_pb2 as pb
import sdwb_pb2_grpc as rpc
from sdwb_app.common import clone_object, target
from concurrent.futures import ThreadPoolExecutor, as_completed

class BoardCoordinatorServicer(rpc.BoardCoordinatorServiceServicer):
    def __init__(
        self,
        board_name,
        coordinator_id,
        initial_state=None,
        initial_participants=None,
    ):
        self.board_name = board_name
        self.coordinator_id = coordinator_id
        self.objects = {
            obj.object_id: clone_object(obj)
            for obj in (initial_state or [])
        }
        self.participants = {
            p.client_id: p
            for p in (initial_participants or [])
        }
        self.locked_objects = set()
        self.lock = threading.RLock()

    def JoinBoard(self, request, context):
        with self.lock:
            self.participants[request.participant.client_id] = request.participant
            snapshot = self._snapshot()
        print(f"[coord] joined {request.participant.client_id}", flush=True)
        return pb.JoinResponse(success=True, snapshot=snapshot, message="joined")

    def SendAction(self, request, context):
        with self.lock:
            # 1. Transformamos o campo 'repeated' em uma lista do Python
            objects_to_process = list(request.objects)
            
            # 2. Se a ação for mudar cor ou remover, precisamos validar todos os objetos
            if request.action in (pb.CHANGE_COLOR, pb.REMOVE):
                # Verifica se pelo menos UM dos objetos da lista já está bloqueado
                for obj in objects_to_process:
                    if obj.object_id in self.locked_objects:
                        return pb.ActionResponse(
                            success=False,
                            error_message="Um ou mais objetos ja estao em uso por outra transacao.",
                        )
                
                # Se nenhum estava bloqueado, bloqueia todos eles para realizar a transação
                for obj in objects_to_process:
                    self.locked_objects.add(obj.object_id)

            # --- Daqui para baixo continua a lógica do 2PC (Two-Phase Commit) ---
            # 3. Dispara o Prepare para os participantes
            success, msg = self._prepare_all(request)
            
            if success:
                # Se todos votaram SIM, aplica localmente no dicionário do coordenador
                for obj in objects_to_process:
                    if request.action in (pb.DRAW, pb.CHANGE_COLOR):
                        self.objects[obj.object_id] = clone_object(obj)
                    elif request.action == pb.REMOVE:
                        self.objects.pop(obj.object_id, None)
                
                self._commit_all(request)
            else:
                self._abort_all(request)

            # 4. Libera os bloqueios de todos os objetos tratados nesta rodada
            if request.action in (pb.CHANGE_COLOR, pb.REMOVE):
                for obj in objects_to_process:
                    self.locked_objects.discard(obj.object_id)

            return pb.ActionResponse(success=success, error_message=msg)
                                

    def GetSnapshot(self, request, context):
        with self.lock:
            return self._snapshot()

    def Ping(self, request, context):
        return pb.Empty()

    def StartElection(self, request, context):
        my_priority = -1
        with self.lock:
            me = self.participants.get(self.coordinator_id)
            if me:
                my_priority = me.priority
        return pb.ElectionResponse(
            ack=my_priority > request.candidate_priority,
            coordinator_id=self.coordinator_id,
        )

    def _snapshot(self):
        return pb.SnapshotResponse(
            current_state=list(self.objects.values()),
            participants=list(self.participants.values()),
            coordinator_id=self.coordinator_id,
        )

    def _apply_action(self, request):
        obj = clone_object(request.object)
        if request.action in (pb.DRAW, pb.CHANGE_COLOR):
            self.objects[obj.object_id] = obj
        elif request.action == pb.REMOVE:
            self.objects.pop(obj.object_id, None)

    def _live_participants(self):
        with self.lock:
            return list(self.participants.values())

    def _prepare_all(self, request):
        participants = self._live_participants()
        if not participants:
            return True, ""

        success = True
        error_msg = ""
        failed_participants = []

        def prepare_one(participant):
            try:
                with grpc.insecure_channel(target(participant.ip, participant.porta)) as channel:
                    stub = rpc.ClientUpdateServiceStub(channel)
                    vote = stub.PrepareAction(request, timeout=1.0)
                    return participant, vote.prepared, vote.message
            except grpc.RpcError:
                return participant, False, "RPC Error"

        # Dispara os pedidos em paralelo para todos os clientes ao mesmo tempo
        with ThreadPoolExecutor(max_workers=max(1, len(participants))) as executor:
            futures = [executor.submit(prepare_one, p) for p in participants]
            for future in as_completed(futures):
                p, prepared, msg = future.result()
                if not prepared:
                    success = False
                    error_msg = msg
                    if msg == "RPC Error":
                        failed_participants.append(p)

        # Remove participantes caídos
        if failed_participants:
            with self.lock:
                for p in failed_participants:
                    # SÓ REMOVE SE NÃO FOR O PRÓPRIO COORDENADOR
                    if p.client_id != self.coordinator_id:
                        self.participants.pop(p.client_id, None)

        return success, error_msg

    def _commit_all(self, request):
        participants = self._live_participants()
        if not participants:
            return

        failed_participants = []

        def commit_one(participant):
            try:
                with grpc.insecure_channel(target(participant.ip, participant.porta)) as channel:
                    rpc.ClientUpdateServiceStub(channel).CommitAction(request, timeout=1.0)
            except grpc.RpcError:
                return participant
            return None

        # Dispara os commits em paralelo
        with ThreadPoolExecutor(max_workers=max(1, len(participants))) as executor:
            futures = [executor.submit(commit_one, p) for p in participants]
            for future in as_completed(futures):
                res = future.result()
                if res:
                    failed_participants.append(res)

        if failed_participants:
            with self.lock:
                for p in failed_participants:
                    # SÓ REMOVE SE NÃO FOR O PRÓPRIO COORDENADOR
                    if p.client_id != self.coordinator_id:
                        self.participants.pop(p.client_id, None)

    def _abort_all(self, request):
        for participant in self._live_participants():
            try:
                with grpc.insecure_channel(target(participant.ip, participant.porta)) as channel:
                    rpc.ClientUpdateServiceStub(channel).AbortAction(request, timeout=1.0)
            except grpc.RpcError:
                pass


@dataclass
class CoordinatorHandle:
    server: grpc.Server
    servicer: BoardCoordinatorServicer
    port: int

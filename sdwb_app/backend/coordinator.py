import threading
from dataclasses import dataclass

import grpc

import sdwb_pb2 as pb
import sdwb_pb2_grpc as rpc
from sdwb_app.common import clone_object, target


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
            object_id = request.object.object_id
            if request.action in (pb.CHANGE_COLOR, pb.REMOVE):
                if object_id in self.locked_objects:
                    return pb.ActionResponse(
                        success=False,
                        error_message="Objeto ja esta em uso por outra transacao.",
                    )
                self.locked_objects.add(object_id)

        try:
            prepared, error = self._prepare_all(request)
            if not prepared:
                self._abort_all(request)
                return pb.ActionResponse(success=False, error_message=error)
            with self.lock:
                self._apply_action(request)
            self._commit_all(request)
            return pb.ActionResponse(success=True)
        finally:
            if request.action in (pb.CHANGE_COLOR, pb.REMOVE):
                with self.lock:
                    self.locked_objects.discard(request.object.object_id)

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
        for participant in self._live_participants():
            try:
                with grpc.insecure_channel(target(participant.ip, participant.porta)) as channel:
                    stub = rpc.ClientUpdateServiceStub(channel)
                    vote = stub.PrepareAction(request, timeout=1.0)
                    if not vote.prepared:
                        return False, vote.message
            except grpc.RpcError:
                with self.lock:
                    self.participants.pop(participant.client_id, None)
        return True, ""

    def _commit_all(self, request):
        for participant in self._live_participants():
            try:
                with grpc.insecure_channel(target(participant.ip, participant.porta)) as channel:
                    rpc.ClientUpdateServiceStub(channel).CommitAction(request, timeout=1.0)
            except grpc.RpcError:
                with self.lock:
                    self.participants.pop(participant.client_id, None)

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

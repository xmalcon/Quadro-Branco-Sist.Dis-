import threading
import time
import uuid
from concurrent import futures

import grpc
import tkinter as tk
from tkinter import messagebox, simpledialog

import sdwb_pb2 as pb
import sdwb_pb2_grpc as rpc
from sdwb_app.backend.coordinator import BoardCoordinatorServicer, CoordinatorHandle
from sdwb_app.common import (
    BIND_ADDRESS,
    COLOR_A,
    COLOR_B,
    clone_object,
    free_port,
    participant_key,
    point,
    target,
)


class ClientUpdateServicer(rpc.ClientUpdateServiceServicer):
    def __init__(self, app):
        self.app = app
        self.pending = set()

    def PrepareAction(self, request, context):
        self.pending.add(request.transaction_id)
        return pb.VoteResponse(prepared=True, message="prepared")

    def CommitAction(self, request, context):
        self.pending.discard(request.transaction_id)
        self.app.root.after(0, self.app.apply_remote_action, request)
        return pb.Empty()

    def AbortAction(self, request, context):
        self.pending.discard(request.transaction_id)
        return pb.Empty()

    def GetLocalSnapshot(self, request, context):
        return self.app.snapshot()


class SDWBClientApp:
    def __init__(self, name_service_address, advertise_ip):
        self.name_service_address = name_service_address
        self.client_id = f"c{int(time.time() * 1000) % 100000}-{uuid.uuid4().hex[:4]}"
        self.priority = int(time.time() * 1000) % 1000000
        self.ip = advertise_ip
        self.callback_port = free_port()
        self.board_name = None
        self.coordinator = None
        self.coordinator_handle = None
        self.objects = {}
        self.canvas_items = {}
        self.selected_object_id = None
        self.current_tool = "line"
        self.current_color = COLOR_A
        self.first_point = None
        self.participants = {}
        self.running = True

        self.callback_server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
        rpc.add_ClientUpdateServiceServicer_to_server(
            ClientUpdateServicer(self), self.callback_server
        )
        bound_port = self.callback_server.add_insecure_port(f"{BIND_ADDRESS}:{self.callback_port}")
        if bound_port == 0:
            raise RuntimeError(f"Nao foi possivel abrir callback em {BIND_ADDRESS}:{self.callback_port}")
        self.callback_server.start()

        self.root = tk.Tk()
        self.root.title(f"SDWB - {self.client_id}")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        threading.Thread(target=self._monitor_coordinator, daemon=True).start()

    def run(self):
        self.root.mainloop()

    def _build_ui(self):
        toolbar = tk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        tk.Button(toolbar, text="Criar novo quadro", command=self.create_board).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Ingressar", command=self.join_board).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Linha", command=lambda: self.set_tool("line")).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Quadrado", command=lambda: self.set_tool("square")).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Selecionar", command=lambda: self.set_tool("select")).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Cor A", bg=COLOR_A, fg="white", command=lambda: self.set_color(COLOR_A)).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Cor B", bg=COLOR_B, fg="white", command=lambda: self.set_color(COLOR_B)).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Aplicar cor", command=self.change_color).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Remover", command=self.remove_selected).pack(side=tk.LEFT)

        self.status = tk.StringVar(value=f"Cliente {self.client_id} aguardando quadro")
        tk.Label(self.root, textvariable=self.status, anchor="w").pack(fill=tk.X)

        self.canvas = tk.Canvas(self.root, width=900, height=560, bg="white")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Associações de eventos corrigidas para clique, arrastar e soltar
        self.canvas.bind("<Button-1>", self.on_start_draw)
        self.canvas.bind("<B1-Motion>", self.on_drag_draw)
        self.canvas.bind("<ButtonRelease-1>", self.on_end_draw)

    def set_tool(self, tool):
        self.current_tool = tool
        self.first_point = None
        self.status.set(f"Ferramenta: {tool}")

    def set_color(self, color):
        self.current_color = color
        self.status.set(f"Cor selecionada: {color}")

    def name_stub(self):
        channel = grpc.insecure_channel(self.name_service_address)
        grpc.channel_ready_future(channel).result(timeout=2.0)
        return channel, rpc.NameDiscoveryServiceStub(channel)

    def coordinator_stub(self):
        if not self.coordinator:
            raise RuntimeError("Nenhum coordenador selecionado.")
        channel = grpc.insecure_channel(target(self.coordinator.ip, self.coordinator.porta))
        grpc.channel_ready_future(channel).result(timeout=2.0)
        return channel, rpc.BoardCoordinatorServiceStub(channel)

    def participant(self):
        return pb.Participant(
            client_id=self.client_id,
            ip=self.ip,
            porta=self.callback_port,
            priority=self.priority,
        )

    def create_board(self):
        board_name = simpledialog.askstring("Novo quadro", "Nome do quadro:")
        if not board_name:
            return
        self.board_name = board_name
        port = free_port()
        self._start_local_coordinator(port, [], [self.participant()])
        self.coordinator = pb.BoardInfo(nome_servico=board_name, ip=self.ip, porta=port)
        self._register_coordinator(self.coordinator)
        self.participants[self.client_id] = self.participant()
        self.status.set(f"Coordenador local de {board_name} em {self.ip}:{port}")

    def join_board(self):
        try:
            channel, stub = self.name_stub()
            boards = list(stub.GetBoards(pb.Empty(), timeout=2.0).boards)
            channel.close()
        except Exception as exc:
            messagebox.showerror(
                "Erro",
                f"Servico de nomes indisponivel em {self.name_service_address}: {exc}",
            )
            return

        if not boards:
            messagebox.showinfo("Quadros", "Nenhum quadro registrado.")
            return

        chosen = self._choose_board(boards)
        if not chosen:
            return
        self.board_name = chosen.nome_servico
        self.coordinator = chosen
        try:
            channel, stub = self.coordinator_stub()
            response = stub.JoinBoard(pb.JoinRequest(participant=self.participant()), timeout=3.0)
            channel.close()
        except Exception as exc:
            messagebox.showerror(
                "Erro",
                f"Falha ao ingressar em {chosen.ip}:{chosen.porta}: {exc}",
            )
            return

        self._load_snapshot(response.snapshot)
        self.status.set(f"Conectado ao quadro {self.board_name}")

    def _choose_board(self, boards):
        dialog = tk.Toplevel(self.root)
        dialog.title("Escolha um quadro")
        listbox = tk.Listbox(dialog, width=70)
        listbox.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        for board in boards:
            listbox.insert(tk.END, f"{board.nome_servico} - {board.ip}:{board.porta}")
        result = {"board": None}

        def confirm():
            selected = listbox.curselection()
            if selected:
                result["board"] = boards[selected[0]]
            dialog.destroy()

        tk.Button(dialog, text="Entrar", command=confirm).pack(pady=8)
        dialog.transient(self.root)
        dialog.grab_set()
        self.root.wait_window(dialog)
        return result["board"]

    def _register_coordinator(self, board):
        channel, stub = self.name_stub()
        stub.RegisterCoordinator(pb.RegisterRequest(board=board), timeout=2.0)
        channel.close()

    def _start_local_coordinator(self, port, state, participants):
        if self.coordinator_handle:
            self.coordinator_handle.server.stop(0)
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=12))
        servicer = BoardCoordinatorServicer(
            self.board_name,
            self.client_id,
            initial_state=state,
            initial_participants=participants,
        )
        rpc.add_BoardCoordinatorServiceServicer_to_server(servicer, server)
        bound_port = server.add_insecure_port(f"{BIND_ADDRESS}:{port}")
        if bound_port == 0:
            raise RuntimeError(f"Nao foi possivel abrir coordenador em {BIND_ADDRESS}:{port}")
        server.start()
        self.coordinator_handle = CoordinatorHandle(server, servicer, port)

    def on_start_draw(self, event):
        if not self.board_name:
            messagebox.showinfo("Quadro", "Crie ou ingresse em um quadro primeiro.")
            return

        if self.current_tool == "select":
            self.select_nearest(event.x, event.y)
            return

        # Guarda o ponto inicial do desenho
        self.first_point = (event.x, event.y)
        self.preview_item = None

    def on_drag_draw(self, event):
        if not self.board_name or self.current_tool == "select" or not self.first_point:
            return

        x0, y0 = self.first_point

        # Apaga o rascunho anterior para não acumular sujeira na tela
        if hasattr(self, "preview_item") and self.preview_item:
            self.canvas.delete(self.preview_item)

        # Desenha um rascunho pontilhado (dash) em tempo real
        if self.current_tool == "line":
            self.preview_item = self.canvas.create_line(
                x0, y0, event.x, event.y, fill=self.current_color, width=2, dash=(4, 4)
            )
        elif self.current_tool == "square":
            self.preview_item = self.canvas.create_rectangle(
                x0, y0, event.x, event.y, outline=self.current_color, width=2, dash=(4, 4)
            )

    def on_end_draw(self, event):
        if not self.board_name or self.current_tool == "select" or not self.first_point:
            return

        # Limpa o último rascunho temporário da tela
        if hasattr(self, "preview_item") and self.preview_item:
            self.canvas.delete(self.preview_item)
            self.preview_item = None

        x0, y0 = self.first_point
        self.first_point = None  # Reseta o ponto inicial

        # Só envia se o usuário realmente arrastou o mouse
        if abs(x0 - event.x) < 2 and abs(y0 - event.y) < 2:
            return

        object_type = pb.LINE if self.current_tool == "line" else pb.SQUARE
        obj = pb.BoardObject(
            object_id=uuid.uuid4().hex,
            type=object_type,
            points=[point(x0, y0), point(event.x, event.y)],
            color=self.current_color,
        )
        self.send_action(pb.DRAW, [obj])

    def send_action(self, action_type, board_objects_list):
        request = pb.ActionRequest(
            transaction_id=uuid.uuid4().hex,
            action=action_type,
            objects=board_objects_list,
        )
        try:
            channel, stub = self.coordinator_stub()
            response = stub.SendAction(request, timeout=4.0)
            channel.close()
        except (grpc.RpcError, RuntimeError) as exc:
            self.status.set(f"Falha no coordenador: {exc}")
            return
        if not response.success:
            messagebox.showerror("Transacao recusada", response.error_message)

    def apply_remote_action(self, request):
        # Como o request agora traz uma lista 'objects', iteramos sobre ela
        for obj in request.objects:
            if request.action in (pb.DRAW, pb.CHANGE_COLOR):
                self.objects[obj.object_id] = clone_object(obj)
            elif request.action == pb.REMOVE:
                self.objects.pop(obj.object_id, None)
                if self.selected_object_id == obj.object_id:
                    self.selected_object_id = None
        self.redraw()

    def change_color(self):
        if not self.selected_object_id:
            messagebox.showinfo("Selecao", "Selecione um objeto primeiro.")
            return
        obj = clone_object(self.objects[self.selected_object_id])
        obj.color = self.current_color
        self.send_action(pb.CHANGE_COLOR, [obj])

    def remove_selected(self):
        if not self.selected_object_id:
            messagebox.showinfo("Selecao", "Selecione um objeto primeiro.")
            return
        self.send_action(pb.REMOVE, [self.objects[self.selected_object_id]])

    def select_nearest(self, x, y):
        item = self.canvas.find_closest(x, y)
        if not item:
            return
        for object_id, canvas_item in self.canvas_items.items():
            if canvas_item == item[0]:
                self.selected_object_id = object_id
                self.redraw()
                self.status.set(f"Selecionado: {object_id[:8]}")
                return

    def redraw(self):
        self.canvas.delete("all")
        self.canvas_items.clear()
        for object_id, obj in self.objects.items():
            if len(obj.points) < 2:
                continue
            p0, p1 = obj.points[0], obj.points[1]
            width = 4 if object_id == self.selected_object_id else 2
            if obj.type == pb.LINE:
                item = self.canvas.create_line(p0.x, p0.y, p1.x, p1.y, fill=obj.color, width=width)
            else:
                item = self.canvas.create_rectangle(p0.x, p0.y, p1.x, p1.y, outline=obj.color, width=width)
            self.canvas_items[object_id] = item

    def snapshot(self):
        return pb.SnapshotResponse(
            current_state=list(self.objects.values()),
            participants=list(self.participants.values()),
            coordinator_id=self.coordinator.nome_servico if self.coordinator else "",
        )

    def _load_snapshot(self, snapshot):
        self.objects = {obj.object_id: clone_object(obj) for obj in snapshot.current_state}
        self.participants = {p.client_id: p for p in snapshot.participants}
        self.participants[self.client_id] = self.participant()
        self.redraw()

    def _monitor_coordinator(self):
        while self.running:
            time.sleep(2.0)
            if not self.board_name or not self.coordinator:
                continue
            try:
                channel, stub = self.coordinator_stub()
                snapshot = stub.GetSnapshot(pb.Empty(), timeout=1.0)
                channel.close()
                self.participants = {p.client_id: p for p in snapshot.participants}
                self.participants[self.client_id] = self.participant()
            except Exception:
                self.root.after(0, self.status.set, "Coordenador falhou; iniciando eleicao.")
                self._elect_new_coordinator()

    def _elect_new_coordinator(self):
        candidates = list(self.participants.values()) or [self.participant()]
        winner = max(candidates, key=participant_key)
        if winner.client_id == self.client_id:
            port = free_port()
            self._start_local_coordinator(port, list(self.objects.values()), candidates)
            self.coordinator = pb.BoardInfo(
                nome_servico=self.board_name,
                ip=self.ip,
                porta=port,
            )
            try:
                self._register_coordinator(self.coordinator)
                self.root.after(0, self.status.set, f"Novo coordenador eleito: {self.client_id}")
            except grpc.RpcError:
                self.root.after(0, self.status.set, "Eleito, mas falhou ao registrar no servico de nomes.")
            return

        for _ in range(5):
            time.sleep(1.0)
            try:
                channel, stub = self.name_stub()
                boards = stub.GetBoards(pb.Empty(), timeout=1.0).boards
                channel.close()
                for board in boards:
                    if board.nome_servico == self.board_name:
                        self.coordinator = board
                        self.root.after(0, self.status.set, "Novo coordenador encontrado.")
                        return
            except grpc.RpcError:
                pass

    def close(self):
        self.running = False
        self.callback_server.stop(0)
        if self.coordinator_handle:
            self.coordinator_handle.server.stop(0)
        self.root.destroy()
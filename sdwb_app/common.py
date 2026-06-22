import socket

import sdwb_pb2 as pb


DEFAULT_NAME_SERVICE = "127.0.0.1:5000"
DEFAULT_ADVERTISE_IP = "127.0.0.1"
BIND_ADDRESS = "0.0.0.0"
COLOR_A = "#111827"
COLOR_B = "#dc2626"


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def resolve_advertise_ip(advertise_ip, name_service_address=None):
    if advertise_ip and advertise_ip.lower() != "auto":
        return advertise_ip

    if name_service_address and ":" in name_service_address:
        host, _, port = name_service_address.rpartition(":")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((host, int(port)))
                return sock.getsockname()[0]
        except OSError:
            pass

    return local_ip()


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def target(ip, port):
    return f"{ip}:{port}"


def point(x, y):
    return pb.Point(x=float(x), y=float(y))


def clone_object(obj):
    return pb.BoardObject(
        object_id=obj.object_id,
        type=obj.type,
        points=[point(p.x, p.y) for p in obj.points],
        color=obj.color,
    )


def participant_key(participant):
    return (participant.priority, participant.client_id)

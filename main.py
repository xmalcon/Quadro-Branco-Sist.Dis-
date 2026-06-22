import argparse
import os

from sdwb_app.backend.name_service import serve_name_service
from sdwb_app.common import DEFAULT_ADVERTISE_IP, DEFAULT_NAME_SERVICE, resolve_advertise_ip
from sdwb_app.frontend.client_app import SDWBClientApp


def parse_args():
    parser = argparse.ArgumentParser(description="Shared Distributed Write Board")
    sub = parser.add_subparsers(dest="role", required=True)

    name = sub.add_parser("name", help="inicia o servico de nomes")
    name.add_argument("--port", type=int, default=int(os.getenv("SDWB_NAME_PORT", "5000")))

    client = sub.add_parser("client", help="inicia a interface grafica do cliente")
    client.add_argument(
        "--name-service",
        default=os.getenv("SDWB_NAME_SERVICE", DEFAULT_NAME_SERVICE),
    )
    client.add_argument(
        "--advertise-ip",
        default=os.getenv("SDWB_ADVERTISE_IP", DEFAULT_ADVERTISE_IP),
        help=(
            "IP ou hostname registrado para coordenador/callback. Use 127.0.0.1 "
            "para varios clientes locais, ou auto para Docker."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.role == "name":
        serve_name_service(args.port)
    elif args.role == "client":
        advertise_ip = resolve_advertise_ip(args.advertise_ip, args.name_service)
        SDWBClientApp(args.name_service, advertise_ip).run()


if __name__ == "__main__":
    main()

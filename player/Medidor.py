"""Medidor: recetor de vídeo sem janela, para as medições no cliente.

Fica no lugar do player (por omissão escuta a mesma porta, 25000) e grava uma linha por frame num
CSV: hora de chegada, nº de sequência, hora de envio (o timestamp RTP, em ms, posto pelo
Servidor.py), atraso e tamanho. A análise faz-se depois com scripts/analisar_medicoes.py.

    python3 player/Medidor.py logs/C1-overlay.csv                # atrás do nó OTT (--player)
    python3 player/Medidor.py logs/C1-direto.csv --ip 0.0.0.0    # sem overlay: o Servidor.py envia direto
    python3 player/Medidor.py logs/C1.csv --duracao 60           # para sozinho ao fim de 60 s

Para com Ctrl+C. Cada linha é escrita logo, por isso parar o nó no CORE não perde dados.
"""
from __future__ import annotations

import argparse
import csv
import os
import socket
import sys
import threading
import time

from RtpPacket import RtpPacket, diferenca_ms

CAMPOS = ["chegada_ms", "seq", "envio_ms", "atraso_ms", "bytes"]
INICIO = "# medidor iniciado:"  # 1.ª linha do ficheiro: hora de arranque, em ms
AVISO_S = 5  # de quanto em quanto tempo mostra quantos frames já gravou


def medir(sock: socket.socket, ficheiro, duracao: float | None = None,
          parar: threading.Event | None = None) -> int:
    """Lê pacotes RTP do socket e grava-os no ficheiro (texto) até `duracao` s ou `parar`.

    O socket tem de ter um timeout, para o ciclo poder ver o `parar`. Devolve os frames gravados.
    """
    ficheiro.write(f"{INICIO} {time.time() * 1000:.3f}\n")
    escritor = csv.writer(ficheiro)
    escritor.writerow(CAMPOS)
    ficheiro.flush()
    fim = None if duracao is None else time.monotonic() + duracao
    aviso = time.monotonic() + AVISO_S
    gravados = 0
    while not (parar is not None and parar.is_set()) and (fim is None or time.monotonic() < fim):
        try:
            dados = sock.recv(65535)
        except socket.timeout:
            continue
        chegada = time.time() * 1000
        rtp = RtpPacket()
        rtp.decode(dados)
        envio = rtp.timestamp()
        escritor.writerow([f"{chegada:.3f}", rtp.seqNum(), envio,
                           f"{diferenca_ms(chegada, envio):.3f}", len(rtp.getPayload())])
        ficheiro.flush()
        gravados += 1
        if time.monotonic() >= aviso:
            aviso += AVISO_S
            print(f"{gravados} frames gravados (último: {rtp.seqNum()}, "
                  f"atraso {diferenca_ms(chegada, envio):.1f} ms)", flush=True)
    return gravados


def main() -> None:
    parser = argparse.ArgumentParser(description="Recetor de vídeo sem janela que grava um CSV por frame")
    parser.add_argument("csv", help="ficheiro onde gravar (ex: logs/C1-overlay.csv)")
    parser.add_argument("--porta", type=int, default=25000, help="porta UDP (predefinição: 25000)")
    parser.add_argument("--ip", default="127.0.0.1",
                        help="IP onde escutar (predefinição: 127.0.0.1; 0.0.0.0 para receber de outra máquina)")
    parser.add_argument("--duracao", type=float, default=None, help="segundos até parar (predefinição: Ctrl+C)")
    args = parser.parse_args()

    pasta = os.path.dirname(args.csv)
    if pasta:
        os.makedirs(pasta, exist_ok=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((args.ip, args.porta))
    except OSError as exc:
        sys.exit(f"Não foi possível usar {args.ip}:{args.porta} ({exc})")
    sock.settimeout(0.2)
    print(f"à escuta em {args.ip}:{args.porta}, a gravar em {args.csv}", flush=True)
    with open(args.csv, "w", newline="", encoding="utf-8") as ficheiro:
        try:
            medir(sock, ficheiro, args.duracao)
        except KeyboardInterrupt:
            pass
    sock.close()
    print(f"terminado: ver python3 scripts/analisar_medicoes.py {args.csv}")


if __name__ == "__main__":
    main()

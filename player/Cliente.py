"""Player de vídeo (adaptado do Cliente.py do ProgEx). Processo à parte do nó overlay:
lê pacotes RTP por UDP e mostra o vídeo numa janela.

    python3 player/Cliente.py [porta] [ip]     # por omissão 25000 e 127.0.0.1

O nó overlay do cliente entrega-lhe o vídeo em localhost. Para receber diretamente
de outra máquina (ex: teste no CORE sem overlay), usar ip 0.0.0.0.

A janela mostra no topo o nome do cliente (ex: C1): o que os scripts do CORE põem em OTT_NOME
ou, arrancado à mão numa shell do CORE, o nome da máquina. Os players C1, C2, ... abrem lado a
lado, para não ficarem uns por cima dos outros.
"""
import os
import re
import sys
from tkinter import Tk

from ClienteGUI import NOME, ClienteGUI

LARGURA_JANELA, ALTURA_JANELA = 410, 390  # aproximadas, só para as pôr lado a lado

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 25000
    addr = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"

    root = Tk()
    app = ClienteGUI(root, addr, port)
    if NOME != "-":
        ident = os.environ.get("OTT_ID")
        app.master.title(f"{NOME}{f' [{ident}]' if ident else ''} · player")
        m = re.search(r"(\d+)$", NOME)
        if m:  # C1, C2, C3 na 1.ª fila, C4, C5, ... na 2.ª
            k = int(m.group(1)) - 1
            por_fila = max(1, root.winfo_screenwidth() // LARGURA_JANELA)
            root.geometry(f"+{(k % por_fila) * LARGURA_JANELA}+{(k // por_fila) * ALTURA_JANELA}")
    else:
        app.master.title(f"Player {addr}:{port}")
    root.mainloop()

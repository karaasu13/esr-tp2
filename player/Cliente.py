"""Player de vídeo (adaptado do Cliente.py do ProgEx). Processo à parte do nó overlay:
lê pacotes RTP por UDP e mostra o vídeo numa janela.

    python3 player/Cliente.py [porta] [ip]     # por omissão 25000 e 127.0.0.1

O nó overlay do cliente entrega-lhe o vídeo em localhost. Para receber diretamente
de outra máquina (ex: teste no CORE sem overlay), usar ip 0.0.0.0.
"""
import sys
from tkinter import Tk

from ClienteGUI import ClienteGUI

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 25000
    addr = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"

    root = Tk()
    app = ClienteGUI(root, addr, port)
    app.master.title(f"Player {addr}:{port}")
    root.mainloop()

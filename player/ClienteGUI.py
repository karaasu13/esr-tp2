"""Janela do player (adaptado do ClienteGUI.py do ProgEx).

Recebe pacotes RTP por UDP e mostra cada frame (JPEG). Alterações ao original:
- timeout do socket já não fecha o player: pode arrancar antes do servidor;
- o socket é lido numa thread, mas a janela só é atualizada na thread do Tk (after);
- o frame é aberto em memória, sem ficheiro de cache;
- conta os frames recebidos e em falta (pelo nº de sequência RTP) e mostra-os ao fechar;
- escreve no stdout (que os scripts do CORE guardam no log) um resumo de 5 em 5 s e cada
  paragem do vídeo (mais de 200 ms sem frame novo), com a hora;
- tkMessageBox (Python 2) trocado por tkinter.messagebox;
- um nº de frame que recua muito (troca para um servidor desfasado, ou vídeo que recomeça) não é
  tratado como atrasado: o player continua a partir daí, em vez de ficar parado até apanhar.
"""
from __future__ import annotations

import io
import os
import socket
import threading
import time
from datetime import datetime
import tkinter.messagebox
from tkinter import Button, Label, E, N, S, W

from PIL import Image, ImageTk

from RtpPacket import RtpPacket, avanco_seq, recomecou

ATUALIZAR_MS = 20  # de quanto em quanto tempo a janela vai buscar o último frame
RESUMO_S = 5  # de quanto em quanto tempo escreve o resumo no log
PARAGEM_S = 0.2  # sem frame novo durante mais do que isto (4 frames a 20/s): escreve no log
NOME = os.environ.get("OTT_NOME", "-")  # nome do nó, posto pelos scripts do CORE


def registar(texto: str) -> None:
    """Uma linha no mesmo formato dos logs dos nós, para o painel a ler."""
    agora = datetime.now()
    print(f"{agora:%H:%M:%S}.{agora.microsecond // 1000:03d} {NOME} INFO player: {texto}", flush=True)


class ClienteGUI:
    def __init__(self, master, addr: str, port: int):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.addr = addr
        self.port = int(port)
        self.frameNbr = 0       # último frame mostrado, contado sem a volta dos 16 bits do RTP
        self.ultimo_seq = None  # nº de sequência RTP (16 bits) desse frame
        self.recebidos = 0      # frames aceites
        self.atrasados = 0      # chegaram fora de ordem ou repetidos (descartados)
        self.primeiro = None    # 1.º frame visto (mesma contagem que frameNbr)
        self.ultimo_t = None    # quando chegou o último frame aceite
        self.atrasados_antes = 0  # atrasados até ao último frame aceite
        self.ultimo_resumo = time.monotonic()
        self.ultimo_frame = None
        self.lock = threading.Lock()
        self.parar = threading.Event()
        self.createWidgets()
        self.openRtpPort()
        threading.Thread(target=self.listenRtp, daemon=True).start()
        self.master.after(ATUALIZAR_MS, self.updateMovie)

    def createWidgets(self):
        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=2, sticky=W + E + N + S, padx=5, pady=5)
        self.estado = Label(self.master, text=f"à espera em {self.addr}:{self.port}")
        self.estado.grid(row=1, column=0, padx=2, pady=2, sticky=W)
        self.teardown = Button(self.master, width=20, padx=3, pady=3, text="Sair", command=self.exitClient)
        self.teardown.grid(row=1, column=1, padx=2, pady=2)

    def openRtpPort(self):
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        try:
            self.rtpSocket.bind((self.addr, self.port))
        except OSError as e:
            tkinter.messagebox.showwarning("Unable to Bind", f"Não foi possível usar {self.addr}:{self.port} ({e})")
            raise SystemExit(1)

    def listenRtp(self):
        while not self.parar.is_set():
            try:
                data = self.rtpSocket.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            rtp = RtpPacket()
            rtp.decode(data)
            seq = rtp.seqNum()
            paragem = None
            recuo = None
            with self.lock:
                if self.ultimo_seq is None:  # 1.º frame
                    avanco = 1
                    self.frameNbr = seq - 1
                else:
                    avanco = avanco_seq(seq, self.ultimo_seq)
                    if recomecou(avanco):  # outro servidor ou o vídeo recomeçou: segue daqui
                        recuo, avanco = -avanco, 1
                # descarta pacotes atrasados, como o original, mas aceita a volta 65535 -> 0
                if avanco > 0:
                    agora = time.monotonic()
                    if self.ultimo_t is not None and agora - self.ultimo_t > PARAGEM_S:
                        paragem = (agora - self.ultimo_t, self.frameNbr, self.frameNbr + avanco,
                                   self.atrasados - self.atrasados_antes)
                    self.ultimo_t, self.atrasados_antes = agora, self.atrasados
                    self.ultimo_seq = seq
                    self.frameNbr += avanco
                    if self.recebidos == 0:
                        self.primeiro = self.frameNbr
                    self.recebidos += 1
                    self.ultimo_frame = rtp.getPayload()
                else:
                    self.atrasados += 1
            if recuo:
                registar(f"o nº do frame recuou {recuo} (outro servidor ou o vídeo recomeçou): "
                         "continua a partir daqui")
            if paragem:
                segundos, antes, depois, atrasados = paragem
                registar(f"{segundos * 1000:.0f} ms sem frame novo (frame {antes} → {depois}: "
                         f"{depois - antes - 1} em falta, {atrasados} atrasados)")

    def updateMovie(self):
        with self.lock:
            frame, self.ultimo_frame = self.ultimo_frame, None
            texto = self.resumo()
        if time.monotonic() - self.ultimo_resumo >= RESUMO_S:
            self.ultimo_resumo = time.monotonic()
            registar(texto)
        if frame is not None:
            try:
                photo = ImageTk.PhotoImage(Image.open(io.BytesIO(frame)))
                self.label.configure(image=photo, height=288)
                self.label.image = photo
            except OSError:
                pass  # JPEG estragado: fica o frame anterior
            self.estado.configure(text=texto)
        if not self.parar.is_set():
            self.master.after(ATUALIZAR_MS, self.updateMovie)

    def resumo(self) -> str:
        if self.primeiro is None:
            return "sem frames"
        esperados = self.frameNbr - self.primeiro + 1
        falta = esperados - self.recebidos
        return (f"frame {self.frameNbr} · recebidos {self.recebidos} de {esperados} "
                f"· em falta {falta} ({100 * falta / esperados:.1f} %) · atrasados {self.atrasados}")

    def exitClient(self):
        self.parar.set()
        with self.lock:
            registar(f"a sair: {self.resumo()}")
        self.rtpSocket.close()
        self.master.destroy()

    def handler(self):
        if tkinter.messagebox.askokcancel("Sair?", "Fechar o player?"):
            self.exitClient()

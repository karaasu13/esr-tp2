"""Janela do player (adaptado do ClienteGUI.py do ProgEx).

Recebe pacotes RTP por UDP e mostra cada frame (JPEG). Alterações ao original:
- timeout do socket já não fecha o player: pode arrancar antes do servidor;
- o socket é lido numa thread, mas a janela só é atualizada na thread do Tk (after);
- o frame é aberto em memória, sem ficheiro de cache;
- conta os frames recebidos e em falta (pelo nº de sequência RTP) e mostra-os ao fechar;
- tkMessageBox (Python 2) trocado por tkinter.messagebox.
"""
from __future__ import annotations

import io
import socket
import threading
import tkinter.messagebox
from tkinter import Button, Label, E, N, S, W

from PIL import Image, ImageTk

from RtpPacket import RtpPacket

ATUALIZAR_MS = 20  # de quanto em quanto tempo a janela vai buscar o último frame


class ClienteGUI:
    def __init__(self, master, addr: str, port: int):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.addr = addr
        self.port = int(port)
        self.frameNbr = 0       # último nº de sequência mostrado
        self.recebidos = 0      # frames aceites
        self.atrasados = 0      # chegaram fora de ordem ou repetidos (descartados)
        self.primeiro = None    # 1.º nº de sequência visto
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
            with self.lock:
                if self.primeiro is None:
                    self.primeiro = seq
                if seq > self.frameNbr:  # descarta pacotes atrasados, como o original
                    self.frameNbr = seq
                    self.recebidos += 1
                    self.ultimo_frame = rtp.getPayload()
                else:
                    self.atrasados += 1

    def updateMovie(self):
        with self.lock:
            frame, self.ultimo_frame = self.ultimo_frame, None
            texto = self.resumo()
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
            print(self.resumo())
        self.rtpSocket.close()
        self.master.destroy()

    def handler(self):
        if tkinter.messagebox.askokcancel("Sair?", "Fechar o player?"):
            self.exitClient()

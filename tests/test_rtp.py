"""Nº de sequência RTP (16 bits): o servidor dá a volta e o player aceita-a; o player aceita a
numeração de outro servidor ao fim de 2 frames seguidos."""
import contextlib
import io
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

PLAYER = Path(__file__).resolve().parent.parent / "player"
sys.path.insert(0, str(PLAYER))
try:
    from RtpPacket import RtpPacket, aceitar, avanco_seq
    try:
        from ClienteGUI import ClienteGUI
    except ImportError:  # sem Tkinter ou Pillow
        ClienteGUI = None
finally:
    sys.path.remove(str(PLAYER))


def seq_codificado(frame: int) -> int:
    rtp = RtpPacket()
    rtp.encode(2, 0, 0, 0, frame, 0, 26, 0, b"jpeg")
    lido = RtpPacket()
    lido.decode(bytes(rtp.getPacket()))
    assert lido.getPayload() == b"jpeg"
    return lido.seqNum()


class TestSeqRtp(unittest.TestCase):
    def test_servidor_da_a_volta_em_vez_de_rebentar(self):
        self.assertEqual([seq_codificado(f) for f in range(65534, 65538)], [65534, 65535, 0, 1])

    def test_avanco_normal_repetido_e_atrasado(self):
        self.assertEqual(avanco_seq(11, 10), 1)
        self.assertEqual(avanco_seq(13, 10), 3)  # perderam-se 2 frames
        self.assertEqual(avanco_seq(10, 10), 0)  # repetido
        self.assertLess(avanco_seq(9, 10), 0)    # atrasado

    def test_avanco_na_volta(self):
        self.assertEqual(avanco_seq(0, 65535), 1)
        self.assertEqual(avanco_seq(2, 65534), 4)
        self.assertLess(avanco_seq(65535, 0), 0)  # atrasado de antes da volta


class TestNumeracaoNova(unittest.TestCase):
    """RtpPacket.aceitar: atraso normal contra troca para um servidor com outra numeração."""

    def test_frame_seguinte_e_salto_para_a_frente(self):
        self.assertEqual(aceitar(11, 10, None), (1, None))
        self.assertEqual(aceitar(90, 10, None), (80, None))  # outro servidor à frente: salta

    def test_pouco_atrasado_descarta_sem_suspeitar(self):
        self.assertEqual(aceitar(8, 10, None), (-2, None))
        self.assertEqual(aceitar(5, 10, None), (-5, None))

    def test_dois_frames_seguidos_muito_atras_mudam_a_numeracao(self):
        avanco, suspeito = aceitar(1000, 1080, None)
        self.assertLess(avanco, 0)  # o 1.º ainda é descartado
        self.assertEqual(suspeito, 1001)
        self.assertEqual(aceitar(1001, 1080, suspeito), (1, None))

    def test_um_perdido_muito_atras_nao_muda_a_numeracao(self):
        _, suspeito = aceitar(500, 1080, None)
        self.assertEqual(aceitar(1081, 1080, suspeito), (1, None))  # a numeração antiga continua
        self.assertLess(aceitar(502, 1081, None)[0], 0)

    def test_na_volta_dos_16_bits(self):
        _, suspeito = aceitar(65530, 10, None)
        self.assertEqual(aceitar(65531, 10, suspeito), (1, None))


@unittest.skipUnless(ClienteGUI, "o player precisa do Tkinter e do Pillow")
class TestPlayer(unittest.TestCase):
    def receber(self, frames: list[int]) -> tuple[ClienteGUI, str]:
        """Ciclo de receção do player (sem janela) com estes nºs de frame; devolve o log."""
        g = object.__new__(ClienteGUI)
        g.lock, g.parar = threading.Lock(), threading.Event()
        g.iniciarContagem()
        g.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        g.rtpSocket.bind(("127.0.0.1", 0))
        g.rtpSocket.settimeout(0.1)
        self.addCleanup(g.rtpSocket.close)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as envio:
            for frame in frames:
                rtp = RtpPacket()
                rtp.encode(2, 0, 0, 0, frame, 0, 26, 0, b"jpeg")
                envio.sendto(bytes(rtp.getPacket()), g.rtpSocket.getsockname())
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            leitor = threading.Thread(target=g.listenRtp)
            leitor.start()
            time.sleep(0.3)
            g.parar.set()
            leitor.join()
        return g, log.getvalue()

    def test_player_aceita_a_volta(self):
        """65533..65538, sem o 65537 e com um atrasado."""
        g, _ = self.receber([65533, 65534, 65535, 65536, 65535, 65538])
        self.assertEqual((g.primeiro, g.frameNbr, g.recebidos, g.atrasados), (65533, 65538, 5, 1))

    def test_troca_para_servidor_80_frames_atras(self):
        """Antes, o player descartava o servidor novo até ele passar o 1004 (4 s parado)."""
        g, log = self.receber([1000, 1001, 1002, 1003, 1004, 924, 925, 926, 927, 928])
        self.assertEqual((g.frameNbr, g.recebidos, g.atrasados), (1008, 9, 1))
        self.assertEqual(g.ultimo_seq, 928)
        self.assertIn("numeração nova: seq 925 depois do 1004 (79 frames para trás)", log)


if __name__ == "__main__":
    unittest.main()

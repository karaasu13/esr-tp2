"""Nº de sequência RTP (16 bits): o servidor dá a volta e o player aceita-a."""
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

PLAYER = Path(__file__).resolve().parent.parent / "player"
sys.path.insert(0, str(PLAYER))
try:
    from RtpPacket import RtpPacket, avanco_seq, recomecou
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

    def test_recuo_grande_e_um_recomeco_e_nao_um_atraso(self):
        self.assertFalse(recomecou(avanco_seq(9, 10)))      # 1 frame atrasado: deita-se fora
        self.assertFalse(recomecou(avanco_seq(990, 1000)))  # 10 atrás: ainda é um atraso
        self.assertTrue(recomecou(avanco_seq(960, 1000)))   # 40 atrás: o outro servidor vai atrás
        self.assertTrue(recomecou(avanco_seq(0, 5000)))     # o vídeo recomeçou do início
        self.assertFalse(recomecou(avanco_seq(1040, 1000)))  # para a frente nunca é recomeço

    def test_avanco_na_volta(self):
        self.assertEqual(avanco_seq(0, 65535), 1)
        self.assertEqual(avanco_seq(2, 65534), 4)
        self.assertLess(avanco_seq(65535, 0), 0)  # atrasado de antes da volta

    @unittest.skipUnless(ClienteGUI, "o player precisa do Tkinter e do Pillow")
    def test_player_aceita_a_volta(self):
        """Ciclo de receção do player (sem janela): 65533..65538, sem o 65537 e com um atrasado."""
        g = object.__new__(ClienteGUI)
        g.lock, g.parar = threading.Lock(), threading.Event()
        g.frameNbr, g.ultimo_seq, g.primeiro, g.ultimo_frame = 0, None, None, None
        g.recebidos = g.atrasados = 0
        g.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        g.rtpSocket.bind(("127.0.0.1", 0))
        g.rtpSocket.settimeout(0.1)
        self.addCleanup(g.rtpSocket.close)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as envio:
            for frame in [65533, 65534, 65535, 65536, 65535, 65538]:
                rtp = RtpPacket()
                rtp.encode(2, 0, 0, 0, frame, 0, 26, 0, b"jpeg")
                envio.sendto(bytes(rtp.getPacket()), g.rtpSocket.getsockname())
        leitor = threading.Thread(target=g.listenRtp)
        leitor.start()
        time.sleep(0.3)
        g.parar.set()
        leitor.join()
        self.assertEqual((g.primeiro, g.frameNbr, g.recebidos, g.atrasados), (65533, 65538, 5, 1))


if __name__ == "__main__":
    unittest.main()

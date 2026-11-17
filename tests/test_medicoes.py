"""Medições no cliente: hora de envio no RTP, medidor (CSV) e análise."""
import io
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for pasta in ("player", "scripts"):
    sys.path.insert(0, str(RAIZ / pasta))
try:
    import analisar_medicoes as am
    import debito_interface
    from Medidor import medir
    from RtpPacket import RtpPacket, diferenca_ms, hora_ms
finally:
    for pasta in ("player", "scripts"):
        sys.path.remove(str(RAIZ / pasta))


def pacote(seq: int) -> bytes:
    rtp = RtpPacket()
    rtp.encode(2, 0, 0, 0, seq, 0, 26, 0, b"x" * 100)
    return bytes(rtp.getPacket())


class TestHoraDeEnvio(unittest.TestCase):
    def test_timestamp_rtp_em_ms(self):
        lido = RtpPacket()
        lido.decode(pacote(1))
        self.assertLess(abs(diferenca_ms(time.time() * 1000, lido.timestamp())), 50)

    def test_diferenca_na_volta_dos_32_bits(self):
        self.assertEqual(diferenca_ms(2**32 + 5, 2**32 - 3), 8)  # a chegada já deu a volta
        self.assertEqual(diferenca_ms(5, 2**32 - 3), 8)
        self.assertEqual(diferenca_ms(100, 110), -10)  # relógios desacertados
        self.assertLess(hora_ms(), 2**32)


class TestMedidor(unittest.TestCase):
    def test_grava_um_frame_por_linha_e_a_analise_le(self):
        recetor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recetor.bind(("127.0.0.1", 0))
        recetor.settimeout(0.05)
        self.addCleanup(recetor.close)
        saida, parar = io.StringIO(), threading.Event()
        gravados = []
        leitor = threading.Thread(target=lambda: gravados.append(medir(recetor, saida, parar=parar)))
        leitor.start()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as envio:
            for seq in (65534, 65535, 1, 2):  # volta aos 16 bits e o 0 perdido
                envio.sendto(pacote(seq), recetor.getsockname())
                time.sleep(0.01)
        time.sleep(0.2)
        parar.set()
        leitor.join()
        self.assertEqual(gravados, [4])

        csv = Path(self._tmp()) / "c.csv"
        csv.write_text(saida.getvalue(), encoding="utf-8")
        inicio, frames = am.ler(str(csv))
        self.assertIsNotNone(inicio)
        self.assertEqual([f.seq for f in frames], [65534, 65535, 1, 2])
        r = am.resumir(frames, inicio)
        self.assertEqual((r.recebidos, r.esperados, r.perdidos), (4, 5, 1))
        self.assertTrue(all(0 <= f.atraso_ms < 50 for f in frames))
        self.assertEqual(frames[0].bytes, 100)

    def _tmp(self) -> str:
        import tempfile
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        return pasta.name


def frames_sinteticos(chegadas_e_seqs, atraso=10.0):
    """Frames a 20 por segundo (envio de 50 em 50 ms) com um atraso fixo, salvo indicação."""
    return [am.Frame(chegada, seq, int(seq * 50), atraso, 1000) for chegada, seq in chegadas_e_seqs]


class TestAnalise(unittest.TestCase):
    def test_atraso_fixo_sem_jitter_nem_perda(self):
        frames = frames_sinteticos([(s * 50 + 10, s) for s in range(1, 101)])
        r = am.resumir(frames, inicio_ms=0)
        self.assertEqual((r.recebidos, r.perdidos, r.duplicados, r.fora_de_ordem), (100, 0, 0, 0))
        self.assertAlmostEqual(r.atraso_medio, 10)
        self.assertAlmostEqual(r.jitter, 0)
        self.assertAlmostEqual(r.fps, 20)
        self.assertAlmostEqual(r.kbit_s, 160)  # 1000 B x 20 por segundo
        self.assertAlmostEqual(r.primeiro_frame_s, 0.06)
        self.assertEqual(r.cortes, [])

    def test_perda_repetido_fora_de_ordem_e_corte(self):
        seqs = [1, 2, 4, 3, 3] + list(range(20, 25))  # perdem-se 5 a 19: 750 ms sem vídeo
        frames = frames_sinteticos([(i * 50, s) for i, s in enumerate(seqs[:5])]
                                   + [(250 + 750 + i * 50, s) for i, s in enumerate(seqs[5:])])
        r = am.resumir(frames, inicio_ms=0, corte_ms=300)
        self.assertEqual((r.recebidos, r.esperados, r.perdidos), (9, 24, 15))
        self.assertEqual((r.duplicados, r.fora_de_ordem), (1, 1))
        self.assertEqual(len(r.cortes), 1)
        self.assertAlmostEqual(r.cortes[0][1], 800)

    def test_jitter_rfc3550(self):
        # Atrasos alternados 10 e 20 ms: |D| = 10 em cada par, J tende para 10.
        frames = [am.Frame(s * 50 + (10 if s % 2 else 20), s, s * 50, 0, 1000) for s in range(1, 400)]
        self.assertAlmostEqual(am.resumir(frames).jitter, 10, places=3)

    def test_p95(self):
        self.assertEqual(am.percentil(list(range(1, 101)), 95), 95)
        self.assertEqual(am.percentil([7], 95), 7)


class TestDebitoInterface(unittest.TestCase):
    PROC = (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo:     100       2    0    0    0     0          0         0      100       2    0    0    0     0       0          0\n"
        "  eth0: 1234567    890    0    0    0     0          0         0  7654321    4321    0    0    0     0       0          0\n"
    )

    def test_le_os_contadores_da_interface(self):
        self.assertEqual(debito_interface.contadores(self.PROC, "eth0"), (1234567, 890, 7654321, 4321))
        with self.assertRaises(ValueError):
            debito_interface.contadores(self.PROC, "eth9")


if __name__ == "__main__":
    unittest.main()

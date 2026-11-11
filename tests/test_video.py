"""Pontas do vídeo: Servidor.py -> nó (UDP local) ... nó -> player (UDP local)."""
import asyncio
import contextlib
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import time

from bootstrapper.servidor import Bootstrapper, carregar_configuracao
from ott.__main__ import FluxoVideo, NoOTT
from ott.protocolo import Tipo
from ott.video import PontaVideo

RAIZ = Path(__file__).resolve().parent.parent
FRAMES = [bytes([65 + i]) * (3000 + 500 * i) for i in range(3)]  # "frames" de teste


def porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def receber(sock: socket.socket, timeout: float = 5) -> bytes:
    return await asyncio.wait_for(asyncio.get_running_loop().sock_recv(sock, 65535), timeout)


def player_falso() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    return sock


def seq_rtp(pacote: bytes) -> int:
    return pacote[2] << 8 | pacote[3]


class TestPontaVideo(unittest.IsolatedAsyncioTestCase):
    async def test_entrada_numera_os_videos_pela_ordem_das_portas(self):
        chegaram = []
        ponta = PontaVideo(lambda s, d: chegaram.append((s, d)), portas_servidor=[0, 0])
        await ponta.start()
        self.addAsyncCleanup(lambda: ponta.close())
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"video-1", ("127.0.0.1", ponta.portas[1]))
            s.sendto(b"video-0", ("127.0.0.1", ponta.portas[0]))
        await asyncio.sleep(0.2)
        self.assertCountEqual(chegaram, [(0, b"video-0"), (1, b"video-1")])

    async def test_saida_entrega_o_pacote_intacto(self):
        player = player_falso()
        self.addCleanup(player.close)
        ponta = PontaVideo(lambda *_: None, player=player.getsockname())
        await ponta.start()
        self.addAsyncCleanup(lambda: ponta.close())
        ponta.entregar_ao_player(FRAMES[2])
        self.assertEqual(await receber(player), FRAMES[2])


class TestServidorEmCiclo(unittest.IsolatedAsyncioTestCase):
    async def test_recomeca_no_fim_com_numero_sempre_a_subir(self):
        recetor = player_falso()
        self.addCleanup(recetor.close)
        pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pasta)  # registado antes do Servidor.py: só é apagado depois de ele parar
        filme = Path(pasta) / "teste.Mjpeg"
        filme.write_bytes(b"".join(b"%05d" % len(f) + f for f in FRAMES))
        servidor = subprocess.Popen(
            [sys.executable, str(RAIZ / "player" / "Servidor.py"), str(filme),
             "127.0.0.1", str(recetor.getsockname()[1])],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(servidor.wait)
        self.addCleanup(servidor.terminate)
        pacotes = [await receber(recetor) for _ in range(2 * len(FRAMES) + 1)]
        self.assertEqual([seq_rtp(p) for p in pacotes], list(range(1, 8)))
        self.assertEqual([p[12:] for p in pacotes], FRAMES * 2 + FRAMES[:1])


class _SocketFalso:
    """Guarda o que o Servidor.py enviaria, sem rede."""

    def __init__(self):
        self.enviados: list[bytes] = []

    def sendto(self, dados: bytes, addr) -> None:
        self.enviados.append(dados)

    def close(self) -> None:
        pass


class _ParaAoFimDe:
    """Substitui o threading.Event do Servidor.py: não espera os 50 ms e pede para parar
    quando já foram enviados `n` pacotes. Assim os 500 frames correm em menos de 1 s."""

    def __init__(self, sock: _SocketFalso, n: int):
        self.sock, self.n = sock, n

    def wait(self, _timeout: float) -> bool:
        return False

    def isSet(self) -> bool:
        return len(self.sock.enviados) >= self.n


class TestVideoReal(unittest.TestCase):
    """O videos/movie.Mjpeg do ProgEx, que está no repositório."""

    FILME = RAIZ / "videos" / "movie.Mjpeg"

    def _frames(self) -> list[bytes]:
        dados, frames = self.FILME.read_bytes(), []
        while dados:
            tamanho = int(dados[:5])
            frames.append(dados[5:5 + tamanho])
            dados = dados[5 + tamanho:]
        return frames

    def test_tem_500_frames_jpeg(self):
        frames = self._frames()
        self.assertEqual(len(frames), 500)
        self.assertTrue(all(f.startswith(b"\xff\xd8") for f in frames))  # marcador SOI do JPEG

    def test_video2_tem_os_mesmos_frames_mas_outras_imagens(self):
        originais = self._frames()
        self.FILME = RAIZ / "videos" / "movie2.Mjpeg"
        invertidos = self._frames()
        self.assertEqual(len(invertidos), len(originais))
        self.assertTrue(all(f.startswith(b"\xff\xd8") for f in invertidos))
        self.assertTrue(all(a != b for a, b in zip(originais, invertidos)))

    def test_servidor_passa_do_frame_500_ao_501_com_o_inicio_do_video(self):
        sys.path.insert(0, str(RAIZ / "player"))
        self.addCleanup(sys.path.remove, str(RAIZ / "player"))
        from Servidor import Servidor
        from VideoStream import VideoStream

        sock = _SocketFalso()
        video = VideoStream(str(self.FILME))
        self.addCleanup(video.file.close)
        servidor = Servidor()
        servidor.clientInfo = {"videoStream": video, "rtpAddr": "127.0.0.1", "rtpPort": 25000,
                               "rtpSocket": sock, "event": _ParaAoFimDe(sock, 503)}
        with contextlib.redirect_stdout(io.StringIO()):  # o Servidor.py escreve uma linha por frame
            servidor.sendRtp()

        frames = self._frames()
        self.assertEqual([seq_rtp(p) for p in sock.enviados], list(range(1, 504)))
        self.assertEqual([p[12:] for p in sock.enviados], frames + frames[:3])


class TestVideoDeNoANo(unittest.IsolatedAsyncioTestCase):
    async def test_renova_join_sem_fiabilidade_e_expira_destino(self):
        no = NoOTT("127.0.0.1")
        no.transporte.set_id(1)
        fluxo_renovado = (2, 0)
        fluxo_expirado = (3, 0)
        agora = time.monotonic()
        no._registado = True
        no.pais_video[fluxo_renovado] = 2
        no.tabela_fluxos[fluxo_renovado] = FluxoVideo(pai=2, local=True)
        no._ultima_renovacao[fluxo_renovado] = agora - 3
        no.tabela_fluxos[fluxo_expirado] = FluxoVideo(destinos={4: agora - 1})
        no.rotas_video[fluxo_expirado] = {4}
        enviados = []
        renovado = asyncio.Event()

        def enviar(destino, tipo, _corpo, **opcoes):
            enviados.append((destino, tipo, opcoes))
            if tipo == Tipo.JOIN and destino == 2:
                renovado.set()

        no.transporte.send = enviar
        tarefa = asyncio.create_task(no._ciclo_fluxos())
        try:
            await asyncio.wait_for(renovado.wait(), 1)
        finally:
            tarefa.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await tarefa

        self.assertIn((2, Tipo.JOIN, {"fiavel": False, "fluxo": fluxo_renovado}), enviados)
        self.assertNotIn(4, no.tabela_fluxos[fluxo_expirado].destinos)
        self.assertNotIn(4, no.rotas_video[fluxo_expirado])

    async def test_ping_acrescenta_o_no_e_reencaminha_para_destinos(self):
        no = NoOTT("127.0.0.1")
        no.transporte.set_id(2)
        no.transporte.add_neighbor(3, ("127.0.0.3", 5000))
        fluxo = (1, 0)
        no._registado = True
        no.pais_video[fluxo] = 1
        no.tabela_fluxos[fluxo] = FluxoVideo(
            pai=1, destinos={3: time.monotonic() + 6}
        )
        no.rotas_video[fluxo] = {3}
        enviados = []
        no.transporte.send = lambda destino, tipo, corpo, **kw: enviados.append(
            (destino, tipo, corpo, kw)
        )

        no._receber_ping(1, fluxo, {
            "n": 12, "caminho": [{"id": 1, "t": time.time()}],
        })

        self.assertEqual(len(enviados), 1)
        destino, tipo, corpo, opcoes = enviados[0]
        self.assertEqual((destino, tipo), (3, Tipo.PING))
        self.assertEqual(opcoes["fluxo"], fluxo)
        self.assertEqual([p["id"] for p in corpo["caminho"]], [1, 2])

    async def test_servidor_py_ate_ao_player(self):
        porta = porta_livre()
        dados = {
            "bootstrapper": {"host": "127.0.0.3", "port": porta},
            "nodes": {"127.0.0.1": 1, "127.0.0.2": 2},
            "links": [{"node1": 1, "ip1": "127.0.0.1", "node2": 2, "ip2": "127.0.0.2"}],
        }
        pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pasta)  # registado antes do Servidor.py: só é apagado depois de ele parar
        config = Path(pasta) / "config.json"
        config.write_text(json.dumps(dados), encoding="utf-8")
        configuracao = carregar_configuracao(config)
        filme = Path(pasta) / "teste.Mjpeg"  # formato do ProgEx: 5 B ASCII com o tamanho + frame
        filme.write_bytes(b"".join(b"%05d" % len(f) + f for f in FRAMES))

        player = player_falso()
        self.addCleanup(player.close)
        bootstrapper = Bootstrapper(configuracao)
        no_servidor = NoOTT("127.0.0.3", porta_bootstrapper=porta, host="127.0.0.1", porta=porta,
                            portas_servidor=[0])
        no_cliente = NoOTT("127.0.0.3", porta_bootstrapper=porta, host="127.0.0.2", porta=porta,
                           player=player.getsockname())
        no_cliente.fluxos_player = {(1, 0)}
        await bootstrapper.start()
        self.addAsyncCleanup(self._fechar, bootstrapper, no_servidor, no_cliente)
        await asyncio.gather(no_servidor.start(), no_cliente.start())
        await asyncio.wait_for(self._esperar_handshake(no_servidor, no_cliente), 2)
        no_cliente.definir_pai((1, 0), 1)

        servidor = subprocess.Popen(
            [sys.executable, str(RAIZ / "player" / "Servidor.py"), str(filme),
             "127.0.0.1", str(no_servidor.video.portas[0])],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(servidor.wait)
        self.addCleanup(servidor.terminate)

        pacotes = [await receber(player) for _ in FRAMES]
        self.assertEqual([p[12:] for p in pacotes], FRAMES)  # payload intacto, pela ordem
        self.assertEqual([seq_rtp(p) for p in pacotes], [1, 2, 3])
        no_cliente.desistir_video((1, 0))
        await asyncio.wait_for(self._esperar_sem_destino(no_servidor, 2), 1)

    @staticmethod
    async def _esperar_handshake(no_servidor, no_cliente):
        while 2 not in no_servidor._confirmados or 1 not in no_cliente._confirmados:
            await asyncio.sleep(0.01)

    @staticmethod
    async def _esperar_sem_destino(no, vizinho):
        while vizinho in no.rotas_video.get((1, 0), set()):
            await asyncio.sleep(0.01)

    async def test_fluxo_fora_da_lista_nao_chega_ao_player(self):
        player = player_falso()
        self.addCleanup(player.close)
        no = NoOTT("127.0.0.1", player=player.getsockname())
        await no.video.start()
        self.addAsyncCleanup(lambda: no.video.close())
        no.fluxos_player = {(1, 0)}
        no._registado = True
        no.pais_video[(1, 0)] = 7
        no.tabela_fluxos[(1, 0)] = FluxoVideo(pai=7, local=True)
        no._receber(7, Tipo.DATA, b"nao", (9, 0), ("127.0.0.1", 5000))
        no._receber(7, Tipo.DATA, b"sim", (1, 0), ("127.0.0.1", 5000))
        self.assertEqual(await receber(player), b"sim")

    @staticmethod
    async def _fechar(*partes):
        for parte in partes:
            parte.close()


if __name__ == "__main__":
    unittest.main()

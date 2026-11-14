import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bootstrapper.servidor import Bootstrapper, carregar_configuracao
from ott import __main__ as ott_main
from ott.__main__ import NoOTT


class TestArranqueOTT(unittest.IsolatedAsyncioTestCase):
    async def test_registo_handshake_queda_e_recuperacao(self):
        porta = self._porta_livre()
        dados = {
            "bootstrapper": {"host": "127.0.0.3", "port": porta},
            "nodes": {"127.0.0.1": 1, "127.0.0.2": 2},
            "links": [{"node1": 1, "ip1": "127.0.0.1",
                       "node2": 2, "ip2": "127.0.0.2"}],
        }
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "config.json"
            caminho.write_text(json.dumps(dados), encoding="utf-8")
            configuracao = carregar_configuracao(caminho)

        bootstrapper = Bootstrapper(configuracao)
        no1 = NoOTT("127.0.0.3", porta_bootstrapper=porta,
                    host="127.0.0.1", porta=porta,
                    porta_vizinhos=porta)
        no2 = NoOTT("127.0.0.3", porta_bootstrapper=porta,
                    host="127.0.0.2", porta=porta,
                    porta_vizinhos=porta)
        await bootstrapper.start()
        self.addAsyncCleanup(self._fechar, bootstrapper, no1, no2)

        await asyncio.gather(no1.start(), no2.start())
        await self._esperar(lambda: 2 in no1._confirmados and 1 in no2._confirmados)

        self.assertEqual(no1.transporte.id, 1)
        self.assertEqual(no2.transporte.id, 2)
        self.assertEqual(no1.transporte.neighbors, [2])
        self.assertEqual(no2.transporte.neighbors, [1])

        with patch.object(ott_main, "INTERVALOS_HELLO", (0.05,)):
            no2.close()
            await self._esperar(lambda: 2 in no1._caidos, timeout=5)

            no2_reiniciado = NoOTT("127.0.0.3", porta_bootstrapper=porta,
                                   host="127.0.0.2", porta=porta,
                                   porta_vizinhos=porta)
            no2_reiniciado.transporte.hello = lambda _vizinho: 0
            self.addAsyncCleanup(no2_reiniciado.close)
            await no2_reiniciado.start()
            await self._esperar(
                lambda: 2 in no1._confirmados and 1 in no2_reiniciado._confirmados
            )
        self.assertEqual(no1.transporte.neighbors, [2])
        self.assertEqual(no2_reiniciado.transporte.neighbors, [1])

    async def test_close_avisado_remove_logo_o_vizinho(self):
        porta = self._porta_livre()
        dados = {
            "bootstrapper": {"host": "127.0.0.3", "port": porta},
            "nodes": {"127.0.0.1": 1, "127.0.0.2": 2},
            "links": [{"node1": 1, "ip1": "127.0.0.1",
                       "node2": 2, "ip2": "127.0.0.2"}],
        }
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "config.json"
            caminho.write_text(json.dumps(dados), encoding="utf-8")
            configuracao = carregar_configuracao(caminho)

        bootstrapper = Bootstrapper(configuracao)
        no1 = NoOTT("127.0.0.3", porta_bootstrapper=porta,
                    host="127.0.0.1", porta=porta, porta_vizinhos=porta)
        no2 = NoOTT("127.0.0.3", porta_bootstrapper=porta,
                    host="127.0.0.2", porta=porta, porta_vizinhos=porta)
        await bootstrapper.start()
        self.addAsyncCleanup(self._fechar, bootstrapper, no1, no2)
        await asyncio.gather(no1.start(), no2.start())
        await self._esperar(lambda: 2 in no1._confirmados and 1 in no2._confirmados)

        fluxo = (1, 0)
        no2.tabela_fluxos[fluxo] = ott_main.FluxoVideo(pai=1)
        no1.transporte.hello(2)
        await self._esperar(lambda: no2.tabela_fluxos[fluxo].pai is None)

        await no1.shutdown(timeout=1)
        await self._esperar(lambda: 1 in no2._caidos)
        self.assertNotIn(1, no2.transporte.neighbors)

    @staticmethod
    def _porta_livre() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.3", 0))
            return sock.getsockname()[1]

    async def _esperar(self, condicao, timeout=2):
        async def aguardar():
            while not condicao():
                await asyncio.sleep(0.02)

        await asyncio.wait_for(aguardar(), timeout)

    async def _fechar(self, bootstrapper, *nos):
        for no in nos:
            no.close()
        bootstrapper.close()
        await asyncio.sleep(0)

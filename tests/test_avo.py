"""Pedido de socorro ao avô: um nó com um só vizinho vivo guarda o pai desse vizinho e, se ficar
sem vizinhos, liga-se a ele. Em localhost, na linha S (1) — A (2) — C (3): o C só tem o A."""
import asyncio
import json
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bootstrapper.servidor import Bootstrapper, carregar_configuracao
from ott import __main__ as ott_main
from ott import rotas as ott_rotas
from ott.__main__ import NoOTT
from ott.protocolo import Tipo
from ott.transporte import Transporte

S, A, C = 1, 2, 3
RAPIDO = {"HELLO_VIVO": 0.1, "HELLO_EM_BAIXO": 0.3, "ESPERA_RISCO": 0.3, "FIM_EMERGENCIA": 0.6}


def porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@patch.object(ott_rotas, "PERIODO_ANUNCIO", 0.2)
@patch.object(ott_rotas, "VALIDADE_ANUNCIO", 0.6)
class TestAvo(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for nome, valor in RAPIDO.items():
            remendo = patch.object(ott_main, nome, valor)
            remendo.start()
            self.addCleanup(remendo.stop)
        self.porta = porta_livre()
        dados = {
            "bootstrapper": {"host": "127.0.0.9", "port": self.porta},
            "nodes": {f"127.0.0.{n}": n for n in (S, A, C)},
            "links": [{"node1": S, "ip1": "127.0.0.1", "node2": A, "ip2": "127.0.0.2"},
                      {"node1": A, "ip1": "127.0.0.2", "node2": C, "ip2": "127.0.0.3"}],
        }
        with tempfile.TemporaryDirectory() as pasta:
            config = Path(pasta) / "config.json"
            config.write_text(json.dumps(dados), encoding="utf-8")
            self.bootstrapper = Bootstrapper(carregar_configuracao(config))
        await self.bootstrapper.start()
        self.addAsyncCleanup(self._fechar, self.bootstrapper)

    def _no(self, n, **opcoes):
        no = NoOTT("127.0.0.9", porta_bootstrapper=self.porta, host=f"127.0.0.{n}",
                   porta=self.porta, **opcoes)
        self.addAsyncCleanup(self._fechar, no)
        return no

    async def _linha(self):
        nos = {A: self._no(A), C: self._no(C), S: self._no(S, portas_servidor=[0])}
        await asyncio.gather(*(no.start() for no in nos.values()))
        await self._esperar(lambda: nos[C].rotas.pai(S) == A and nos[A].rotas.pai(S) == S)
        return nos

    async def test_no_com_um_so_vizinho_guarda_o_avo(self):
        nos = await self._linha()
        await self._esperar(lambda: S in nos[C]._avos)  # o pai do A é o S: o avô do C
        self.assertEqual(nos[C]._avos[S], ("127.0.0.1", self.porta))
        self.assertIn(C, nos[A]._protegidos)
        self.assertNotIn(C, nos[S]._vizinhos_configurados)  # o S não sabe nada do C

    async def test_sem_vizinhos_liga_se_ao_avo_e_recebe_rotas_por_ele(self):
        nos = await self._linha()
        await self._esperar(lambda: S in nos[C]._avos)
        nos[A].close()  # morre sem aviso: o C fica sem vizinhos
        await self._esperar(lambda: S in nos[C]._confirmados and C in nos[S]._confirmados)
        self.assertIn(S, nos[C]._emergencia)
        self.assertIn(C, nos[S]._emergencia)
        await self._esperar(lambda: nos[C].rotas.pai(S) == S)  # o vídeo do S já pode vir direto

    async def test_quando_o_vizinho_original_volta_a_emergencia_acaba(self):
        nos = await self._linha()
        await self._esperar(lambda: S in nos[C]._avos)
        nos[A].close()
        await self._esperar(lambda: S in nos[C]._confirmados)
        a2 = self._no(A)
        await a2.start()  # o A volta
        await self._esperar(lambda: S not in nos[C]._vizinhos_configurados, timeout=8)
        await self._esperar(lambda: C not in nos[S]._vizinhos_configurados)  # recebeu o CLOSE
        await self._esperar(lambda: nos[C].rotas.pai(S) == A)  # de volta ao caminho original
        self.assertEqual(nos[C]._emergencia, set())

    async def test_hello_de_desconhecido_sem_socorro_e_ignorado(self):
        nos = await self._linha()
        intruso = Transporte(99)  # um nó que não vem do bootstrapper nem pede socorro
        await intruso.start("127.0.0.4", 0)
        self.addAsyncCleanup(self._fechar, intruso)
        intruso.set_address(S, ("127.0.0.1", self.porta))
        intruso.send(S, Tipo.HELLO, {"arranque": 1, "intervalo": 1.0, "ouvidos": []})
        await asyncio.sleep(0.3)
        self.assertNotIn(99, nos[S]._vizinhos_configurados)

    @staticmethod
    async def _esperar(condicao, timeout=5.0):
        limite = time.monotonic() + timeout
        while not condicao():
            if time.monotonic() >= limite:
                raise AssertionError("condição não ficou verdadeira a tempo")
            await asyncio.sleep(0.02)

    @staticmethod
    async def _fechar(parte):
        parte.close()
        await asyncio.sleep(0)

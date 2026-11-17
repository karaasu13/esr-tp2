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


# HELLOs mais rápidos para os testes não demorarem (no CORE: 1 s e 5 s)
RAPIDO = {"HELLO_VIVO": 0.1, "HELLO_EM_BAIXO": 0.3}


class TestArranqueOTT(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for nome, valor in RAPIDO.items():
            remendo = patch.object(ott_main, nome, valor)
            remendo.start()
            self.addCleanup(remendo.stop)
        self.porta = self._porta_livre()
        dados = {
            "bootstrapper": {"host": "127.0.0.3", "port": self.porta},
            "nodes": {"127.0.0.1": 1, "127.0.0.2": 2},
            "links": [{"node1": 1, "ip1": "127.0.0.1",
                       "node2": 2, "ip2": "127.0.0.2"}],
        }
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "config.json"
            caminho.write_text(json.dumps(dados), encoding="utf-8")
            self.bootstrapper = Bootstrapper(carregar_configuracao(caminho))
        await self.bootstrapper.start()
        self.addAsyncCleanup(self._fechar_bootstrapper)

    def _no(self, ip):
        no = NoOTT("127.0.0.3", porta_bootstrapper=self.porta, host=ip, porta=self.porta)
        self.addAsyncCleanup(self._fechar_no, no)
        return no

    async def _par(self):
        no1, no2 = self._no("127.0.0.1"), self._no("127.0.0.2")
        await asyncio.gather(no1.start(), no2.start())
        await self._esperar(lambda: 2 in no1._confirmados and 1 in no2._confirmados)
        return no1, no2

    async def test_registo_e_hello_formam_o_overlay(self):
        no1, no2 = await self._par()
        self.assertEqual((no1.transporte.id, no2.transporte.id), (1, 2))
        self.assertEqual(no1.transporte.neighbors, [2])
        self.assertEqual(no2.transporte.neighbors, [1])
        # o limite de silêncio segue o intervalo anunciado no HELLO
        self.assertEqual(no1.transporte._vizinhos[2].intervalo, RAPIDO["HELLO_VIVO"])

    async def test_queda_sem_aviso_e_regresso(self):
        no1, no2 = await self._par()
        no2.close()  # sem CLOSE: morre
        await self._esperar(lambda: no1._vizinhos_estado[2].estado == "em baixo")
        self.assertNotIn(2, no1.transporte.neighbors)
        no2b = self._no("127.0.0.2")
        await no2b.start()
        await self._esperar(lambda: 2 in no1._confirmados and 1 in no2b._confirmados)
        self.assertTrue(no1._vizinhos_estado[2].ja_vivo)

    async def test_reinicio_rapido_detetado_pelo_numero_de_arranque(self):
        no1, no2 = await self._par()
        fluxo = (1, 0)
        no2.tabela_fluxos[fluxo] = ott_main.FluxoVideo(pai=1)
        no1.arranque += 1  # como se o no1 tivesse reiniciado antes de o no2 dar pelo silêncio
        await self._esperar(lambda: no2.tabela_fluxos[fluxo].pai is None)  # estado antigo limpo
        await self._esperar(lambda: 1 in no2._confirmados and 2 in no1._confirmados)

    async def test_close_remove_logo_e_nao_insiste_com_hellos(self):
        no1, no2 = await self._par()
        await no1.shutdown(timeout=1)
        await self._esperar(lambda: no2._vizinhos_estado[1].estado == "saiu")
        self.assertNotIn(1, no2.transporte.neighbors)
        enviados = []
        enviar = no2.transporte.send
        no2.transporte.send = lambda destino, tipo, *a, **kw: (
            enviados.append((destino, tipo)), enviar(destino, tipo, *a, **kw))[1]
        await asyncio.sleep(4 * RAPIDO["HELLO_EM_BAIXO"])
        self.assertNotIn((1, ott_main.Tipo.HELLO), enviados)

    async def test_ligacao_cortada_volta_sozinha(self):
        no1, no2 = await self._par()
        for a, b in ((no1, 2), (no2, 1)):  # 100% de perda nos dois sentidos, nós vivos
            self._cortar(a, b)
        await self._esperar(lambda: 2 not in no1._confirmados and 1 not in no2._confirmados)
        for a in (no1, no2):
            self._repor(a)
        await self._esperar(lambda: 2 in no1._confirmados and 1 in no2._confirmados)

    async def test_falha_num_so_sentido_nao_fica_a_ligar_e_desligar(self):
        no1, no2 = await self._par()
        self._cortar(no1, 2)  # o no2 deixa de ouvir o no1; o no1 continua a ouvir o no2
        await self._esperar(lambda: 1 not in no2._confirmados and 2 not in no1._confirmados)
        mudancas, anterior = 0, 2 in no1._confirmados
        for _ in range(40):
            await asyncio.sleep(0.05)
            agora = 2 in no1._confirmados
            mudancas += agora != anterior
            anterior = agora
        self.assertEqual(mudancas, 0)
        self.assertEqual(no1._vizinhos_estado[2].estado, "a ouvir")  # ouve-o, mas ele não o ouve

    @staticmethod
    def _cortar(no, vizinho):
        """Deita fora tudo o que o nó envia ao vizinho."""
        enviar = no.transporte._enviar
        no._enviar_original = enviar

        def _enviar(addr, destino, *args, **kw):
            return 0 if destino == vizinho else enviar(addr, destino, *args, **kw)

        no.transporte._enviar = _enviar

    @staticmethod
    def _repor(no):
        no.transporte._enviar = no._enviar_original

    @staticmethod
    def _porta_livre() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.3", 0))
            return sock.getsockname()[1]

    async def _esperar(self, condicao, timeout=5):
        async def aguardar():
            while not condicao():
                await asyncio.sleep(0.02)

        await asyncio.wait_for(aguardar(), timeout)

    async def _fechar_no(self, no):
        no.close()
        await asyncio.sleep(0)

    async def _fechar_bootstrapper(self):
        self.bootstrapper.close()
        await asyncio.sleep(0)


class TestPortasServidor(unittest.TestCase):
    def test_sem_porta_soma_o_numero_do_video(self):
        self.assertEqual(ott_main.portas_servidor([None, None]), [24000, 24001])
        self.assertEqual(ott_main.portas_servidor([25000, None]), [25000, 24001])
        self.assertEqual(ott_main.portas_servidor([]), [])

    def test_portas_repetidas_ou_invalidas_dao_erro(self):
        for pedidas in ([24001, None], [24000, 24000], [0], [70000]):
            with self.subTest(pedidas=pedidas), self.assertRaises(ValueError):
                ott_main.portas_servidor(pedidas)

    def test_linha_de_comando(self):
        """`--servidor --servidor` chega ao nó como [24000, 24001]; repetidas param o arranque."""
        argv = ["ott", "10.0.0.1", "--servidor", "--servidor"]
        with patch("sys.argv", argv), patch.object(ott_main, "NoOTT") as no, \
                patch.object(ott_main.asyncio, "run"):
            ott_main.main()
        self.assertEqual(no.call_args.kwargs["portas_servidor"], [24000, 24001])

        argv = ["ott", "10.0.0.1", "--servidor", "24001", "--servidor"]
        with patch("sys.argv", argv), patch.object(ott_main, "NoOTT") as no, \
                patch("sys.stderr"), self.assertRaises(SystemExit):
            ott_main.main()
        no.assert_not_called()

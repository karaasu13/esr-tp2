import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from bootstrapper.servidor import Bootstrapper, carregar_configuracao
from ott.protocolo import Tipo
from ott.transporte import Transporte


class TestConfiguracaoBootstrapper(unittest.TestCase):
    def test_carrega_cenarios_existentes(self):
        raiz = Path(__file__).resolve().parents[1]
        for nome in ("cenario1.json", "cenario2.json"):
            with self.subTest(cenario=nome):
                self.assertTrue(carregar_configuracao(raiz / "configs" / nome).ids_por_ip)

    def test_rejeita_ligacao_com_ip_que_nao_corresponde_ao_no(self):
        dados = {
            "bootstrapper": {"host": "127.0.0.1", "port": 5000},
            "nodes": {"10.0.0.1": 1, "10.0.0.2": 2},
            "links": [{"node1": 1, "ip1": "10.0.0.1", "node2": 2, "ip2": "10.0.0.9"}],
        }
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "config.json"
            caminho.write_text(json.dumps(dados), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "não correspondem"):
                carregar_configuracao(caminho)


class TestBootstrapper(unittest.IsolatedAsyncioTestCase):
    async def test_registo_devolve_id_e_vizinhos_configurados(self):
        dados = {
            "bootstrapper": {"host": "127.0.0.1", "port": 5000},
            "nodes": {"127.0.0.1": 1, "127.0.0.2": 2},
            "links": [{"node1": 1, "ip1": "127.0.0.1",
                       "node2": 2, "ip2": "127.0.0.2"}],
        }
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "config.json"
            caminho.write_text(json.dumps(dados), encoding="utf-8")
            configuracao = carregar_configuracao(caminho)
        bootstrapper = Bootstrapper(replace(configuracao, porta=0))
        respostas = []
        recebido = asyncio.Event()

        def ao_receber(origem, tipo, corpo, fluxo, addr):
            del fluxo, addr
            respostas.append((origem, tipo, corpo))
            recebido.set()

        no = Transporte(on_message=ao_receber)
        await bootstrapper.start()
        await no.start("127.0.0.1", 0)
        no.set_address(0, bootstrapper.endereco_local)
        self.addAsyncCleanup(self._fechar, bootstrapper, no)

        no.send(0, Tipo.REGISTER, {}, fiavel=True)
        await asyncio.wait_for(recebido.wait(), timeout=1)

        self.assertEqual(len(respostas), 1)
        origem, tipo, corpo = respostas[0]
        self.assertEqual(origem, 0)
        self.assertEqual(tipo, Tipo.REGISTER_ACK)
        self.assertEqual(corpo["id"], 1)
        self.assertEqual(corpo["vizinhos"], [{"id": 2, "ip": "127.0.0.2"}])
        self.assertFalse(no._pendentes)

    async def _fechar(self, bootstrapper, no):
        no.close()
        bootstrapper.close()
        await asyncio.sleep(0)

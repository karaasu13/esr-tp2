"""Anúncios e escolha do pai (ott/rotas.py), sozinhos e no cenário 2 em localhost."""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bootstrapper.servidor import Bootstrapper, carregar_configuracao
from ott import rotas as ott_rotas
from ott.__main__ import NoOTT
from ott.protocolo import Tipo
from ott.rotas import Rotas
from tests.test_video import player_falso, porta_livre, receber

FLUXO = (1, 0)
# Cenário 2 (configs/cenario2.json e docs/enderecos.md): ID -> nome
S1, N1, N3, N2, C1, C2 = 1, 2, 3, 4, 5, 6
LIGACOES = [(S1, N1), (N1, N2), (N1, N3), (N2, N3), (N2, C2), (N3, C1)]
PAIS = {N1: S1, N2: N1, N3: N1, C1: N3, C2: N2}
FRAMES = [b"\x80\x1a" + bytes([i]) * 100 for i in range(3)]


class TransporteFalso:
    def __init__(self, vizinhos, rtts):
        self.id = 1
        self.neighbors = vizinhos
        self._rtts = rtts
        self.enviados = []

    def rtt(self, no):
        return self._rtts.get(no)

    def send(self, destino, tipo, corpo=None, fiavel=False, fluxo=(0, 0)):
        self.enviados.append((destino, tipo, corpo, fiavel))


def anuncio(ronda, rtt=0.0, saltos=0):
    return {"srv": 9, "ronda": ronda, "videos": [0], "rtt": rtt, "saltos": saltos}


class TestRotas(unittest.TestCase):
    def setUp(self):
        self.transporte = TransporteFalso([2, 3, 4], {2: 0.001, 4: 0.002})  # 3 sem RTT: 100 ms
        self.mudancas = []
        self.rotas = Rotas(self.transporte, lambda srv, pai: self.mudancas.append((srv, pai)))

    def test_filtra_e_escolhe_o_mais_barato(self):
        rotas, transporte = self.rotas, self.transporte
        rotas.receber(2, anuncio(10))
        self.assertEqual(rotas.pai(9), 2)
        self.assertAlmostEqual(rotas.servidores[9].anuncios[2].custo, 2.0)  # 1 ms + 1 salto
        self.assertEqual(sorted(d for d, *_ in transporte.enviados), [3, 4])  # menos ao pai
        _, tipo, corpo, fiavel = transporte.enviados[0]
        self.assertEqual((tipo, fiavel), (Tipo.ANNOUNCE, False))
        self.assertEqual((corpo["ronda"], corpo["saltos"]), (10, 1))
        self.assertAlmostEqual(corpo["rtt"], 1.0)

        transporte.enviados.clear()
        rotas.receber(4, anuncio(10, rtt=5.0, saltos=2))  # não é o pai: guarda e não troca
        rotas.receber(2, anuncio(9))  # ronda antiga
        rotas.receber(3, anuncio(10, saltos=17))  # demasiados saltos
        self.assertEqual(transporte.enviados, [])
        self.assertEqual(rotas.servidores[9].anuncios[2].ronda, 10)
        self.assertNotIn(3, rotas.servidores[9].anuncios)
        self.assertEqual(self.mudancas, [(9, 2)])

    def test_pai_cai_passa_logo_ao_melhor_da_ronda_mais_recente(self):
        rotas = self.rotas
        rotas.receber(2, anuncio(10))
        rotas.receber(3, anuncio(10, rtt=1.0, saltos=1))  # custo 103
        rotas.receber(4, anuncio(11, rtt=50.0, saltos=2))  # custo 55, ronda mais recente
        rotas.vizinho_em_baixo(2)
        self.assertEqual(rotas.pai(9), 4)

        rotas.vizinho_em_baixo(4)  # o 3 só tem a ronda 10, mais antiga do que a 11 do pai
        self.assertEqual(rotas.pai(9), 3)
        rotas.vizinho_em_baixo(3)  # sem ninguém: a rota desaparece
        self.assertIsNone(rotas.pai(9))
        rotas.receber(2, anuncio(11))  # não é mais nova do que a rota que se perdeu
        self.assertIsNone(rotas.pai(9))
        rotas.receber(2, anuncio(12))
        self.assertEqual(rotas.pai(9), 2)
        self.assertEqual(self.mudancas, [(9, 2), (9, 4), (9, 3), (9, None), (9, 2)])

    def test_servidor_para_e_as_rotas_expiram(self):
        rotas = self.rotas
        rotas.receber(2, anuncio(10))
        rotas.receber(4, anuncio(10, rtt=5.0, saltos=2))
        rotas.servidores[9].anuncios[2].recebido -= ott_rotas.VALIDADE_ANUNCIO + 1
        rotas.expirar()  # o anúncio do pai expirou: passa ao 4
        self.assertEqual(rotas.pai(9), 4)
        rotas.servidores[9].anuncios[4].recebido -= ott_rotas.VALIDADE_ANUNCIO + 1
        rotas.expirar()
        self.assertIsNone(rotas.pai(9))
        self.assertNotIn(9, rotas.servidores)
        self.assertEqual(self.mudancas, [(9, 2), (9, 4), (9, None)])


@patch.object(ott_rotas, "PERIODO_ANUNCIO", 0.2)
@patch.object(ott_rotas, "VALIDADE_ANUNCIO", 0.6)  # 3 períodos, como os 6 s de 2 s
class TestCenario2(unittest.IsolatedAsyncioTestCase):
    async def _montar(self, ver_c2=False):
        """Cenário 2 em localhost; o nó do S1 arranca depois de os outros se conhecerem."""
        porta = porta_livre()
        dados = {
            "bootstrapper": {"host": "127.0.0.7", "port": porta},
            "nodes": {f"127.0.0.{no}": no for no in range(1, 7)},
            "links": [{"node1": a, "ip1": f"127.0.0.{a}", "node2": b, "ip2": f"127.0.0.{b}"}
                      for a, b in LIGACOES],
        }
        with tempfile.TemporaryDirectory() as pasta:
            config = Path(pasta) / "config.json"
            config.write_text(json.dumps(dados), encoding="utf-8")
            configuracao = carregar_configuracao(config)
        self.players = {C1: player_falso(), C2: player_falso()}
        for player in self.players.values():
            self.addCleanup(player.close)

        def criar(no, **opcoes):
            return NoOTT("127.0.0.7", porta_bootstrapper=porta, host=f"127.0.0.{no}",
                         porta=porta, **opcoes)

        bootstrapper = Bootstrapper(configuracao)
        nos = {no: criar(no) for no in (N1, N2, N3)}
        nos[S1] = criar(S1, portas_servidor=[0])
        for cliente, player in self.players.items():
            nos[cliente] = criar(cliente, player=player.getsockname())
        nos[C1].fluxos_player = {FLUXO}  # como --ver 1:0
        if ver_c2:
            nos[C2].fluxos_player = {FLUXO}
        self.addAsyncCleanup(self._fechar, bootstrapper, *nos.values())

        await bootstrapper.start()
        await asyncio.gather(*(nos[no].start() for no in (N1, N2, N3, C1, C2)))
        await self._esperar(lambda: all(
            b in nos[a]._confirmados and a in nos[b]._confirmados
            for a, b in LIGACOES if S1 not in (a, b)))
        await nos[S1].start()
        await self._esperar(lambda: all(
            nos[no].rotas.pai(S1) == pai for no, pai in PAIS.items()))
        return nos

    def _contar_dados(self, no):
        """Lista que recebe o destino de cada DATA que o nó envia."""
        destinos = []
        enviar = no.transporte.send

        def contar(destino, tipo, *args, **kw):
            if tipo == Tipo.DATA:
                destinos.append(destino)
            return enviar(destino, tipo, *args, **kw)

        no.transporte.send = contar
        return destinos

    async def test_pais_video_ate_ao_player_e_uma_copia_em_s1_n1(self):
        nos = await self._montar()
        dados_s1 = self._contar_dados(nos[S1])

        # Só o C1 pede: o JOIN sobe sozinho e os frames chegam ao player.
        await self._esperar(lambda: set(nos[S1].tabela_fluxos[FLUXO].destinos) == {N1})
        for frame in FRAMES:
            nos[S1]._frame_do_servidor(0, frame)
        self.assertEqual([await receber(self.players[C1]) for _ in FRAMES], FRAMES)

        # Os dois clientes a ver: o N1 reencaminha para N2 e N3, o S1 envia uma só cópia.
        nos[C2].pedir_video(FLUXO)
        await self._esperar(lambda: set(nos[N1].tabela_fluxos[FLUXO].destinos) == {N2, N3})
        dados_s1.clear()
        for frame in FRAMES:
            nos[S1]._frame_do_servidor(0, frame)
        for player in self.players.values():
            self.assertEqual([await receber(player) for _ in FRAMES], FRAMES)
        self.assertEqual(dados_s1, [N1] * len(FRAMES))

    async def test_servidor_deixa_de_anunciar_e_as_rotas_desaparecem(self):
        nos = await self._montar()
        nos[S1]._tarefas_aplicacao[-1].cancel()  # a tarefa dos anúncios
        await self._esperar(lambda: all(S1 not in nos[no].rotas.servidores for no in PAIS))
        self.assertIsNone(nos[C1].tabela_fluxos[FLUXO].pai)  # definir_pai(fluxo, None)

    async def test_n1_cai_e_o_c2_fica_sem_pai(self):
        nos = await self._montar(ver_c2=True)
        await nos[N1].shutdown()  # CLOSE: os vizinhos dão logo pela saída
        # O N2 e o N3 ainda podem apanhar-se um ao outro, até os anúncios expirarem.
        await self._esperar(lambda: nos[C2].rotas.pai(S1) is None)
        self.assertIsNone(nos[C2].tabela_fluxos[FLUXO].pai)

    async def test_ligacao_n1_n2_cai_e_o_n2_passa_pelo_n3(self):
        nos = await self._montar(ver_c2=True)
        await self._esperar(lambda: set(nos[N1].tabela_fluxos[FLUXO].destinos) == {N2, N3})
        for a, b in ((N1, N2), (N2, N1)):  # como tc com 100% de perda, nos dois sentidos
            self._cortar(nos[a], b)
            nos[a].transporte.idle_timeout = 1.0  # para o teste não esperar 3,5 s

        await self._esperar(lambda: nos[N2].rotas.pai(S1) == N3)
        await self._esperar(lambda: N2 in nos[N3].tabela_fluxos[FLUXO].destinos)
        await self._esperar(lambda: set(nos[N1].tabela_fluxos[FLUXO].destinos) == {N3})
        dados_s1 = self._contar_dados(nos[S1])
        for frame in FRAMES:
            nos[S1]._frame_do_servidor(0, frame)
        for player in self.players.values():
            self.assertEqual([await receber(player) for _ in FRAMES], FRAMES)
        self.assertEqual(dados_s1, [N1] * len(FRAMES))

    @staticmethod
    def _cortar(no, vizinho):
        """Deita fora tudo o que o nó envia ao vizinho."""
        enviar = no.transporte._enviar

        def _enviar(addr, destino, *args, **kw):
            return 0 if destino == vizinho else enviar(addr, destino, *args, **kw)

        no.transporte._enviar = _enviar

    @staticmethod
    async def _esperar(condicao, timeout=5.0):
        limite = time.monotonic() + timeout
        while not condicao():
            if time.monotonic() >= limite:
                raise AssertionError("condição não ficou verdadeira a tempo")
            await asyncio.sleep(0.02)

    @staticmethod
    async def _fechar(*partes):
        for parte in partes:
            parte.close()


if __name__ == "__main__":
    unittest.main()

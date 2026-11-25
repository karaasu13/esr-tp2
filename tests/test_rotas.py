"""Anúncios e escolha do pai (ott/rotas.py), sozinhos e no cenário 2 em localhost."""
import asyncio
import json
import random
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
        self.rtts = rtts  # segundos
        self.jitters = {}  # segundos
        self.perdas = {}  # fração
        self.enviados = []

    def rtt_min(self, no):
        return self.rtts.get(no)

    def jitter(self, no):
        return self.jitters.get(no)

    def perda(self, no):
        return self.perdas.get(no)

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

    def test_pai_cai_passa_logo_ao_melhor_viavel(self):
        rotas = self.rotas
        rotas.receber(2, anuncio(10))  # pai 2; este nó anuncia a ronda 10 com custo 2
        rotas.receber(3, anuncio(10, rtt=1.0, saltos=1))  # anuncia 2, não menos do que eu: não viável
        rotas.receber(4, anuncio(11, rtt=50.0, saltos=2))  # ronda mais nova: viável
        rotas.vizinho_em_baixo(2)
        self.assertEqual(rotas.pai(9), 4)

        rotas.vizinho_em_baixo(4)  # o 3 só tem a ronda 10, mais antiga do que a 11 que anunciei
        self.assertIsNone(rotas.pai(9))
        rotas.receber(3, anuncio(11, rtt=90.0, saltos=3))  # mesma ronda que anunciei, custo maior
        self.assertIsNone(rotas.pai(9))
        rotas.receber(3, anuncio(12, rtt=90.0, saltos=3))  # ronda mais nova: viável
        self.assertEqual(rotas.pai(9), 3)
        self.assertEqual(self.mudancas, [(9, 2), (9, 4), (9, None), (9, 3)])

    def test_pai_que_falhou_nao_volta_logo_amortecimento(self):
        rotas = self.rotas
        rotas.receber(2, anuncio(10))  # pai 2, custo 2
        rotas.receber(4, anuncio(11, rtt=50.0, saltos=2))  # reserva viável, bem mais cara
        rotas.vizinho_em_baixo(2)
        self.assertEqual(rotas.pai(9), 4)
        rotas.receber(2, anuncio(12))  # o 2 volta, muito mais barato
        rotas.receber(4, anuncio(12, rtt=50.0, saltos=2))
        self.assertEqual(rotas.pai(9), 4)  # castigado: não se volta já para ele
        rotas._castigo[2] = 0.0  # passaram os 30 s
        rotas.receber(2, anuncio(13))
        rotas.receber(4, anuncio(13, rtt=50.0, saltos=2))
        self.assertEqual(rotas.pai(9), 2)

    def test_castigado_ainda_serve_se_nao_houver_outro(self):
        rotas = self.rotas
        rotas.receber(2, anuncio(10))
        rotas.vizinho_em_baixo(2)
        self.assertIsNone(rotas.pai(9))
        rotas.receber(2, anuncio(11))  # o único caminho: antes um pai castigado do que nenhum
        self.assertEqual(rotas.pai(9), 2)

    def test_reserva_da_mesma_ronda_so_se_anunciar_menos_do_que_eu(self):
        rotas = self.rotas
        rotas.receber(4, anuncio(10, rtt=3.0, saltos=2))  # pai 4; anuncio 5 + 2 = 7
        rotas.receber(2, anuncio(10, rtt=4.0, saltos=2))  # anuncia 6 < 7: reserva válida
        rotas.vizinho_em_baixo(4)
        self.assertEqual(rotas.pai(9), 2)

    def test_nunca_escolhe_quem_recebe_atraves_de_mim(self):
        rotas = self.rotas
        rotas.receber(2, anuncio(10))  # pai 2; anuncio a ronda 10 com custo 2
        # O 3 recebe através de mim (somou a minha ligação): anuncia a mesma ronda com mais custo,
        # mesmo que, pelas medições deste nó, ir por ele pareça barato.
        rotas.receber(3, anuncio(10, rtt=1.5, saltos=2))
        rotas.vizinho_em_baixo(2)
        self.assertIsNone(rotas.pai(9))
        rotas.servidores[9].anuncios[3].recebido -= ott_rotas.VALIDADE_ANUNCIO + 1
        rotas.expirar()
        self.assertNotIn(9, rotas.servidores)

    def test_servidor_para_e_as_rotas_expiram(self):
        rotas = self.rotas
        rotas.receber(2, anuncio(10))  # anuncio 2
        rotas.receber(4, anuncio(10, rtt=0.5, saltos=1))  # anuncia 1,5 < 2: reserva válida
        rotas.servidores[9].anuncios[2].recebido -= ott_rotas.VALIDADE_ANUNCIO + 1
        rotas.expirar()  # o anúncio do pai expirou: passa ao 4
        self.assertEqual(rotas.pai(9), 4)
        rotas.servidores[9].anuncios[4].recebido -= ott_rotas.VALIDADE_ANUNCIO + 1
        rotas.expirar()
        self.assertIsNone(rotas.pai(9))
        self.assertNotIn(9, rotas.servidores)
        self.assertEqual(self.mudancas, [(9, 2), (9, 4), (9, None)])


class TestTrocaComMargem(unittest.TestCase):
    """Custo com as quatro medidas e troca por um vizinho claramente melhor."""

    def setUp(self):
        self.transporte = TransporteFalso([2, 3, 4], {2: 0.001, 3: 0.002, 4: 0.002})
        self.mudancas = []
        self.rotas = Rotas(self.transporte, lambda srv, pai: self.mudancas.append((srv, pai)))

    def test_custo_soma_as_quatro_medidas_e_le_anuncios_antigos(self):
        self.transporte.jitters[2] = 0.0005
        self.transporte.perdas[2] = 0.01
        self.rotas.receber(2, {"srv": 9, "ronda": 10, "videos": [0], "rtt": 3.0, "saltos": 1,
                               "jitter": 0.5, "perda": 1.0})
        # (3 + 1) ms de RTT + (0,5 + 0,5) ms de jitter + 100 x (1 + 1)% + 2 saltos
        self.assertAlmostEqual(self.rotas.servidores[9].anuncios[2].custo, 4 + 1 + 200 + 2)
        self.rotas.receber(3, anuncio(10))  # sem jitter nem perda: contam como 0
        self.assertAlmostEqual(self.rotas.servidores[9].anuncios[3].custo, 3.0)

    def test_perda_numa_ligacao_faz_a_rota_mudar(self):
        rotas, transporte = self.rotas, self.transporte
        rotas.receber(2, anuncio(10))  # custo 2
        rotas.receber(3, anuncio(10))  # custo 3: não ganha pela margem
        self.assertEqual(rotas.pai(9), 2)
        transporte.perdas[2] = 0.05  # 5% de perda: +100 no custo da ligação ao 2
        rotas.receber(2, anuncio(11))
        rotas.receber(3, anuncio(11))
        self.assertEqual(rotas.pai(9), 3)
        self.assertEqual(self.mudancas, [(9, 2), (9, 3)])

    def test_custos_parecidos_nao_ficam_a_trocar(self):
        rotas, transporte = self.rotas, self.transporte
        sorte = random.Random(1)
        for ronda in range(100, 115):  # 15 rondas de 2 s = 30 s simulados
            for vizinho in sorte.sample([2, 3], 2):  # ordem de chegada ao acaso
                # 10 ms ± 5% e até 0,5 ms de jitter: diferenças até 1,5, abaixo da margem de 2.
                # A ordem e as medidas mudam a cada ronda: sem margem, trocaria várias vezes.
                transporte.rtts[vizinho] = sorte.uniform(0.0095, 0.0105)
                transporte.jitters[vizinho] = sorte.uniform(0.0, 0.0005)
                rotas.receber(vizinho, anuncio(ronda, rtt=4.0, saltos=1))
        self.assertEqual(len(self.mudancas), 1)  # só a primeira escolha

    def test_troca_por_um_melhor_nunca_escolhe_um_nao_viavel(self):
        rotas, transporte = self.rotas, self.transporte
        rotas.receber(2, anuncio(10))  # pai 2: anuncio a ronda 10 com custo 2
        transporte.perdas[2] = 0.05
        rotas.receber(2, anuncio(10))  # a ligação ao pai piorou: custo 102 na mesma ronda
        # O 4 recebe através de mim: anuncia 3,5 na ronda 10, mais do que os 2 que anunciei.
        # Ir por ele custaria 6,5 (passa a margem), mas não é viável.
        rotas.receber(4, anuncio(10, rtt=1.5, saltos=2))
        self.assertAlmostEqual(rotas.servidores[9].anuncios[4].custo, 6.5)
        self.assertEqual(rotas.pai(9), 2)
        self.assertEqual(self.mudancas, [(9, 2)])


class RedeFalsa:
    """Vários Rotas ligados em memória. As mensagens vão para uma fila e são entregues por
    ordem de envio, como numa rede em que todas as ligações têm o mesmo atraso."""

    def __init__(self, ligacoes):
        self.rtt = {}
        for a, b, ms in ligacoes:
            self.rtt[a, b] = self.rtt[b, a] = ms / 1000
        nos = {n for a, b, _ in ligacoes for n in (a, b)}
        self.fila = []
        self.rotas = {n: Rotas(self._transporte(n), lambda srv, pai: None) for n in nos}

    def _transporte(self, no):
        rede = self

        class T:
            id = no

            @property
            def neighbors(self):
                return sorted(b for a, b in rede.rtt if a == no)

            def rtt_min(self, outro):
                return rede.rtt.get((no, outro))

            def jitter(self, outro):
                return None

            def perda(self, outro):
                return None

            def send(self, destino, tipo, corpo=None, fiavel=False, fluxo=(0, 0)):
                if (no, destino) in rede.rtt:
                    rede.fila.append((destino, no, tipo, corpo))
        return T()

    def entregar(self):
        while self.fila:
            destino, origem, tipo, corpo = self.fila.pop(0)
            if tipo == Tipo.WITHDRAW:
                self.rotas[destino].receber_withdraw(origem, corpo)
            else:
                self.rotas[destino].receber(origem, corpo)

    def anunciar(self, srv, ronda):
        for v in self.rotas[srv].transporte.neighbors:
            self.fila.append((v, srv, Tipo.ANNOUNCE, {"srv": srv, "ronda": ronda, "videos": [0],
                                                      "rtt": 0.0, "saltos": 0}))
        self.entregar()

    def cortar(self, a, b):
        del self.rtt[a, b], self.rtt[b, a]
        self.rotas[a].vizinho_em_baixo(b)
        self.rotas[b].vizinho_em_baixo(a)
        self.entregar()

    def expirar_anuncios_de(self, no, vizinho, srv):
        anuncio = self.rotas[no].servidores[srv].anuncios.get(vizinho)
        if anuncio:
            anuncio.recebido -= ott_rotas.VALIDADE_ANUNCIO + 1
        self.rotas[no].expirar()
        self.entregar()

    def pais(self, srv):
        return {n: r.pai(srv) for n, r in self.rotas.items() if n != srv}

    def sem_ciclos(self, srv):
        pais = self.pais(srv)
        for inicio in pais:
            vistos, n = set(), inicio
            while n is not None and n != srv:
                if n in vistos:
                    return False
                vistos.add(n)
                n = pais.get(n)
        return True


class TestAnel(unittest.TestCase):
    """Anel S - A - B - C - S: cortar uma ligação nunca forma ciclos e a rede recupera."""

    def test_corte_no_anel_sem_ciclos(self):
        S, A, B, C = 1, 2, 3, 4
        rede = RedeFalsa([(S, A, 1), (A, B, 1), (B, C, 5), (C, S, 1)])
        rede.anunciar(S, 10)
        self.assertEqual(rede.pais(S), {A: S, B: A, C: S})

        rede.cortar(S, A)  # o B recebe através do A: o A não o pode escolher
        self.assertTrue(rede.sem_ciclos(S))
        self.assertIsNone(rede.pais(S)[A])

        rede.anunciar(S, 11)  # chega ao C e daí ao B, que ainda tem o A como pai
        self.assertTrue(rede.sem_ciclos(S))
        rede.expirar_anuncios_de(B, A, S)  # o anúncio do A não é renovado: o B passa ao C
        self.assertTrue(rede.sem_ciclos(S))
        self.assertEqual(rede.pais(S)[B], C)
        rede.anunciar(S, 12)  # a ronda nova chega ao A pelo B
        self.assertTrue(rede.sem_ciclos(S))
        self.assertEqual(rede.pais(S), {A: B, B: C, C: S})

    def test_servidor_cai_e_ninguem_se_escolhe_mutuamente(self):
        S, A, B = 1, 2, 3
        rede = RedeFalsa([(S, A, 1), (S, B, 2), (A, B, 1)])
        rede.anunciar(S, 10)
        rede.cortar(S, A)
        rede.cortar(S, B)
        self.assertTrue(rede.sem_ciclos(S))
        pais = rede.pais(S)
        self.assertFalse(pais[A] == B and pais[B] == A)


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
        await self._esperar(lambda: set(self._destinos(nos[S1])) == {N1})
        for frame in FRAMES:
            nos[S1]._frame_do_servidor(0, frame)
        self.assertEqual([await receber(self.players[C1]) for _ in FRAMES], FRAMES)

        # Os dois clientes a ver: o N1 reencaminha para N2 e N3, o S1 envia uma só cópia.
        nos[C2].pedir_video(FLUXO)
        await self._esperar(lambda: set(self._destinos(nos[N1])) == {N2, N3})
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
        await self._esperar(lambda: set(self._destinos(nos[N1])) == {N2, N3})
        for a, b in ((N1, N2), (N2, N1)):  # como tc com 100% de perda, nos dois sentidos
            self._cortar(nos[a], b)
            nos[a].transporte.idle_timeout = 1.0  # para o teste não esperar 3,5 s

        await self._esperar(lambda: nos[N2].rotas.pai(S1) == N3)
        await self._esperar(lambda: N2 in self._destinos(nos[N3]))
        await self._esperar(lambda: set(self._destinos(nos[N1])) == {N3})
        dados_s1 = self._contar_dados(nos[S1])
        for frame in FRAMES:
            nos[S1]._frame_do_servidor(0, frame)
        for player in self.players.values():
            self.assertEqual([await receber(player) for _ in FRAMES], FRAMES)
        self.assertEqual(dados_s1, [N1] * len(FRAMES))

    async def test_ligacao_n1_n3_cai_n3_sem_viavel_envia_withdraw(self):
        nos = await self._montar()
        # 5% de perda entre N2 e N1 (+100 no custo): o N2 passa a receber pelo N3. Assim o N3
        # não tem nenhum vizinho viável quando perder o N1 (o N2 recebe através dele).
        medir = nos[N2].transporte.perda
        nos[N2].transporte.perda = lambda no: 0.05 if no == N1 else medir(no)
        await self._esperar(lambda: nos[N2].rotas.pai(S1) == N3)

        withdraws = []
        enviar = nos[N3].transporte.send

        def registar(destino, tipo, *args, **kw):
            if tipo == Tipo.WITHDRAW:
                withdraws.append(destino)
            return enviar(destino, tipo, *args, **kw)

        nos[N3].transporte.send = registar
        pais_c1 = []
        ao_mudar = nos[C1].rotas.ao_mudar_pai
        nos[C1].rotas.ao_mudar_pai = lambda srv, pai: (pais_c1.append(pai), ao_mudar(srv, pai))

        with patch.object(ott_rotas, "VALIDADE_ANUNCIO", 6.0):  # sem expirar durante o teste
            for a, b in ((N1, N3), (N3, N1)):
                self._cortar(nos[a], b)
                nos[a].transporte.idle_timeout = 1.0
            inicio = time.monotonic()
            await self._esperar(lambda: None in pais_c1)
            self.assertLess(time.monotonic() - inicio, 3.0)  # idle timeout de 1 s, não 6 s
            self.assertCountEqual(withdraws, [N2, C1])

            # Ronda seguinte: S1 -> N1 -> N2 -> N3 -> C1, e o vídeo volta a chegar ao C1.
            await self._esperar(lambda: nos[N3].rotas.pai(S1) == N2
                                and nos[C1].rotas.pai(S1) == N3
                                and nos[N2].rotas.pai(S1) == N1)
            await self._esperar(lambda: N3 in self._destinos(nos[N2]))
            await self._esperar(lambda: N2 in self._destinos(nos[N1]))
            for frame in FRAMES:
                nos[S1]._frame_do_servidor(0, frame)
            self.assertEqual([await receber(self.players[C1]) for _ in FRAMES], FRAMES)

    async def test_sem_video_do_pai_troca_em_menos_de_1_s(self):
        nos = await self._montar()
        # Medidas fixas (o jitter em localhost varia alguns ms): ligação N3-N1 com custo 2,5,
        # N3-N2 e N2-N1 com custo 1. O N3 fica com o N1 (pelo N2 custaria só menos 0,5, abaixo
        # da margem), e o N2 anuncia menos do que o N3, por isso é uma reserva viável.
        self._fixar_medidas(nos[N3], {N1: (0.0, 0.0015), N2: (0.0, 0.0)})
        self._fixar_medidas(nos[N2], {N1: (0.0, 0.0)})
        rota_n3 = nos[N3].rotas.servidores[S1]

        def medidas_fixas_em_vigor():
            a1, a2 = rota_n3.anuncios.get(N1), rota_n3.anuncios.get(N2)
            return (a1 and a2 and a1.ronda == a2.ronda and rota_n3.viavel(a2)
                    and abs((a1.custo - a2.custo) - 0.5) < 0.01)

        await self._esperar(medidas_fixas_em_vigor)
        self.assertEqual(nos[N3].rotas.pai(S1), N1)

        async def video():  # 20 frames por segundo, como o Servidor.py
            while True:
                nos[S1]._frame_do_servidor(0, FRAMES[0])
                await asyncio.sleep(0.05)

        tarefa = asyncio.create_task(video())
        self.addCleanup(tarefa.cancel)
        await self._esperar(lambda: FLUXO in nos[N3]._ultimo_video)

        enviar = nos[N1].transporte.send  # o N1 deixa de enviar DATA ao N3; o resto passa
        nos[N1].transporte.send = lambda destino, tipo, *a, **kw: (
            0 if tipo == Tipo.DATA and destino == N3 else enviar(destino, tipo, *a, **kw))
        inicio = time.monotonic()
        await self._esperar(lambda: nos[N3].rotas.pai(S1) == N2, timeout=1.0)
        self.assertLess(time.monotonic() - inicio, 1.0)
        self.assertIn(N1, nos[N3].transporte.neighbors)  # não deu o N1 como em baixo
        await self._esperar(lambda: FLUXO in nos[N3]._ultimo_video)  # vídeo de novo, pelo N2
        self.assertEqual(nos[N3].tabela_fluxos[FLUXO].pai, N2)

    @staticmethod
    def _fixar_medidas(no, medidas):
        """Medidas fixas (rtt_min e jitter, em segundos) das ligações aos vizinhos indicados."""
        rtt_min, jitter = no.transporte.rtt_min, no.transporte.jitter
        no.transporte.rtt_min = lambda v: medidas[v][0] if v in medidas else rtt_min(v)
        no.transporte.jitter = lambda v: medidas[v][1] if v in medidas else jitter(v)

    @staticmethod
    def _destinos(no):
        """Destinos do fluxo no nó (vazio enquanto o fluxo ainda não existe nesse nó)."""
        estado = no.tabela_fluxos.get(FLUXO)
        return estado.destinos if estado else {}

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


@patch.object(ott_rotas, "PERIODO_ANUNCIO", 0.2)
@patch.object(ott_rotas, "VALIDADE_ANUNCIO", 0.6)
class TestDoisServidores(unittest.IsolatedAsyncioTestCase):
    """S1 - A - B - S2, com C1 ligado ao A e C2 ao B; os clientes pedem só o vídeo 0 (--ver 0).
    Medidas fixas: todas as ligações a 0 ms, menos A-B com 0,5 ms. Pelo lado de cada cliente o
    servidor mais perto custa 2 e o outro 3,5: a diferença não passa a margem, por isso só a
    espera antes da 1.ª escolha evita que o cliente fique com o servidor que arrancou primeiro."""

    S1, A, B, S2, C1, C2 = 1, 2, 3, 4, 5, 6
    LIGACOES = [(1, 2), (2, 3), (3, 4), (2, 5), (3, 6)]

    async def _montar(self):
        S1, A, B, S2, C1, C2 = self.S1, self.A, self.B, self.S2, self.C1, self.C2
        porta = porta_livre()
        dados = {
            "bootstrapper": {"host": "127.0.0.7", "port": porta},
            "nodes": {f"127.0.0.{no}": no for no in range(1, 7)},
            "links": [{"node1": a, "ip1": f"127.0.0.{a}", "node2": b, "ip2": f"127.0.0.{b}"}
                      for a, b in self.LIGACOES],
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
        nos = {no: criar(no) for no in (A, B)}
        for servidor in (S1, S2):
            nos[servidor] = criar(servidor, portas_servidor=[0])
        for cliente, player in self.players.items():
            nos[cliente] = criar(cliente, player=player.getsockname())
            nos[cliente].videos_pedidos = {0}  # como --ver 0
        for no, t in nos.items():
            vizinhos = [b for a, b in self.LIGACOES if a == no] + [a for a, b in self.LIGACOES if b == no]
            TestCenario2._fixar_medidas(t, {v: (0.0005 if {no, v} == {A, B} else 0.0, 0.0)
                                            for v in vizinhos})
        self.addAsyncCleanup(TestCenario2._fechar, bootstrapper, *nos.values())

        await bootstrapper.start()
        await asyncio.gather(*(nos[no].start() for no in (A, B, C1, C2)))
        await TestCenario2._esperar(lambda: all(
            b in nos[a]._confirmados and a in nos[b]._confirmados
            for a, b in self.LIGACOES if not {a, b} & {S1, S2}))
        await nos[S1].start()
        await asyncio.sleep(0.3)  # o S2 arranca depois (menos do que a espera de 2 rondas)
        await nos[S2].start()
        return nos

    async def test_cada_cliente_fica_com_o_mais_barato_e_troca_quando_o_seu_para(self):
        S1, A, B, S2, C1, C2 = self.S1, self.A, self.B, self.S2, self.C1, self.C2
        esperar = TestCenario2._esperar
        nos = await self._montar()
        await esperar(lambda: nos[C1]._servidor_do_video.get(0) == S1
                      and nos[C2]._servidor_do_video.get(0) == S2)
        await esperar(lambda: set(nos[S1].tabela_fluxos[(S1, 0)].destinos) == {A}
                      and set(nos[S2].tabela_fluxos[(S2, 0)].destinos) == {B})
        for servidor, cliente in ((S1, C1), (S2, C2)):
            for frame in FRAMES:
                nos[servidor]._frame_do_servidor(0, frame)
            self.assertEqual([await receber(self.players[cliente]) for _ in FRAMES], FRAMES)

        await nos[S1].shutdown()  # o S1 para: o C1 passa a pedir o vídeo 0 ao S2
        await esperar(lambda: nos[C1]._servidor_do_video.get(0) == S2)
        self.assertNotIn((S1, 0), nos[C1].fluxos_player)  # o fluxo antigo foi largado
        await esperar(lambda: set(nos[B].tabela_fluxos[(S2, 0)].destinos) == {A, C2})
        for frame in FRAMES:
            nos[S2]._frame_do_servidor(0, frame)
        for player in self.players.values():
            self.assertEqual([await receber(player) for _ in FRAMES], FRAMES)
        self.assertEqual(nos[C2]._servidor_do_video[0], S2)

    async def test_leitor_de_video_morre_e_o_cliente_passa_para_o_outro_servidor(self):
        S1, S2, C1 = self.S1, self.S2, self.C1
        esperar = TestDoisServidores._esperar_s
        with patch.object(ott_main, "SEM_LEITOR", 0.3):
            nos = await self._montar()
            await esperar(lambda: nos[C1]._servidor_do_video.get(0) == S1)
            nos[S1]._frame_do_servidor(0, FRAMES[0])  # o leitor do S1 mandou um frame e calou-se
            await esperar(lambda: 0 not in nos[C1].rotas.servidores[S1].anuncios[self.A].videos)
            await esperar(lambda: nos[C1]._servidor_do_video.get(0) == S2)
            nos[S1]._frame_do_servidor(0, FRAMES[1])  # voltou: o S1 volta a anunciar o vídeo 0
            await esperar(lambda: 0 in nos[C1].rotas.servidores[S1].anuncios[self.A].videos)

    async def test_player_fecha_e_o_cliente_deixa_de_pedir(self):
        S1, A, C1 = self.S1, self.A, self.C1
        esperar = TestDoisServidores._esperar_s
        nos = await self._montar()
        await esperar(lambda: C1 in nos[A].tabela_fluxos.get((S1, 0), ott_main.FluxoVideo()).destinos)
        nos[C1].parar_de_ver()
        await esperar(lambda: C1 not in nos[A].tabela_fluxos[(S1, 0)].destinos)
        self.assertEqual(nos[C1].fluxos_player, set())
        await asyncio.sleep(0.5)  # e não volta a pedir na escolha de servidores seguinte
        self.assertNotIn(C1, nos[A].tabela_fluxos[(S1, 0)].destinos)

    _esperar_s = staticmethod(TestCenario2._esperar)


class TestRegisto(unittest.IsolatedAsyncioTestCase):
    async def test_insiste_ate_o_bootstrapper_aparecer(self):
        porta = porta_livre()
        dados = {"bootstrapper": {"host": "127.0.0.7", "port": porta},
                 "nodes": {"127.0.0.1": 1}, "links": []}
        with tempfile.TemporaryDirectory() as pasta:
            config = Path(pasta) / "config.json"
            config.write_text(json.dumps(dados), encoding="utf-8")
            configuracao = carregar_configuracao(config)
        no = NoOTT("127.0.0.7", porta_bootstrapper=porta, host="127.0.0.1", porta=porta)
        bootstrapper = Bootstrapper(configuracao)
        self.addAsyncCleanup(TestCenario2._fechar, no, bootstrapper)
        with patch.object(ott_main, "INSISTIR_REGISTO", 0.2):
            arranque = asyncio.create_task(no.start(insistir=True))
            await asyncio.sleep(0.6)  # ainda sem bootstrapper: continua à espera, sem erro
            self.assertFalse(arranque.done())
            await bootstrapper.start()
            await asyncio.wait_for(arranque, 5)
        self.assertEqual(no.transporte.id, 1)


if __name__ == "__main__":
    unittest.main()

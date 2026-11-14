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
        # (3 + 1) ms de RTT + (0,5 + 0,5) ms de jitter + 20 x (1 + 1)% + 2 saltos
        self.assertAlmostEqual(self.rotas.servidores[9].anuncios[2].custo, 4 + 1 + 40 + 2)
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
            await self._esperar(lambda: N3 in nos[N2].tabela_fluxos[FLUXO].destinos)
            await self._esperar(lambda: N2 in nos[N1].tabela_fluxos[FLUXO].destinos)
            for frame in FRAMES:
                nos[S1]._frame_do_servidor(0, frame)
            self.assertEqual([await receber(self.players[C1]) for _ in FRAMES], FRAMES)

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

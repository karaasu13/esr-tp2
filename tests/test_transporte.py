"""Testes do transporte em localhost. Correr com: python3 -m unittest -v"""
import asyncio
import unittest

from unittest.mock import patch

from ott import transporte as ott_transporte
from ott.protocolo import CABECALHO, Flag, Pacote, Tipo
from ott.transporte import Transporte

RAPIDO = dict(idle_timeout=0.35, rto_inicial=0.05, rto_max=0.3)


class TestPacote(unittest.TestCase):
    def test_cabecalho_tem_14_bytes(self):
        self.assertEqual(CABECALHO.size, 14)

    def test_codigos_das_mensagens_de_fluxo(self):
        self.assertEqual(Tipo.CLOSE, 0x07)
        self.assertEqual(Tipo.JOIN, 0x11)
        self.assertEqual(Tipo.LEAVE, 0x12)
        self.assertEqual(Tipo.PING, 0x14)

    def test_ida_e_volta(self):
        for p in [
            Pacote(Tipo.HELLO, 3, 5, 42, (0, 0), Flag.FIAVEL, 40, b'{"a":1}'),
            Pacote(Tipo.DATA, 1, 2, 7, (1, 2), 0, None, b"\x00\xff video"),
        ]:
            self.assertEqual(Pacote.descodificar(p.codificar()), p)

    def test_rejeita_lixo(self):
        with self.assertRaises(ValueError):
            Pacote.descodificar(b"curto")
        with self.assertRaises(ValueError):
            Pacote.descodificar(b"\x09" + bytes(13))  # versão errada


class TestTransporte(unittest.IsolatedAsyncioTestCase):
    async def par(self, perda=0.0, sondas=0.1, **kw):
        """Dois nós (1 e 2) vizinhos um do outro, em portas livres de localhost.

        Com sondas, cada um manda um HELLO de sonda ao outro nesse intervalo, como faz o nó
        (ott/__main__.py): é o sinal de vida e dá as medidas de RTT e perda."""
        self.rx = {1: [], 2: []}
        self.down = {1: [], 2: []}
        nos = {}
        for i, seed in ((1, 11), (2, 22)):
            t = Transporte(i, perda=perda, seed=seed, **{**RAPIDO, **kw},
                           on_message=lambda o, tp, c, f, a, i=i: self._guardar(i, o, tp, c),
                           on_neighbor_down=lambda n, i=i: self.down[i].append(n))
            await t.start("127.0.0.1", 0)
            nos[i] = t
        nos[1].add_neighbor(2, nos[2].endereco_local)
        nos[2].add_neighbor(1, nos[1].endereco_local)
        self.addAsyncCleanup(self._fechar, nos)
        if sondas:
            for i, outro in ((1, 2), (2, 1)):
                tarefa = asyncio.create_task(self._sondar(nos[i], outro, sondas))
                self.addCleanup(tarefa.cancel)
        return nos[1], nos[2]

    def _guardar(self, i, origem, tipo, corpo):
        if not (isinstance(corpo, dict) and "sonda" in corpo):  # as sondas não interessam aqui
            self.rx[i].append((origem, tipo, corpo))

    @staticmethod
    async def _sondar(t, outro, intervalo):
        while True:
            if t._transport is None or t._transport.is_closing():
                return
            t.send(outro, Tipo.HELLO, {"sonda": 1}, sonda=True)
            await asyncio.sleep(intervalo)

    async def _fechar(self, nos):
        for t in nos.values():
            t.close()
        await asyncio.sleep(0)

    async def test_entrega_simples(self):
        a, b = await self.par()
        a.send(2, Tipo.HELLO, {"id": 1}, fiavel=True)
        a.send(2, Tipo.DATA, b"frame", fluxo=(1, 0))
        await asyncio.sleep(0.1)
        self.assertEqual(self.rx[2], [(1, Tipo.HELLO, {"id": 1}), (1, Tipo.DATA, b"frame")])
        self.assertFalse(a._pendentes)  # o HELLO foi confirmado
        self.assertIsNotNone(a.rtt(2))

    async def test_perda_entrega_exatamente_uma_vez(self):
        a, b = await self.par(perda=0.3, max_tentativas=12)
        for n in range(30):
            a.send(2, Tipo.HELLO, {"n": n}, fiavel=True)
        for _ in range(200):
            await asyncio.sleep(0.05)
            if not a._pendentes:
                break
        recebidos = sorted(c["n"] for o, tp, c in self.rx[2])
        self.assertEqual(recebidos, list(range(30)))  # todas, sem duplicados
        self.assertEqual(self.down[1], [])

    async def test_sondas_mantem_vizinho_vivo_e_medem_rtt(self):
        a, b = await self.par()
        await asyncio.sleep(1.0)  # ~3x o limite de silêncio, sem tráfego da aplicação
        self.assertEqual(self.down, {1: [], 2: []})
        self.assertIsNotNone(a.rtt(2))  # RTT medido só com os ACK das sondas
        self.assertEqual(a.perda(2), 0.0)

    async def test_sonda_nao_e_retransmitida(self):
        a, b = await self.par(perda=1.0, sondas=0, idle_timeout=10)
        a.send(2, Tipo.HELLO, {"sonda": 1}, sonda=True)
        self.assertFalse(a._pendentes)  # não fica à espera de ACK para reenviar

    async def test_com_trafego_continua_a_medir_rtt(self):
        a, b = await self.par(sondas=0.05)
        self.assertIsNone(a.rtt_min(2))
        for _ in range(40):
            a.send(2, Tipo.DATA, b"frame", fluxo=(1, 0))
            await asyncio.sleep(0.01)
        self.assertGreaterEqual(len(a._amostras_rtt[2]), 5)
        self.assertIsNotNone(a.rtt_min(2))
        self.assertLessEqual(a.rtt_min(2), a.rtt(2) + 1e-9)
        self.assertIsNotNone(a.jitter(2))
        self.assertEqual(a.perda(2), 0.0)

    async def test_perda_aproxima_se_da_simulada(self):
        a, b = await self.par(perda=0.3, sondas=0.02, idle_timeout=10)
        b.perda_simulada = 0.0  # só se perde o que o 1 envia (30% no sentido 1 -> 2)
        with patch.object(ott_transporte, "ESPERA_ACK_SONDA", 0.2):
            await asyncio.sleep(1.5)
            self.assertEqual(len(a._vizinhos[2].sondas), ott_transporte.AMOSTRAS_PERDA)
            self.assertAlmostEqual(a.perda(2), 0.3, delta=0.2)  # perdem-se as sondas do 1
            self.assertAlmostEqual(b.perda(1), 0.3, delta=0.2)  # perdem-se os ACK do 1 (ida e volta)

    async def test_limite_segue_o_intervalo_anunciado_e_o_rto(self):
        a, b = await self.par(sondas=0, idle_timeout=None)
        a.set_intervalo(2, 2.0)
        self.assertAlmostEqual(a.limite(2), 6.0)  # 3 intervalos, como no BFD
        a.set_intervalo(2, 0.1)
        self.assertAlmostEqual(a.limite(2), 0.3)  # RTO ainda é o inicial (0.05): manda o intervalo
        a._rtt[2] = [0.5, 0.1]  # ligação lenta: RTO = 0.5 + 4 x 0.1 = 0.9
        self.assertAlmostEqual(a.limite(2), 3 * 0.9)  # nunca abaixo de 3 x RTO (regra do QUIC)

    async def test_vizinho_morto_detetado_pelo_intervalo_anunciado(self):
        a, b = await self.par(sondas=0.05, idle_timeout=None)
        a.set_intervalo(2, 0.1)  # limite = 3 x max(0.1, RTO de 0.05 a 0.2 s)
        await asyncio.sleep(0.3)
        self.assertEqual(self.down[1], [])
        b.close()
        await asyncio.sleep(1.0)
        self.assertEqual(self.down[1], [2])

    async def test_deteta_vizinho_morto(self):
        a, b = await self.par()
        b.close()
        await asyncio.sleep(0.6)
        self.assertEqual(self.down[1], [2])
        self.assertNotIn(2, a.neighbors)

    async def test_acks_perdidos_nao_matam_vizinho_vivo(self):
        # Caso visto no CORE: com perda alta, todas as tentativas de uma mensagem ficaram sem
        # ACK e o vizinho (vivo) era dado como em baixo. Agora só o idle timeout decide.
        a, b = await self.par(perda=0.4, max_tentativas=2, idle_timeout=10)
        for n in range(20):
            a.send(2, Tipo.HELLO, {"n": n}, fiavel=True)
        for _ in range(200):
            await asyncio.sleep(0.05)
            if not a._pendentes:
                break
        self.assertEqual(self.down[1], [])
        self.assertEqual(sorted(c["n"] for o, tp, c in self.rx[2]), list(range(20)))

    async def test_nao_vizinho_desiste_ao_fim_das_tentativas(self):
        a, b = await self.par(max_tentativas=3)
        a.set_address(9, ("127.0.0.1", 9))  # ninguém à escuta; não é vizinho
        a.send(9, Tipo.REGISTER, {}, fiavel=True)
        await asyncio.sleep(0.6)  # 0.05 + 0.1 + 0.2 s
        self.assertFalse(a._pendentes)
        self.assertEqual(self.down[1], [])

    async def test_vizinho_reiniciado_nao_e_tomado_por_duplicado(self):
        a, b = await self.par()
        b.send(1, Tipo.HELLO, {"vida": 1}, fiavel=True)
        await asyncio.sleep(0.1)
        porta_b = b.endereco_local[1]
        b.close()
        await asyncio.sleep(0.6)  # a dá o b como em baixo
        b2 = Transporte(2, seed=22, **RAPIDO)  # mesma seed: sem ISN aleatório colidia
        await b2.start("127.0.0.1", porta_b)
        self.addAsyncCleanup(self._fechar, {2: b2})
        b2.add_neighbor(1, a.endereco_local)
        b2.send(1, Tipo.HELLO, {"vida": 2}, fiavel=True)
        await asyncio.sleep(0.1)
        self.assertEqual([c for o, tp, c in self.rx[1]], [{"vida": 1}, {"vida": 2}])

    async def test_no_sem_id_recebe_resposta_do_bootstrapper(self):
        boot = Transporte(0, on_message=lambda o, tp, c, f, addr: boot.send(0, Tipo.REGISTER_ACK, {"id": 7}, True) if tp == Tipo.REGISTER else None)
        no_rx = []
        no = Transporte(on_message=lambda o, tp, c, f, a: no_rx.append(c))
        await boot.start("127.0.0.1", 0)
        await no.start("127.0.0.1", 0)
        self.addAsyncCleanup(self._fechar, {0: boot, 1: no})
        boot.set_address(0, no.endereco_local)  # (no bootstrapper real: guardar addr por IP)
        no.set_address(0, boot.endereco_local)
        no.send(0, Tipo.REGISTER, {}, fiavel=True)
        await asyncio.sleep(0.1)
        self.assertEqual(no_rx, [{"id": 7}])
        self.assertFalse(no._pendentes)


if __name__ == "__main__":
    unittest.main()

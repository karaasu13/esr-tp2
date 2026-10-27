"""Testes do transporte em localhost. Correr com: python3 -m unittest -v"""
import asyncio
import unittest

from ott.protocolo import CABECALHO, Flag, Pacote, Tipo
from ott.transporte import Transporte

RAPIDO = dict(keepalive=0.1, idle_timeout=0.35, rto_inicial=0.05, rto_max=0.3)


class TestPacote(unittest.TestCase):
    def test_cabecalho_tem_14_bytes(self):
        self.assertEqual(CABECALHO.size, 14)

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
    async def par(self, perda=0.0, **kw):
        """Dois nós (1 e 2) vizinhos um do outro, em portas livres de localhost."""
        self.rx = {1: [], 2: []}
        self.down = {1: [], 2: []}
        nos = {}
        for i, seed in ((1, 11), (2, 22)):
            t = Transporte(i, perda=perda, seed=seed, **{**RAPIDO, **kw},
                           on_message=lambda o, tp, c, f, a, i=i: self.rx[i].append((o, tp, c)),
                           on_neighbor_down=lambda n, i=i: self.down[i].append(n))
            await t.start("127.0.0.1", 0)
            nos[i] = t
        nos[1].add_neighbor(2, nos[2].endereco_local)
        nos[2].add_neighbor(1, nos[1].endereco_local)
        self.addAsyncCleanup(self._fechar, nos)
        return nos[1], nos[2]

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

    async def test_keepalive_mantem_vizinho_vivo(self):
        a, b = await self.par()
        await asyncio.sleep(1.0)  # ~3x o idle timeout, sem tráfego da aplicação
        self.assertEqual(self.down, {1: [], 2: []})
        self.assertIsNotNone(a.rtt(2))  # RTT medido só com os keepalive

    async def test_deteta_vizinho_morto(self):
        a, b = await self.par()
        b.close()
        await asyncio.sleep(0.6)
        self.assertEqual(self.down[1], [2])
        self.assertNotIn(2, a.neighbors)

    async def test_sem_ack_da_vizinho_como_morto(self):
        a, b = await self.par(max_tentativas=3, idle_timeout=10)
        b.close()
        a.send(2, Tipo.HELLO, {}, fiavel=True)
        await asyncio.sleep(0.6)  # 0.05 + 0.1 + 0.2 s de espera
        self.assertEqual(self.down[1], [2])

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

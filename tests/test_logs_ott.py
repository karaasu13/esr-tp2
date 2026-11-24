"""Testes da leitura dos logs (scripts/logs_ott.py), usada pelo painel e pelo desenho da árvore.

Usam logs escritos à mão, com os números dos nós do cenário 3 (S1=17, N1=13, N5=10, C1=14).
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import logs_ott as L  # noqa: E402

LOGS = {
    "S1": [
        "10:00:00.000 S1 INFO [0] à escuta em 10.0.20.20:5000",
        "10:00:01.000 S1 INFO [17] tabela de vizinhos: 13@10.0.18.20",
        "10:00:05.000 S1 INFO [17] fluxo (17, 0): pai=None destinos=13 ativo=True",
    ],
    "N1": [
        "10:00:00.000 N1 INFO [0] à escuta em 10.0.18.20:5000",
        "10:00:01.000 N1 INFO [13] tabela de vizinhos: 17@10.0.20.20, 14@10.0.19.20",
        "10:00:02.000 N1 INFO [13] pai para o servidor 17: 17 (custo 1.0)",
        "10:00:05.000 N1 INFO [13] fluxo (17, 0): pai=17 destinos=14 ativo=True",
    ],
    "N5": [  # morreu sem aviso com o C1 nos destinos: a linha ficou velha
        "10:00:00.000 N5 INFO [0] à escuta em 10.0.14.20:5000",
        "10:00:03.000 N5 INFO [10] fluxo (17, 0): pai=13 destinos=14 ativo=True",
        "10:00:04.000 N5 INFO no.sh: morto com kill -9 (sem aviso)",
    ],
    "C1": [
        "10:00:00.000 C1 INFO [0] à escuta em 10.0.19.20:5000",
        "10:00:01.000 C1 INFO [14] tabela de vizinhos: 13@10.0.18.20",
        "10:00:02.000 C1 INFO [14] pai para o servidor 17: 13 (custo 2.0)",
        "10:00:04.000 C1 INFO [14] vídeo 0: servidor 17 (custo 2.0)",
        "10:00:06.000 C1 INFO PING 1 do vídeo (17, 0): 17 → 13 (+0.5 ms) → 14 (+0.3 ms)",
        "10:00:07.000 C1 INFO [14] vizinho 10 em baixo (sem sinal há 3.0 s)",
    ],
    "C1-player": [
        "10:00:09.000 C1 INFO player: frame 90 · recebidos 88 de 90 · em falta 2 (2.2 %) · atrasados 0",
    ],
}


class TestLogsOtt(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.antes = L.LOGS
        L.LOGS = Path(self.pasta.name)
        for nome, linhas in LOGS.items():
            (L.LOGS / f"cenario3-{nome}.log").write_text("\n".join(linhas) + "\n", encoding="utf-8")

    def tearDown(self):
        L.LOGS = self.antes
        self.pasta.cleanup()

    def test_arvore_segue_o_pai_que_cada_no_diz_ter(self):
        estado = L.analisar("3")
        [arv] = L.arvores(estado)
        self.assertEqual((arv.raiz, arv.video), ("S1", "0"))
        self.assertEqual(arv.filhos, {"S1": ["N1"], "N1": ["C1"]})  # o N5 morto não conta como pai do C1
        self.assertEqual(arv.ao_player, {"C1"})
        texto = "\n".join(L.texto_arvore(arv, estado))
        self.assertIn("└─ C1 ▶", texto)

    def test_estado_dos_nos_e_do_cliente(self):
        estado = L.analisar("3")
        self.assertEqual(estado.nos["N5"].estado, L.MORTO)
        self.assertEqual(estado.nos["N1"].estado, L.ATIVO)
        c1 = estado.nos["C1"]
        self.assertEqual([i for i, _ms in c1.ping[1]], ["17", "13", "14"])
        self.assertAlmostEqual(sum(ms for _i, ms in c1.ping[1] if ms), 0.8)
        self.assertEqual(c1.player.falta_pct, 2.2)

    def test_hora_limite_da_o_estado_nesse_instante(self):
        estado = L.analisar("3", ate="10:00:03")
        self.assertEqual(estado.nos["N5"].estado, L.ATIVO)  # ainda não tinha morrido
        self.assertNotIn("0", estado.nos["C1"].videos)       # o C1 ainda não via o vídeo

    def test_no_reiniciado_esquece_o_estado_antigo(self):
        with open(L.LOGS / "cenario3-N1.log", "a", encoding="utf-8") as f:
            f.write("10:00:10.000 N1 INFO [0] à escuta em 10.0.18.20:5000\n")
        estado = L.analisar("3")
        self.assertEqual(estado.nos["N1"].fluxos, {})
        self.assertEqual(estado.nos["N1"].estado, L.A_ARRANCAR)

    def test_avisos_iguais_ficam_numa_linha(self):
        with open(L.LOGS / "cenario3-S1.log", "a", encoding="utf-8") as f:
            f.write("10:00:01.000 S1 INFO [17] vizinho 13 ativo\n10:00:07.500 S1 INFO [17] vizinho 13 em baixo (sem sinal há 3.0 s)\n")
        with open(L.LOGS / "cenario3-C1.log", "a", encoding="utf-8") as f:
            f.write("10:00:01.000 C1 INFO [14] vizinho 13 ativo\n10:00:07.600 C1 INFO [14] vizinho 13 em baixo (sem sinal há 3.0 s)\n")
        textos = [a.texto for a in L.analisar("3").acontecimentos if "N1 em baixo" in a.texto]
        self.assertEqual(len(textos), 1)
        self.assertIn("visto por", textos[0])


if __name__ == "__main__":
    unittest.main()

"""Programas que o nó arranca sozinho (ott/locais.py): um Servidor.py que morre volta a
arrancar, e o fecho do player avisa o nó. Usa scripts falsos em vez dos verdadeiros."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ott import locais


class TestProgramasLocais(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.pasta = Path(pasta.name)
        self.arranques = self.pasta / "arranques.txt"
        # Escreve uma linha por arranque e morre logo (como um Servidor.py que alguém matou).
        self.morre = self.pasta / "morre.py"
        self.morre.write_text(
            f"open({str(self.arranques)!r}, 'a').write('x\\n')\nimport sys; sys.exit(1)\n")

    async def test_servidor_que_morre_volta_a_arrancar_ate_o_no_fechar(self):
        programas = locais.ProgramasLocais("S1")
        with patch.object(locais, "SERVIDOR_PY", self.morre), \
                patch.object(locais, "ESPERA_REINICIO", 0.1):
            await programas.servidores([self.morre])
            await asyncio.sleep(0.8)
            await programas.fechar()
            arranques = self.arranques.read_text().count("x")
            self.assertGreaterEqual(arranques, 3)
            await asyncio.sleep(0.4)  # depois de fechar, não volta a arrancar
            self.assertEqual(self.arranques.read_text().count("x"), arranques)

    async def test_player_que_fecha_avisa_o_no_e_nao_volta_a_abrir(self):
        programas = locais.ProgramasLocais("C1")
        fechou = asyncio.Event()
        with patch.object(locais, "CLIENTE_PY", self.morre):
            await programas.player(14, ao_fechar=fechou.set)
            await asyncio.wait_for(fechou.wait(), 3)
            await asyncio.sleep(0.3)
            await programas.fechar()
        self.assertEqual(self.arranques.read_text().count("x"), 1)


if __name__ == "__main__":
    unittest.main()

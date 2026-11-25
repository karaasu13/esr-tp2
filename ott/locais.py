"""Programas locais que o próprio nó arranca, para cada nó se arrancar com um só comando.

* `--videos movie movie2`: um `player/Servidor.py` por vídeo, a enviar para o nó em
  127.0.0.1:24000 + k. A saída deles (uma linha por frame) vai para o lixo; os erros vêm para o log.
* `--ver k` sem `--player`: o `player/Cliente.py`, na porta 25000. As linhas que o player escreve
  (resumo de 5 em 5 s, paragens do vídeo) aparecem no terminal do nó.

Se um Servidor.py morrer (alguém o matou), o nó volta a arrancá-lo passado 1 s. Se o player fechar
(o utilizador fechou a janela), o nó é avisado, para deixar de pedir o vídeo.

Os filhos morrem com o nó, mesmo que este seja morto sem aviso (Ctrl+\\ ou kill -9): pedem ao
kernel um SIGTERM quando o pai morre (prctl PR_SET_PDEATHSIG). Correm num grupo de processos
próprio, para o Ctrl+C no terminal chegar só ao nó, que depois os fecha com calma.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Awaitable, Callable

from .video import LOCALHOST, PORTA_PLAYER, PORTA_SERVIDOR

log = logging.getLogger("ott.locais")

PROJETO = Path(__file__).resolve().parent.parent
PASTA_VIDEOS = PROJETO / "videos"
SERVIDOR_PY = PROJETO / "player" / "Servidor.py"
CLIENTE_PY = PROJETO / "player" / "Cliente.py"
PR_SET_PDEATHSIG = 1
ESPERA_REINICIO = 1.0  # s antes de voltar a arrancar um Servidor.py que morreu


def ficheiro_video(nome: str) -> Path:
    """`movie`, `movie.Mjpeg` ou um caminho: devolve o ficheiro, ou ValueError se não existir."""
    for candidato in (Path(nome), PASTA_VIDEOS / nome, PASTA_VIDEOS / f"{nome}.Mjpeg"):
        if candidato.is_file():
            return candidato.resolve()
    raise ValueError(f"vídeo não encontrado: {nome} (procurei também em {PASTA_VIDEOS})")


def _morrer_com_o_pai() -> None:  # corre no filho, entre o fork e o exec
    try:
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except (OSError, AttributeError):
        pass  # fora do Linux: os filhos ficam órfãos se o nó morrer sem aviso


class ProgramasLocais:
    """Arranca e fecha os programas locais de um nó."""

    def __init__(self, nome: str = "", escrever: Callable[[str], None] | None = None):
        self.nome = nome
        self.escrever = escrever or (lambda linha: print(linha, flush=True))
        self._processos: list[asyncio.subprocess.Process] = []
        self._tarefas: list[asyncio.Task] = []
        self._a_fechar = False

    async def servidores(self, videos: list[Path]) -> list[int]:
        """Um Servidor.py por vídeo; o k-ésimo envia para 127.0.0.1:24000 + k. Devolve as portas."""
        portas = []
        for k, video in enumerate(videos):
            porta = PORTA_SERVIDOR + k
            await self._servidor(k, video, porta)
            portas.append(porta)
        return portas

    async def _servidor(self, k: int, video: Path, porta: int) -> None:
        async def reiniciar() -> None:  # morreu sem ser o nó a fechá-lo: volta a arrancá-lo
            await asyncio.sleep(ESPERA_REINICIO)
            if not self._a_fechar:
                log.warning("Servidor.py do vídeo %d morreu: volto a arrancá-lo", k)
                await self._servidor(k, video, porta)

        await self._lancar(f"Servidor.py (vídeo {k}: {video.name})",
                           [str(SERVIDOR_PY), str(video), LOCALHOST, str(porta)],
                           mostrar_saida=False, ao_terminar=reiniciar)

    async def player(self, id_no: int | None, porta: int = PORTA_PLAYER,
                     ao_fechar: Callable[[], None] | None = None) -> None:
        env = {}
        if self.nome:
            env["OTT_NOME"] = self.nome
        if id_no is not None:
            env["OTT_ID"] = str(id_no)
        if not os.environ.get("DISPLAY"):
            # As shells dos nós do CORE nem sempre trazem o DISPLAY; o ecrã do PC é quase sempre :0.
            env["DISPLAY"] = ":0"
        async def fechou() -> None:
            if ao_fechar is not None:
                ao_fechar()

        await self._lancar("player (Cliente.py)", [str(CLIENTE_PY), str(porta)], env=env,
                           mostrar_saida=True, ao_terminar=fechou)

    async def _lancar(self, descricao: str, argumentos: list[str], *, env: dict | None = None,
                      mostrar_saida: bool,
                      ao_terminar: Callable[[], Awaitable[None]] | None = None) -> None:
        ambiente = {**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1"}
        processo = await asyncio.create_subprocess_exec(
            sys.executable, *argumentos,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE if mostrar_saida else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=ambiente, preexec_fn=_morrer_com_o_pai, start_new_session=True,
        )
        self._processos.append(processo)
        log.info("arrancado: %s (pid %d)", descricao, processo.pid)
        if mostrar_saida:
            self._tarefas.append(asyncio.create_task(self._reencaminhar(processo.stdout, None)))
        self._tarefas.append(asyncio.create_task(self._reencaminhar(processo.stderr, descricao)))
        self._tarefas.append(asyncio.create_task(self._vigiar(processo, descricao, ao_terminar)))

    async def _reencaminhar(self, fluxo: asyncio.StreamReader, erros_de: str | None) -> None:
        """Passa a saída do filho para o terminal do nó (os erros, com o nome do programa)."""
        while linha := await fluxo.readline():
            texto = linha.decode(errors="replace").rstrip()
            if not texto:
                continue
            if erros_de is None:
                self.escrever(texto)
            else:
                log.warning("%s: %s", erros_de, texto)

    async def _vigiar(self, processo: asyncio.subprocess.Process, descricao: str,
                      ao_terminar: Callable[[], Awaitable[None]] | None) -> None:
        codigo = await processo.wait()
        if self._a_fechar:
            return
        if ao_terminar is not None:
            asyncio.create_task(ao_terminar())
        if codigo not in (0, -signal.SIGTERM, -signal.SIGKILL):
            log.warning("%s terminou com o código %d", descricao, codigo)
            if descricao.startswith("player"):
                log.warning("se o erro for do ecrã (display), correr uma vez no PC, fora do CORE: "
                            "xhost +SI:localuser:root")

    async def fechar(self, espera: float = 1.0) -> None:
        self._a_fechar = True
        for processo in self._processos:
            if processo.returncode is None:
                processo.terminate()
        for processo in self._processos:
            try:
                await asyncio.wait_for(processo.wait(), espera)
            except asyncio.TimeoutError:
                processo.kill()
        for tarefa in self._tarefas:
            tarefa.cancel()
        self._processos.clear()
        self._tarefas.clear()

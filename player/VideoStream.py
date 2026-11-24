"""Frames de um ficheiro MJPEG e o número de cada frame, tirado do relógio.

O número do frame não conta "desde que este servidor arrancou": é o instante em que o frame sai,
em vigésimos de segundo desde 1970 (frame n = instante n / FPS). A imagem do frame n é a
n-ésima do ficheiro, em ciclo (n % nº de frames). Assim, dois servidores com o mesmo vídeo e o
relógio certo enviam o mesmo número com a mesma imagem no mesmo instante, arranquem quando
arrancarem, e o cliente pode trocar de um para o outro sem congelar nem saltar (como num canal
em direto: o MPEG-DASH em direto também numera os segmentos pela hora UTC).
"""
import math

FPS = 20  # frames por segundo (um de 50 em 50 ms, como no ProgEx)


def numero_do_instante(t):
	"""Número do frame que sai no instante t (segundos desde 1970, como o time.time())."""
	return math.floor(t * FPS)


def instante_do_numero(n):
	"""Instante (segundos desde 1970) em que sai o frame n."""
	return n / FPS


class VideoStream:
	def __init__(self, filename):
		"""Lê o ficheiro todo (cada frame vem precedido de 5 bytes ASCII com o tamanho)."""
		self.filename = filename
		try:
			with open(filename, 'rb') as f:
				dados = f.read()
		except OSError:
			raise IOError
		self.frames = []
		i = 0
		while i < len(dados):
			tamanho = int(dados[i:i + 5])
			self.frames.append(dados[i + 5:i + 5 + tamanho])
			i += 5 + tamanho
		if not self.frames:
			raise IOError(f"{filename}: sem frames")

	def __len__(self):
		return len(self.frames)

	def frame(self, n):
		"""Imagem (JPEG) do frame n: a n-ésima do ficheiro, em ciclo."""
		return self.frames[n % len(self.frames)]

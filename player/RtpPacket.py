from time import time
HEADER_SIZE = 12
SEQ_MOD = 1 << 16  # o nº de sequência do RTP tem 16 bits e dá a volta: ... 65534, 65535, 0, 1 ...


def avanco_seq(novo, ultimo):
	"""Quantos números o `novo` está à frente do `ultimo`, tendo em conta a volta aos 16 bits.

	Aritmética de números de série (RFC 1982), como no RFC 3550: é mais novo se estiver menos
	de meia volta (32768) à frente. Devolve 0 ou um número negativo se for repetido ou atrasado.
	"""
	dif = (novo - ultimo) % SEQ_MOD
	return dif if dif < SEQ_MOD // 2 else dif - SEQ_MOD


MAX_ATRASO = 5  # frames (250 ms a 20/s): mais atrás do que isto já não é um pacote fora de ordem


def aceitar(seq, ultimo, suspeito):
	"""Decide se o player aceita o frame `seq`, sendo `ultimo` o último que aceitou.

	Devolve (avanço, suspeito). Com avanço > 0 aceita, e faltaram avanço - 1 frames; com
	avanço <= 0 descarta (repetido ou atrasado).

	Um frame mais de MAX_ATRASO atrás do último não é um atraso normal: mudou a numeração (outro
	servidor com o relógio desacertado, ou um servidor reiniciado). Como no RFC 3550 (apêndice A.1),
	descarta o primeiro e guarda em `suspeito` o número que viria a seguir; se esse chegar, aceita
	a numeração nova (avanço 1: a contagem continua, a imagem salta). Assim o congelamento fica em
	~2 frames, em vez de durar até a numeração nova passar a antiga. `suspeito` começa a None.
	"""
	avanco = avanco_seq(seq, ultimo)
	if avanco > 0:
		return avanco, None
	if avanco < -MAX_ATRASO:
		if seq == suspeito:
			return 1, None
		return avanco, (seq + 1) % SEQ_MOD
	return avanco, suspeito


TS_MOD = 1 << 32  # o campo timestamp do RTP tem 32 bits


def hora_ms():
	"""Hora atual em milissegundos, cortada a 32 bits (dá a volta a cada 49,7 dias).

	Vai no campo timestamp do RTP como hora de envio do frame, para o cliente medir o atraso.
	No CORE todos os nós partilham o relógio do PC, por isso a diferença é o atraso real.
	"""
	return int(time() * 1000) % TS_MOD


def diferenca_ms(chegada_ms, envio_ms):
	"""Atraso em ms entre a hora de envio (32 bits, de hora_ms) e a hora de chegada (sem corte).

	Tem em conta a volta aos 32 bits; um valor negativo quer dizer relógios desacertados.
	"""
	dif = (chegada_ms - envio_ms) % TS_MOD
	return dif if dif < TS_MOD // 2 else dif - TS_MOD


class RtpPacket:	
	header = bytearray(HEADER_SIZE)
	
	def __init__(self):
		pass
		
	def encode(self, version, padding, extension, cc, seqnum, marker, pt, ssrc, payload):
		"""Encode the RTP packet with header fields and payload."""
		timestamp = hora_ms() # hora de envio em ms (no original: segundos), para medir o atraso
		header = bytearray(HEADER_SIZE) 
		header[0] = (header[0] | version << 6) & 0xC0; # 2 bits
		header[0] = (header[0] | padding << 5); # 1 bit
		header[0] = (header[0] | extension << 4); # 1 bit
		header[0] = (header[0] | (cc & 0x0F)); # 4 bits
		header[1] = (header[1] | marker << 7); # 1 bit
		header[1] = (header[1] | (pt & 0x7f)); # 7 bits
		seqnum %= SEQ_MOD # dá a volta aos 16 bits em vez de rebentar no frame 65536
		header[2] = (seqnum >> 8) & 0xFF;
		header[3] = (seqnum & 0xFF);
		header[4] = (timestamp >> 24);
		header[5] = (timestamp >> 16) & 0xFF;
		header[6] = (timestamp >> 8) & 0xFF;
		header[7] = (timestamp & 0xFF);
		header[8] = (ssrc >> 24);
		header[9] = (ssrc >> 16) & 0xFF;
		header[10] = (ssrc >> 8) & 0xFF;
		header[11] = ssrc & 0xFF
		# set header and  payload
		self.header = header
		self.payload = payload
		
	def decode(self, byteStream):
		"""Decode the RTP packet."""
		self.header = bytearray(byteStream[:HEADER_SIZE])
		self.payload = byteStream[HEADER_SIZE:]
	
	def version(self):
		"""Return RTP version."""
		return int(self.header[0] >> 6)
	
	def seqNum(self):
		"""Return sequence (frame) number."""
		seqNum = self.header[2] << 8 | self.header[3]
		return int(seqNum)
	
	def timestamp(self):
		"""Return timestamp."""
		timestamp = self.header[4] << 24 | self.header[5] << 16 | self.header[6] << 8 | self.header[7]
		return int(timestamp)
	
	def payloadType(self):
		"""Return payload type."""
		pt = self.header[1] & 127
		return int(pt)
	
	def getPayload(self):
		"""Return payload."""
		return self.payload
		
	def getPacket(self):
		"""Return RTP packet."""
		return self.header + self.payload

	def printheader(self):
		print("[RTP Packet] Version: ...")



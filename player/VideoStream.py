class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		try:
			self.file = open(filename, 'rb')
		except:
			raise IOError
		self.frameNum = 0
		
	def nextFrame(self):
		"""Get next frame."""
		data = self.file.read(5) # Get the framelength from the first 5 bits
		if data: 
			framelength = int(data)
							
			# Read the current frame
			data = self.file.read(framelength)
			self.frameNum += 1
		return data
		
	def rewind(self):
		"""Volta ao início do ficheiro. O número do frame NÃO volta a zero: continua a subir,
		porque o player deita fora frames com número igual ou inferior ao último."""
		self.file.seek(0)

	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum
	
	
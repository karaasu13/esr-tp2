-- Leitor do protocolo OTT (TP2 ESR) para o Wireshark e o tshark.
--
-- Mostra os campos do nosso cabeçalho pelo nome, o corpo JSON das mensagens de controlo (com o
-- leitor de JSON do Wireshark) e o pacote RTP que vai dentro de cada DATA.
--
-- Usar:
--   wireshark -X lua_script:wireshark/ott.lua captura.pcap
--   tshark -X lua_script:wireshark/ott.lua -r captura.pcap
-- ou copiar este ficheiro para ~/.local/lib/wireshark/plugins/ (fica sempre ativo).
-- Filtros: ott, ott.tipo == 0x03 (HELLO), ott.fiavel == 1, ott.origem == 13, ott.fluxo.servidor == 17

local ott = Proto("ott", "OTT (TP2 ESR, protocolo QUIC-like sobre UDP)")

local tipos = {
  [0x01] = "REGISTER", [0x02] = "REGISTER_ACK", [0x03] = "HELLO", [0x06] = "ACK",
  [0x07] = "CLOSE", [0x10] = "ANNOUNCE", [0x11] = "JOIN", [0x12] = "LEAVE",
  [0x13] = "WITHDRAW", [0x14] = "PING", [0x15] = "SOCORRO", [0x16] = "AVO", [0x20] = "DATA",
}

local f = ott.fields
f.versao   = ProtoField.uint8("ott.versao", "Versão", base.DEC)
f.tipo     = ProtoField.uint8("ott.tipo", "Tipo", base.HEX, tipos)
f.flags    = ProtoField.uint8("ott.flags", "Flags", base.HEX)
f.fiavel   = ProtoField.bool("ott.fiavel", "FIAVEL (pede ACK)", 8, nil, 0x01)
f.origem   = ProtoField.uint16("ott.origem", "Origem (nº do nó)", base.DEC)
f.destino  = ProtoField.uint16("ott.destino", "Destino (nº do nó)", base.DEC)
f.pn       = ProtoField.uint32("ott.pn", "Nº do pacote (pn)", base.DEC)
f.servidor = ProtoField.uint16("ott.fluxo.servidor", "Fluxo: servidor", base.DEC)
f.stream   = ProtoField.uint8("ott.fluxo.stream", "Fluxo: vídeo", base.DEC)
f.id_msg   = ProtoField.uint32("ott.id_msg", "Id da mensagem", base.DEC)
f.ack_pn   = ProtoField.uint32("ott.ack.pn", "Confirma o pacote", base.DEC)
f.corpo    = ProtoField.string("ott.corpo", "Corpo (JSON)")

local CABECALHO = 14
local json = Dissector.get("json")
local rtp = Dissector.get("rtp")

function ott.dissector(buf, pinfo, arvore)
  if buf:len() < CABECALHO or buf(0, 1):uint() ~= 1 then
    return 0  -- não é nosso (ou é de outra versão)
  end
  pinfo.cols.protocol = "OTT"
  local tipo = buf(1, 1):uint()
  local flags = buf(2, 1):uint()
  local fiavel = bit.band(flags, 0x01) ~= 0
  local origem, destino = buf(3, 2):uint(), buf(5, 2):uint()
  local nome = tipos[tipo] or string.format("tipo 0x%02x", tipo)

  local t = arvore:add(ott, buf(0, CABECALHO), "OTT, " .. nome)
  t:add(f.versao, buf(0, 1))
  t:add(f.tipo, buf(1, 1))
  local tf = t:add(f.flags, buf(2, 1))
  tf:add(f.fiavel, buf(2, 1))
  t:add(f.origem, buf(3, 2))
  t:add(f.destino, buf(5, 2))
  t:add(f.pn, buf(7, 4))
  t:add(f.servidor, buf(11, 2))
  t:add(f.stream, buf(13, 1))

  local pos = CABECALHO
  if fiavel and buf:len() >= pos + 4 then
    t:add(f.id_msg, buf(pos, 4))
    pos = pos + 4
  end

  local info = string.format("%s %d → %d pn=%d", nome, origem, destino, buf(7, 4):uint())
  local servidor, stream = buf(11, 2):uint(), buf(13, 1):uint()
  if servidor ~= 0 then
    info = info .. string.format(" fluxo=(%d, %d)", servidor, stream)
  end
  if fiavel then
    info = info .. (tipo == 0x03 and " [sonda]" or " [fiável]")
  end

  local resto = buf:len() - pos
  if tipo == 0x06 and resto >= 4 then           -- ACK: o pn confirmado, em binário
    t:add(f.ack_pn, buf(pos, 4))
    info = info .. string.format(" confirma pn=%d", buf(pos, 4):uint())
  elseif tipo == 0x20 and resto > 0 then         -- DATA: o pacote RTP do Servidor.py, intacto
    if rtp then rtp:call(buf(pos):tvb(), pinfo, arvore) end
    pinfo.cols.protocol = "OTT/RTP"
  elseif resto > 0 then                          -- controlo: JSON
    t:add(f.corpo, buf(pos))
    if json then json:call(buf(pos):tvb(), pinfo, t) end
    local texto = buf(pos):string()
    if #texto <= 60 then info = info .. " " .. texto end
  end
  pinfo.cols.info:set(info)
  return buf:len()
end

local udp = DissectorTable.get("udp.port")
udp:add(5000, ott)  -- porta dos nós
udp:add(5001, ott)  -- bootstrapper no cenário 3

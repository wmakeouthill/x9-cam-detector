import React, { useState, useEffect } from 'react';
import { Settings, Save, Server, Shield, CheckCircle, AlertCircle, RefreshCw, Terminal, Sliders, Radio } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';

interface CameraConfigViewProps {
  currentIp: string;
  currentPort: number;
  currentHttpPort: number;
  currentUser: string;
  currentSubtype: number;
  onConfigSaved: () => void;
}

export function CameraConfigView({
  currentIp,
  currentPort,
  currentHttpPort,
  currentUser,
  currentSubtype,
  onConfigSaved,
}: CameraConfigViewProps) {
  const { token } = useAuthStore();
  const [ip, setIp] = useState(currentIp || '192.168.1.108');
  const [port, setPort] = useState(currentPort || 554);
  const [httpPort, setHttpPort] = useState(currentHttpPort || 80);
  const [user, setUser] = useState(currentUser || 'admin');
  const [password, setPassword] = useState('');
  const [subtype, setSubtype] = useState(currentSubtype || 0);

  const [hasLoaded, setHasLoaded] = useState(false);

  useEffect(() => {
    if (!hasLoaded && currentIp) {
      setIp(currentIp);
      setPort(currentPort || 554);
      setHttpPort(currentHttpPort || 80);
      setUser(currentUser || 'admin');
      if (currentSubtype !== undefined) setSubtype(currentSubtype);
      setHasLoaded(true);
    }
  }, [currentIp, currentPort, currentHttpPort, currentUser, currentSubtype, hasLoaded]);

  const [saving, setSaving] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [foundCameras, setFoundCameras] = useState<Array<{ ip: string; ports: number[]; name: string }>>([]);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const handleScan = async () => {
    setScanning(true);
    setMessage(null);
    try {
      const res = await fetch('/api/camera/scan', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        const cams = data.cameras || [];
        setFoundCameras(cams);
        if (cams.length === 0) {
          setMessage({ text: 'Nenhuma câmera com portas RTSP/HTTP detectada na sub-rede.', type: 'error' });
        } else {
          setMessage({
            text: `${cams.length} dispositivo(s) encontrado(s) na rede! Selecione abaixo para aplicar o IP.`,
            type: 'success',
          });
        }
      } else {
        throw new Error('Falha na resposta do scanner.');
      }
    } catch {
      setMessage({ text: 'Erro ao executar varredura de rede.', type: 'error' });
    } finally {
      setScanning(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setMessage(null);

    try {
      const res = await fetch('/api/camera/config', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          ip,
          port: Number(port),
          http_port: Number(httpPort),
          user,
          password: password || '',
          subtype: Number(subtype),
        }),
      });

      if (res.ok) {
        setMessage({
          text: 'Configuração atualizada! O sistema está reconectando à câmera com os novos parâmetros.',
          type: 'success',
        });
        onConfigSaved();
      } else {
        throw new Error('Falha ao atualizar parâmetros.');
      }
    } catch (err: any) {
      setMessage({ text: err.message || 'Erro ao salvar configuração.', type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="w-full h-full p-3.5 sm:p-6 lg:p-8 overflow-y-auto space-y-4 sm:space-y-6">
      {/* Cabeçalho expandido */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4 border-b border-zinc-800/80 pb-4 sm:pb-5">
        <div className="flex items-center gap-2.5 sm:gap-3">
          <div className="w-9 h-9 sm:w-11 sm:h-11 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shadow-[0_0_20px_rgba(16,185,129,0.15)] shrink-0">
            <Settings className="w-5 h-5 sm:w-6 sm:h-6" />
          </div>
          <div>
            <h2 className="text-lg sm:text-xl font-bold font-['Chakra_Petch'] text-zinc-100 uppercase tracking-wide flex flex-wrap items-center gap-2">
              <span>Configuração da Câmera & Rede</span>
              <span className="text-[9px] sm:text-[10px] font-mono px-2 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                INTELBRAS ONVIF/RTSP
              </span>
            </h2>
            <p className="text-[11px] sm:text-xs text-zinc-400 font-mono">
              Parâmetros de comunicação, portas de streaming e autenticação local da Intelbras VIP 1230 FC+
            </p>
          </div>
        </div>
      </div>

      {message && (
        <div
          className={`p-3.5 sm:p-4 rounded-xl border text-xs flex items-center gap-3 font-mono ${
            message.type === 'success'
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
              : 'bg-red-500/10 border-red-500/30 text-red-300'
          }`}
        >
          {message.type === 'success' ? (
            <CheckCircle className="w-5 h-5 shrink-0 text-emerald-400" />
          ) : (
            <AlertCircle className="w-5 h-5 shrink-0 text-red-400" />
          )}
          <span>{message.text}</span>
        </div>
      )}

      {/* Grid Principal aproveitando a largura 1920x1080 */}
      <form onSubmit={handleSubmit} className="space-y-4 sm:space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6">
          {/* Card 1: Endereçamento de Rede e Portas */}
          <div className="bg-[#080B10] border border-zinc-800/90 rounded-xl p-4 sm:p-6 space-y-4 sm:space-y-6 flex flex-col justify-between">
            <div className="space-y-4 sm:space-y-5">
              <div className="flex items-center justify-between border-b border-zinc-800/80 pb-3">
                <span className="text-xs font-bold uppercase tracking-wider text-emerald-400 font-mono flex items-center gap-2">
                  <Server className="w-4 h-4" /> 1. Endereçamento e Protocolos
                </span>
                <span className="text-[10px] font-mono text-zinc-500">NETWORK::CONFIG</span>
              </div>

              <div>
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1.5 sm:gap-2 mb-1.5">
                  <label className="text-xs font-semibold text-zinc-300 font-mono">
                    Endereço IP da Câmera (Fixo ou Auto)
                  </label>
                  <button
                    type="button"
                    onClick={handleScan}
                    disabled={scanning}
                    className="self-start sm:self-auto flex items-center gap-1.5 px-2.5 py-1 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 text-[11px] font-mono transition-all cursor-pointer disabled:opacity-50"
                  >
                    {scanning ? (
                      <RefreshCw className="w-3 h-3 animate-spin" />
                    ) : (
                      <Radio className="w-3 h-3" />
                    )}
                    <span>{scanning ? 'Varrendo Rede...' : 'Auto-Detectar Câmera'}</span>
                  </button>
                </div>
                <input
                  type="text"
                  value={ip}
                  onChange={(e) => setIp(e.target.value)}
                  placeholder="192.168.1.108"
                  required
                  className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg p-2.5 sm:p-3 text-sm text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none min-h-[42px]"
                />
                <p className="text-[10px] text-zinc-500 font-mono mt-1">IP Padrão de fábrica Intelbras: 192.168.1.108</p>

                {foundCameras.length > 0 && (
                  <div className="mt-3 p-3 rounded-lg bg-[#0C1017] border border-emerald-500/30 space-y-2">
                    <p className="text-[11px] font-mono font-bold text-emerald-400 flex items-center gap-1.5">
                      <Radio className="w-3.5 h-3.5" /> Dispositivos Detectados na Rede:
                    </p>
                    <div className="space-y-2">
                      {foundCameras.map((cam) => (
                        <div
                          key={cam.ip}
                          className="flex flex-col sm:flex-row sm:items-center justify-between p-2.5 rounded bg-zinc-900/80 border border-zinc-800 text-xs font-mono gap-2"
                        >
                          <div>
                            <span className="text-zinc-200 font-bold">{cam.ip}</span>
                            <span className="text-[10px] text-zinc-400 ml-2 block sm:inline">
                              {cam.name} (Portas: {cam.ports.join(', ')})
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => {
                              setIp(cam.ip);
                              if (cam.ports.includes(554)) setPort(554);
                              if (cam.ports.includes(80)) setHttpPort(80);
                            }}
                            className="w-full sm:w-auto px-2.5 py-1 sm:py-0.5 rounded bg-emerald-500 hover:bg-emerald-400 text-black text-[10px] font-bold uppercase transition-all cursor-pointer text-center"
                          >
                            Usar IP
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-300 mb-1.5 font-mono">
                    Porta RTSP (Vídeo)
                  </label>
                  <input
                    type="number"
                    value={port}
                    onChange={(e) => setPort(Number(e.target.value))}
                    placeholder="554"
                    required
                    className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg p-2.5 sm:p-3 text-sm text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none min-h-[42px]"
                  />
                  <p className="text-[10px] text-zinc-500 font-mono mt-1">Padrão RTSP: 554</p>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-300 mb-1.5 font-mono">
                    Porta HTTP (CGI / Flash)
                  </label>
                  <input
                    type="number"
                    value={httpPort}
                    onChange={(e) => setHttpPort(Number(e.target.value))}
                    placeholder="80"
                    required
                    className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg p-2.5 sm:p-3 text-sm text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none min-h-[42px]"
                  />
                  <p className="text-[10px] text-zinc-500 font-mono mt-1">Padrão HTTP Web: 80</p>
                </div>
              </div>

              <div className="p-3 sm:p-3.5 rounded-lg bg-[#0C1017] border border-zinc-800/80 text-[11px] font-mono text-zinc-400 flex items-start gap-2.5">
                <Terminal className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                <span className="break-all sm:break-normal">
                  O stream RTSP usa <span className="text-zinc-200">rtsp://usuario:senha@{ip}:{port}/cam/realmonitor?channel=1&subtype={subtype}</span> para ler os pacotes H.264 diretamente via OpenCV/FFmpeg.
                </span>
              </div>
            </div>
          </div>

          {/* Card 2: Credenciais & Resolução de Stream */}
          <div className="bg-[#080B10] border border-zinc-800/90 rounded-xl p-4 sm:p-6 space-y-4 sm:space-y-6 flex flex-col justify-between">
            <div className="space-y-4 sm:space-y-5">
              <div className="flex items-center justify-between border-b border-zinc-800/80 pb-3">
                <span className="text-xs font-bold uppercase tracking-wider text-emerald-400 font-mono flex items-center gap-2">
                  <Shield className="w-4 h-4" /> 2. Autenticação & Qualidade de Fluxo
                </span>
                <span className="text-[10px] font-mono text-zinc-500">AUTH::SECURITY</span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-300 mb-1.5 font-mono">
                    Usuário da Câmera
                  </label>
                  <input
                    type="text"
                    value={user}
                    onChange={(e) => setUser(e.target.value)}
                    placeholder="admin"
                    required
                    className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg p-2.5 sm:p-3 text-sm text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none min-h-[42px]"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-300 mb-1.5 font-mono">
                    Senha da Câmera
                  </label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="•••••••• (deixe vazio para manter a atual)"
                    className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg p-2.5 sm:p-3 text-sm text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none min-h-[42px]"
                  />
                  <p className="text-[10px] sm:text-[11px] text-zinc-500 mt-1 font-mono">
                    🔒 Deixe em branco para manter a senha já gravada no sistema.
                  </p>
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-300 mb-2 font-mono flex items-center gap-1.5">
                  <Sliders className="w-3.5 h-3.5 text-emerald-400" /> Resolução de Leitura do Canal
                </label>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 sm:gap-3">
                  <label
                    className={`p-3 sm:p-3.5 rounded-lg border flex items-center gap-3 cursor-pointer transition-all ${
                      subtype === 0
                        ? 'bg-emerald-950/40 border-emerald-500/50 text-emerald-200 shadow-[0_0_12px_rgba(16,185,129,0.1)]'
                        : 'bg-[#0E131C] border-zinc-800 text-zinc-400 hover:border-zinc-700'
                    }`}
                  >
                    <input
                      type="radio"
                      name="subtype"
                      checked={subtype === 0}
                      onChange={() => setSubtype(0)}
                      className="accent-emerald-500"
                    />
                    <div>
                      <p className="text-xs font-semibold font-mono">Stream 1 (1080p FHD)</p>
                      <p className="text-[10px] text-zinc-400">Máxima fidelidade para detecção noturna</p>
                    </div>
                  </label>

                  <label
                    className={`p-3 sm:p-3.5 rounded-lg border flex items-center gap-3 cursor-pointer transition-all ${
                      subtype === 1
                        ? 'bg-emerald-950/40 border-emerald-500/50 text-emerald-200 shadow-[0_0_12px_rgba(16,185,129,0.1)]'
                        : 'bg-[#0E131C] border-zinc-800 text-zinc-400 hover:border-zinc-700'
                    }`}
                  >
                    <input
                      type="radio"
                      name="subtype"
                      checked={subtype === 1}
                      onChange={() => setSubtype(1)}
                      className="accent-emerald-500"
                    />
                    <div>
                      <p className="text-xs font-semibold font-mono">Substream (480p Leve)</p>
                      <p className="text-[10px] text-zinc-400">Economia máxima de banda e CPU</p>
                    </div>
                  </label>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Barra de Ação Inferior */}
        <div className="pt-4 border-t border-zinc-800 flex flex-col sm:flex-row items-center justify-between gap-3">
          <div className="text-xs font-mono text-zinc-500 hidden sm:block">
            Alterações são aplicadas imediatamente no pipeline de captura
          </div>

          <button
            type="submit"
            disabled={saving}
            className="w-full sm:w-auto flex items-center justify-center gap-2 px-8 py-3 rounded-xl bg-gradient-to-r from-emerald-500 to-green-500 hover:from-emerald-400 hover:to-green-400 text-black font-bold font-mono text-xs uppercase tracking-wider transition-all disabled:opacity-50 cursor-pointer shadow-lg shadow-emerald-500/20 min-h-[44px]"
          >
            {saving ? (
              <RefreshCw className="w-4 h-4 animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            <span>{saving ? 'Conectando...' : 'Salvar e Conectar'}</span>
          </button>
        </div>
      </form>
    </div>
  );
}

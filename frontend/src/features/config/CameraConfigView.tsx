import React, { useState, useEffect, useRef } from 'react';
import {
  Settings,
  Save,
  Server,
  Shield,
  CheckCircle,
  AlertCircle,
  RefreshCw,
  Terminal,
  Sliders,
  Radar,
  Camera,
  Network,
  ArrowUpRight,
  HelpCircle,
  Sun,
  Moon,
  Clock,
  Film,
  Target,
} from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';

interface CameraConfigViewProps {
  currentIp: string;
  currentPort: number;
  currentHttpPort: number;
  currentUser: string;
  currentSubtype: number;
  currentImageProfileMode?: 'auto' | 'day' | 'night';
  currentNightStartTime?: string;
  currentNightEndTime?: string;
  currentClipMinDuration?: number;
  currentClipMaxDuration?: number;
  currentTargetOverlay?: boolean;
  onConfigSaved: () => void;
}

interface FoundDevice {
  ip: string;
  ports: number[];
  name: string;
  subnet?: string;
  is_camera?: boolean;
}

export function CameraConfigView({
  currentIp,
  currentPort,
  currentHttpPort,
  currentUser,
  currentSubtype,
  currentImageProfileMode,
  currentNightStartTime,
  currentNightEndTime,
  currentClipMinDuration,
  currentClipMaxDuration,
  currentTargetOverlay,
  onConfigSaved,
}: CameraConfigViewProps) {
  const { token } = useAuthStore();
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [ip, setIp] = useState(currentIp || '');
  const [port, setPort] = useState(currentPort || 554);
  const [httpPort, setHttpPort] = useState(currentHttpPort || 80);
  const [user, setUser] = useState(currentUser || 'admin');
  const [password, setPassword] = useState('');
  const [subtype, setSubtype] = useState(currentSubtype !== undefined ? currentSubtype : 1);
  const [imageProfileMode, setImageProfileMode] = useState<'auto' | 'day' | 'night'>(currentImageProfileMode || 'auto');
  const [nightStartTime, setNightStartTime] = useState(currentNightStartTime || '');
  const [nightEndTime, setNightEndTime] = useState(currentNightEndTime || '');
  const [clipMinDuration, setClipMinDuration] = useState<number>(currentClipMinDuration || 20);
  const [clipMaxDuration, setClipMaxDuration] = useState<number>(currentClipMaxDuration || 120);
  const [targetOverlay, setTargetOverlay] = useState<boolean>(currentTargetOverlay !== undefined ? currentTargetOverlay : true);

  const [profileSaving, setProfileSaving] = useState(false);
  const [profileSavedMsg, setProfileSavedMsg] = useState<string | null>(null);
  const [clipSaving, setClipSaving] = useState(false);
  const [clipSavedMsg, setClipSavedMsg] = useState<string | null>(null);
  const [overlaySaving, setOverlaySaving] = useState(false);
  const [overlaySavedMsg, setOverlaySavedMsg] = useState<string | null>(null);

  // Busca SEMPRE a configuração real gravada no banco ao entrar na tela (sem presets fictícios)
  useEffect(() => {
    if (!token) return;
    let isMounted = true;
    setLoadingConfig(true);
    fetch('/api/camera/config', {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        if (res.ok) return res.json();
        throw new Error('Falha ao carregar configuração');
      })
      .then((cfg) => {
        if (!isMounted) return;
        if (cfg.ip) setIp(cfg.ip);
        if (cfg.port !== undefined) setPort(cfg.port);
        if (cfg.http_port !== undefined) setHttpPort(cfg.http_port);
        if (cfg.user) setUser(cfg.user);
        if (cfg.subtype !== undefined) setSubtype(cfg.subtype);
        if (cfg.image_profile_mode) setImageProfileMode(cfg.image_profile_mode);
        if (cfg.night_start_time) setNightStartTime(cfg.night_start_time);
        if (cfg.night_end_time) setNightEndTime(cfg.night_end_time);
        if (cfg.clip_min_duration !== undefined) setClipMinDuration(cfg.clip_min_duration);
        if (cfg.clip_max_duration !== undefined) setClipMaxDuration(cfg.clip_max_duration);
        if (cfg.target_overlay !== undefined) setTargetOverlay(Boolean(cfg.target_overlay));
      })
      .catch(() => {
        // Fallback para props se offline
        if (!isMounted) return;
        if (currentIp) setIp(currentIp);
        if (currentNightStartTime) setNightStartTime(currentNightStartTime);
        if (currentNightEndTime) setNightEndTime(currentNightEndTime);
      })
      .finally(() => {
        if (isMounted) setLoadingConfig(false);
      });

    return () => {
      isMounted = false;
    };
  }, [token]);

  const applyImageProfileSchedule = async (newMode: 'auto' | 'day' | 'night', start: string, end: string) => {
    setImageProfileMode(newMode);
    setNightStartTime(start);
    setNightEndTime(end);
    if (!token) return;
    setProfileSaving(true);
    setProfileSavedMsg(null);
    try {
      const res = await fetch('/api/camera/image-profile', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          mode: newMode,
          start_time: start,
          end_time: end,
        }),
      });
      if (res.ok) {
        setProfileSavedMsg('✓ Perfil e Janela salvos com sucesso!');
        setTimeout(() => setProfileSavedMsg(null), 3000);
        onConfigSaved();
      }
    } catch {
      // offline
    } finally {
      setProfileSaving(false);
    }
  };

  const handleSubtypeChange = async (newSubtype: number) => {
    setSubtype(newSubtype);
    if (!token) return;
    try {
      await fetch('/api/camera/subtype', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ subtype: newSubtype }),
      });
      onConfigSaved();
    } catch {
      // offline
    }
  };

  const saveClipDuration = async (minD: number, maxD: number) => {
    setClipMinDuration(minD);
    setClipMaxDuration(maxD);
    if (!token) return;
    setClipSaving(true);
    setClipSavedMsg(null);
    try {
      const res = await fetch('/api/camera/clip-duration', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          clip_min_duration: minD,
          clip_max_duration: maxD,
        }),
      });
      if (res.ok) {
        setClipSavedMsg('✓ Duração de gravação salva com sucesso!');
        setTimeout(() => setClipSavedMsg(null), 3000);
        onConfigSaved();
      }
    } catch {
      // offline
    } finally {
      setClipSaving(false);
    }
  };

  const handleToggleTargetOverlay = async () => {
    const nextVal = !targetOverlay;
    setTargetOverlay(nextVal);
    if (!token) return;
    setOverlaySaving(true);
    setOverlaySavedMsg(null);
    try {
      const res = await fetch('/api/camera/target-overlay', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ target_overlay: nextVal }),
      });
      if (res.ok) {
        setOverlaySavedMsg(nextVal ? '✓ Alvo IVS ativado na câmera!' : '✓ Alvo IVS desativado!');
        setTimeout(() => setOverlaySavedMsg(null), 3000);
        onConfigSaved();
      }
    } catch {
      // offline
    } finally {
      setOverlaySaving(false);
    }
  };

  // Refs para scroll suave
  const formRef = useRef<HTMLDivElement>(null);
  const detectorRef = useRef<HTMLDivElement>(null);

  const [saving, setSaving] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanPerformed, setScanPerformed] = useState(false);
  const [subnetsInput, setSubnetsInput] = useState('192.168.1, 192.168.2');
  const [scannedSubnets, setScannedSubnets] = useState<string[]>([]);
  const [foundCameras, setFoundCameras] = useState<FoundDevice[]>([]);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);
  const [detectorMessage, setDetectorMessage] = useState<{ text: string; type: 'success' | 'error' | 'info' } | null>(null);

  if (loadingConfig) {
    return (
      <div className="w-full h-full p-4 sm:p-8 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3 p-8 rounded-2xl bg-[#080B10] border border-zinc-800 text-center font-mono max-w-md shadow-2xl">
          <RefreshCw className="w-8 h-8 animate-spin text-emerald-400" />
          <p className="text-sm font-semibold text-zinc-100">Carregando Configurações Salvas</p>
          <p className="text-xs text-zinc-400">
            Buscando os parâmetros reais gravados no banco de dados SQLite, sem carregar presets fictícios.
          </p>
        </div>
      </div>
    );
  }

  const presetSubnets = [
    { label: '192.168.1.0/24 (Principal)', value: '192.168.1' },
    { label: '192.168.2.0/24 (2º Modem / Roteador)', value: '192.168.2' },
    { label: '192.168.0.0/24 (Comum TP-Link/D-Link)', value: '192.168.0' },
    { label: '192.168.15.0/24 (Vivo Fibra)', value: '192.168.15' },
    { label: '10.0.0.0/24 (Rede / AP)', value: '10.0.0' },
  ];

  const togglePresetSubnet = (val: string) => {
    const parts = subnetsInput
      .split(/[,;\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);

    if (parts.includes(val)) {
      // Remove
      const remaining = parts.filter((s) => s !== val);
      setSubnetsInput(remaining.join(', '));
    } else {
      // Adiciona
      parts.push(val);
      setSubnetsInput(parts.join(', '));
    }
  };

  const isPresetActive = (val: string) => {
    const parts = subnetsInput
      .split(/[,;\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    return parts.includes(val);
  };

  const handleScan = async (overrideSubnets?: string) => {
    const target = overrideSubnets !== undefined ? overrideSubnets : subnetsInput;
    setScanning(true);
    setScanPerformed(true);
    setDetectorMessage(null);

    try {
      const params = new URLSearchParams();
      if (target.trim()) {
        params.set('subnets', target.trim());
      }
      const res = await fetch(`/api/camera/scan?${params.toString()}`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (res.ok) {
        const data = await res.json();
        const cams: FoundDevice[] = data.cameras || [];
        const subs: string[] = data.scanned_subnets || [];
        setFoundCameras(cams);
        setScannedSubnets(subs);

        if (cams.length === 0) {
          setDetectorMessage({
            type: 'info',
            text: `Nenhuma câmera respondendo nas portas RTSP/HTTP nas sub-redes: ${subs.join(', ')}.`,
          });
        } else {
          setDetectorMessage({
            type: 'success',
            text: `${cams.length} dispositivo(s) encontrado(s) nas sub-redes varridas! Clique em "Usar esta Câmera" para aplicar.`,
          });
        }
      } else {
        throw new Error('Falha na resposta do scanner.');
      }
    } catch {
      setDetectorMessage({
        type: 'error',
        text: 'Erro ao executar varredura de sub-redes. Verifique a conexão com o backend.',
      });
    } finally {
      setScanning(false);
    }
  };

  const handleSelectCamera = (cam: FoundDevice) => {
    setIp(cam.ip);
    if (cam.ports.includes(554)) setPort(554);
    if (cam.ports.includes(80)) setHttpPort(80);

    setMessage({
      text: `Câmera ${cam.ip} selecionada com sucesso! Verifique a senha da câmera acima e clique em "Salvar e Conectar".`,
      type: 'success',
    });

    formRef.current?.scrollIntoView({ behavior: 'smooth' });
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
          image_profile_mode: imageProfileMode,
          night_start_time: nightStartTime,
          night_end_time: nightEndTime,
          clip_min_duration: Number(clipMinDuration),
          clip_max_duration: Number(clipMaxDuration),
          target_overlay: targetOverlay,
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
    <div className="w-full h-full p-3.5 sm:p-6 lg:p-8 overflow-y-auto space-y-6 sm:space-y-8">
      {/* Cabeçalho expandido */}
      <div ref={formRef} className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4 border-b border-zinc-800/80 pb-4 sm:pb-5">
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

      {/* Formulário Principal de Configuração */}
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
                    Endereço IP da Câmera (Fixo ou Detectado)
                  </label>
                  <button
                    type="button"
                    onClick={() => {
                      detectorRef.current?.scrollIntoView({ behavior: 'smooth' });
                    }}
                    className="text-[11px] font-mono text-emerald-400 hover:text-emerald-300 flex items-center gap-1 cursor-pointer"
                  >
                    <Radar className="w-3 h-3" />
                    <span>Detectar na rede</span>
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
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-300 mb-1.5 font-mono">
                    Porta RTSP (Vídeo H.264)
                  </label>
                  <input
                    type="number"
                    value={port}
                    onChange={(e) => setPort(Number(e.target.value))}
                    placeholder="554"
                    required
                    className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg p-2.5 sm:p-3 text-sm text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none min-h-[42px]"
                  />
                  <p className="text-[10px] text-zinc-500 font-mono mt-1">Padrão Intelbras: 554</p>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-300 mb-1.5 font-mono">
                    Porta HTTP / Web / Snapshot
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
                      onChange={() => handleSubtypeChange(0)}
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
                      onChange={() => handleSubtypeChange(1)}
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

        {/* Card 3: Perfil de Imagem & Calibração de Luz (WDR / Noite / Dia) */}
        <div className="bg-[#080B10] border border-zinc-800/90 rounded-xl p-4 sm:p-6 space-y-4 sm:space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-zinc-800/80 pb-3 gap-2">
            <div>
              <span className="text-xs font-bold uppercase tracking-wider text-emerald-400 font-mono flex items-center gap-2">
                <Sun className="w-4 h-4 text-amber-400" /> 3. Perfil de Imagem & Horário Noturno (WDR / IR)
              </span>
              <p className="text-[11px] text-zinc-400 font-mono mt-0.5">
                Defina como a câmera comuta entre o modo colorido diurno e o infravermelho P&B com WDR
              </p>
            </div>
            <span className="text-[10px] font-mono text-zinc-500">ISP::DAY_NIGHT</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {/* Opção Auto */}
            <label
              className={`p-3.5 rounded-xl border flex flex-col justify-between cursor-pointer transition-all ${
                imageProfileMode === 'auto'
                  ? 'bg-emerald-950/40 border-emerald-500/60 text-emerald-200 shadow-[0_0_12px_rgba(16,185,129,0.15)]'
                  : 'bg-[#0E131C] border-zinc-800 text-zinc-400 hover:border-zinc-700'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold font-mono flex items-center gap-2">
                  <Clock className="w-4 h-4 text-cyan-400" /> Automático (Horário)
                </span>
                <input
                  type="radio"
                  name="imageProfileMode"
                  checked={imageProfileMode === 'auto'}
                  onChange={() => applyImageProfileSchedule('auto', nightStartTime, nightEndTime)}
                  className="accent-emerald-500"
                />
              </div>
              <p className="text-[11px] text-zinc-400 font-mono">
                Segue a janela noturna programada abaixo. Durante o dia fica colorido, de noite ativa Smart IR + WDR.
              </p>
            </label>

            {/* Opção Forçar Dia */}
            <label
              className={`p-3.5 rounded-xl border flex flex-col justify-between cursor-pointer transition-all ${
                imageProfileMode === 'day'
                  ? 'bg-amber-950/40 border-amber-500/60 text-amber-200 shadow-[0_0_12px_rgba(245,158,11,0.15)]'
                  : 'bg-[#0E131C] border-zinc-800 text-zinc-400 hover:border-zinc-700'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold font-mono flex items-center gap-2">
                  <Sun className="w-4 h-4 text-amber-400" /> Forçar Modo Dia
                </span>
                <input
                  type="radio"
                  name="imageProfileMode"
                  checked={imageProfileMode === 'day'}
                  onChange={() => applyImageProfileSchedule('day', nightStartTime, nightEndTime)}
                  className="accent-amber-500"
                />
              </div>
              <p className="text-[11px] text-zinc-400 font-mono">
                Mantém cores naturais e WDR 60 contínuos. Ideal se você deixa as luzes do local acesas à noite.
              </p>
            </label>

            {/* Opção Forçar Noite */}
            <label
              className={`p-3.5 rounded-xl border flex flex-col justify-between cursor-pointer transition-all ${
                imageProfileMode === 'night'
                  ? 'bg-indigo-950/40 border-indigo-500/60 text-indigo-200 shadow-[0_0_12px_rgba(99,102,241,0.15)]'
                  : 'bg-[#0E131C] border-zinc-800 text-zinc-400 hover:border-zinc-700'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold font-mono flex items-center gap-2">
                  <Moon className="w-4 h-4 text-indigo-400" /> Forçar Modo Noite
                </span>
                <input
                  type="radio"
                  name="imageProfileMode"
                  checked={imageProfileMode === 'night'}
                  onChange={() => applyImageProfileSchedule('night', nightStartTime, nightEndTime)}
                  className="accent-indigo-500"
                />
              </div>
              <p className="text-[11px] text-zinc-400 font-mono">
                Força Smart IR P&B e WDR 38 24h. Ideal para máxima visão noturna mesmo com luzes fracas.
              </p>
            </label>
          </div>

          {/* Configuração de Janela Noturna */}
          <div className="pt-2 border-t border-zinc-800/80 space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                <label className="text-xs font-semibold text-zinc-300 font-mono flex items-center gap-2">
                  <Clock className="w-3.5 h-3.5 text-emerald-400" />
                  Janela do Horário Noturno (quando o perfil estiver em Automático)
                </label>
                {profileSavedMsg && (
                  <span className="text-[11px] font-mono text-emerald-400 animate-fade-in bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/30">
                    {profileSavedMsg}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-[10px] text-zinc-500 font-mono">Presets:</span>
                {[
                  { label: '22h às 05h', start: '22:00', end: '05:00' },
                  { label: '23h às 06h', start: '23:00', end: '06:00' },
                  { label: '00h às 06h', start: '00:00', end: '06:00' },
                  { label: '19h às 05h', start: '19:00', end: '05:00' },
                ].map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => applyImageProfileSchedule(imageProfileMode, p.start, p.end)}
                    className={`px-2 py-0.5 rounded text-[10px] font-mono border transition-all cursor-pointer ${
                      nightStartTime === p.start && nightEndTime === p.end
                        ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/50'
                        : 'bg-zinc-800/70 text-zinc-400 border-zinc-700/60 hover:text-zinc-200'
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
              <div>
                <label className="block text-xs font-medium text-zinc-400 mb-1 font-mono">
                  Início da Noite (Ativa IR + WDR 38)
                </label>
                <input
                  type="time"
                  value={nightStartTime}
                  onChange={(e) => setNightStartTime(e.target.value)}
                  onBlur={() => applyImageProfileSchedule(imageProfileMode, nightStartTime, nightEndTime)}
                  className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg p-2.5 text-sm text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-zinc-400 mb-1 font-mono">
                  Fim da Noite (Restaura Colorido + WDR 60)
                </label>
                <div className="flex gap-2">
                  <input
                    type="time"
                    value={nightEndTime}
                    onChange={(e) => setNightEndTime(e.target.value)}
                    onBlur={() => applyImageProfileSchedule(imageProfileMode, nightStartTime, nightEndTime)}
                    className="flex-1 bg-[#0E131C] border border-zinc-700/60 rounded-lg p-2.5 text-sm text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none"
                  />
                  <button
                    type="button"
                    disabled={profileSaving}
                    onClick={() => applyImageProfileSchedule(imageProfileMode, nightStartTime, nightEndTime)}
                    className="px-3 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded-lg text-xs font-mono transition-colors cursor-pointer flex items-center gap-1.5 whitespace-nowrap"
                  >
                    {profileSaving ? 'Salvando...' : 'Salvar Horário'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Card 4: Gravação Dinâmica de Alarme & Flagrante (Post-Roll Buffer) */}
        <div className="bg-[#080B10] border border-zinc-800/90 rounded-xl p-4 sm:p-6 space-y-4 sm:space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-zinc-800/80 pb-3 gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-bold uppercase tracking-wider text-emerald-400 font-mono flex items-center gap-2">
                <Film className="w-4 h-4 text-emerald-400" /> 4. Gravação Dinâmica de Alarme (Post-Roll / Extensão por Movimento)
              </span>
              {clipSavedMsg && (
                <span className="text-[11px] font-mono text-emerald-400 animate-fade-in bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/30">
                  {clipSavedMsg}
                </span>
              )}
            </div>
            <span className="text-[10px] font-mono text-zinc-500">ALARM::DYNAMIC_CLIP</span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-semibold text-zinc-300 mb-1.5 font-mono">
                Duração Base Inicial (Segundos)
              </label>
              <input
                type="number"
                min={10}
                max={60}
                value={clipMinDuration}
                onChange={(e) => setClipMinDuration(Number(e.target.value))}
                onBlur={() => saveClipDuration(clipMinDuration, clipMaxDuration)}
                className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg p-2.5 sm:p-3 text-sm text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none min-h-[42px]"
              />
              <p className="text-[10px] text-zinc-500 font-mono mt-1">
                Padrão recomendado: 20s. Tempo mínimo garantido do início do flagrante.
              </p>
            </div>

            <div>
              <label className="block text-xs font-semibold text-zinc-300 mb-1.5 font-mono">
                Teto Máximo de Segurança (Segundos)
              </label>
              <input
                type="number"
                min={30}
                max={300}
                value={clipMaxDuration}
                onChange={(e) => setClipMaxDuration(Number(e.target.value))}
                onBlur={() => saveClipDuration(clipMinDuration, clipMaxDuration)}
                className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg p-2.5 sm:p-3 text-sm text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none min-h-[42px]"
              />
              <p className="text-[10px] text-zinc-500 font-mono mt-1">
                Padrão: 120s (2 min). Protege o upload do WhatsApp caso alguém fique parado no campo de visão.
              </p>
            </div>

            <div className="flex flex-col justify-end">
              <button
                type="button"
                disabled={clipSaving}
                onClick={() => saveClipDuration(clipMinDuration, clipMaxDuration)}
                className="w-full px-4 py-2.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded-lg text-xs font-mono transition-colors cursor-pointer flex items-center justify-center gap-1.5 min-h-[42px]"
              >
                {clipSaving ? 'Salvando...' : 'Salvar Duração'}
              </button>
            </div>
          </div>

          <div className="p-3 rounded-lg bg-[#0C1017] border border-zinc-800/80 text-[11px] font-mono text-zinc-400 flex items-start gap-2.5">
            <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            <span>
              <b className="text-zinc-200">Como funciona:</b> Ao detectar uma pessoa, a câmera aciona o flash e inicia a gravação de {clipMinDuration}s. A cada frame em que a pessoa continua visível, o sistema estende a gravação em +10s (e mantém a luz acesa) até o limite de {clipMaxDuration}s. Quando a pessoa sai, o vídeo é finalizado e enviado de uma só vez para o WhatsApp e Google Drive.
            </span>
          </div>
        </div>

        {/* Inteligência de Borda & Sobreposição Nativa (Intelbras IVS) */}
        <div className="pt-4 border-t border-zinc-800/80 space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2">
              <label className="text-xs font-bold uppercase tracking-wider text-emerald-400 font-mono flex items-center gap-2">
                <Target className="w-4 h-4 text-emerald-400" /> Sobreposição Nativa de Alvo (Target Overlay / IVS)
              </label>
              {overlaySavedMsg && (
                <span className="text-[11px] font-mono text-emerald-400 animate-fade-in bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/30">
                  {overlaySavedMsg}
                </span>
              )}
            </div>
            <span className="text-[10px] font-mono text-zinc-500 bg-zinc-900 border border-zinc-800 px-2 py-0.5 rounded">
              HARDWARE DA CÂMERA
            </span>
          </div>

          <div className="p-4 rounded-xl bg-[#0C1017] border border-zinc-800/90 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="space-y-1">
              <p className="text-xs font-semibold text-zinc-200 flex items-center gap-2">
                Ativar Marcação de Alvo Nativa (Intelbras ShowTrack / ShowRule)
              </p>
              <p className="text-[11px] text-zinc-400 leading-relaxed">
                Faz o chip da câmera carimbar os retângulos de detecção de pessoas e regras de IVS diretamente no fluxo de vídeo (0% de CPU no servidor). Ideal para os modos <b className="text-zinc-200">Híbrido</b> e <b className="text-zinc-200">100% Câmera</b>.
              </p>
            </div>

            <button
              type="button"
              disabled={overlaySaving}
              onClick={handleToggleTargetOverlay}
              className={`w-12 h-6 flex items-center rounded-full p-1 cursor-pointer transition-colors shrink-0 disabled:opacity-50 ${
                targetOverlay ? 'bg-emerald-600 justify-end shadow-[0_0_10px_rgba(16,185,129,0.3)]' : 'bg-zinc-700 justify-start'
              }`}
            >
              <div className="w-4 h-4 bg-white rounded-full shadow-md" />
            </button>
          </div>
        </div>

        {/* Barra de Ação do Formulário */}
        <div className="pt-2 border-t border-zinc-800/80 flex flex-col sm:flex-row items-center justify-between gap-3">
          <div className="text-xs font-mono text-zinc-500 hidden sm:block">
            Alterações são salvas de forma segura no SQLite com criptografia AES-256
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

      {/* SEÇÃO DEDICADA: DETECTOR AUTOMÁTICO DE CÂMERAS & SCANNER DE SUB-REDES */}
      <div
        ref={detectorRef}
        className="bg-[#080B10] border border-zinc-800/90 rounded-xl p-4 sm:p-6 lg:p-7 space-y-5 sm:space-y-6 relative overflow-hidden"
      >
        {/* Glow sutil de fundo do radar */}
        <div className="absolute top-0 right-0 w-96 h-96 bg-emerald-500/5 rounded-full blur-3xl pointer-events-none -mr-20 -mt-20" />

        {/* Cabeçalho da Seção */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-zinc-800/80 pb-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.2)] shrink-0">
              <Radar className={`w-5 h-5 ${scanning ? 'animate-spin text-emerald-300' : ''}`} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base sm:text-lg font-bold font-['Chakra_Petch'] text-zinc-100 uppercase tracking-wide">
                  Detector Automático de Câmeras na Rede
                </h3>
                <span className="text-[9px] sm:text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 uppercase">
                  Scanner Multi-Sub-rede
                </span>
              </div>
              <p className="text-xs text-zinc-400 font-mono">
                Varredura paralela no modem principal (192.168.1.x), 2º modem/roteador (192.168.2.x) ou sub-redes personalizadas
              </p>
            </div>
          </div>

          <div className="text-[10px] font-mono text-zinc-500 hidden md:block text-right">
            <span>PORTAS: 554 (RTSP) · 80 (HTTP) · 37777 (DAHUA) · 8000 (HIK)</span>
          </div>
        </div>

        {/* Configuração de Sub-redes para Varredura */}
        <div className="space-y-3 sm:space-y-4">
          <div>
            <label className="block text-xs font-semibold text-zinc-300 font-mono mb-2 flex items-center gap-2">
              <Network className="w-3.5 h-3.5 text-emerald-400" />
              Sub-redes para Varredura (Selecione os atalhos ou digite os IPs dos modens)
            </label>

            {/* Presets Rápidos */}
            <div className="flex flex-wrap gap-2 mb-3">
              {presetSubnets.map((p) => {
                const active = isPresetActive(p.value);
                return (
                  <button
                    key={p.value}
                    type="button"
                    onClick={() => togglePresetSubnet(p.value)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-mono transition-all border flex items-center gap-1.5 cursor-pointer ${
                      active
                        ? 'bg-emerald-500/20 border-emerald-500/60 text-emerald-300 shadow-[0_0_10px_rgba(16,185,129,0.15)] font-semibold'
                        : 'bg-[#0E131C] border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200'
                    }`}
                  >
                    <span
                      className={`w-1.5 h-1.5 rounded-full ${
                        active ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-600'
                      }`}
                    />
                    <span>{p.label}</span>
                  </button>
                );
              })}
            </div>

            {/* Campo de Entrada de Sub-redes Customizadas + Botão de Ação */}
            <div className="flex flex-col sm:flex-row gap-2.5">
              <div className="relative flex-1">
                <input
                  type="text"
                  value={subnetsInput}
                  onChange={(e) => setSubnetsInput(e.target.value)}
                  placeholder="Ex: 192.168.1.1, 192.168.2.1 ou 192.168.1, 192.168.2"
                  className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg px-3 py-2.5 text-xs text-zinc-100 font-mono focus:border-emerald-500 focus:outline-none min-h-[42px]"
                />
                <span className="absolute right-3 top-3 text-[10px] font-mono text-zinc-500 hidden sm:block">
                  separado por vírgula
                </span>
              </div>

              <button
                type="button"
                onClick={() => handleScan()}
                disabled={scanning || !subnetsInput.trim()}
                className="flex items-center justify-center gap-2 px-6 py-2.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-black font-bold font-mono text-xs uppercase tracking-wider transition-all disabled:opacity-50 cursor-pointer shadow-lg shadow-emerald-500/20 shrink-0 min-h-[42px]"
              >
                {scanning ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Varrendo Redes...</span>
                  </>
                ) : (
                  <>
                    <Radar className="w-3.5 h-3.5" />
                    <span>Escanear Sub-redes</span>
                  </>
                )}
              </button>
            </div>
            <p className="text-[10px] text-zinc-500 font-mono mt-1.5 flex items-center gap-1.5">
              <span>💡 Dica: Você pode digitar o IP de cada modem (ex: 192.168.1.1, 192.168.2.1) que o detector varrerá todos os 254 endereços de cada um em paralelo (~2 a 3 segundos).</span>
            </p>
          </div>
        </div>

        {/* Mensagem de Feedback do Detector */}
        {detectorMessage && (
          <div
            className={`p-3.5 rounded-xl border text-xs flex items-center gap-3 font-mono ${
              detectorMessage.type === 'success'
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                : detectorMessage.type === 'error'
                ? 'bg-red-500/10 border-red-500/30 text-red-300'
                : 'bg-zinc-800/40 border-zinc-700/50 text-zinc-300'
            }`}
          >
            {detectorMessage.type === 'success' ? (
              <CheckCircle className="w-4 h-4 shrink-0 text-emerald-400" />
            ) : detectorMessage.type === 'error' ? (
              <AlertCircle className="w-4 h-4 shrink-0 text-red-400" />
            ) : (
              <HelpCircle className="w-4 h-4 shrink-0 text-amber-400" />
            )}
            <span>{detectorMessage.text}</span>
          </div>
        )}

        {/* Animação de Varredura em Andamento */}
        {scanning && (
          <div className="p-6 rounded-xl bg-[#0C1017] border border-emerald-500/30 flex flex-col items-center justify-center space-y-3 text-center">
            <div className="relative">
              <Radar className="w-10 h-10 text-emerald-400 animate-spin" />
              <div className="absolute inset-0 rounded-full bg-emerald-500/20 animate-ping pointer-events-none" />
            </div>
            <div>
              <p className="text-sm font-bold font-mono text-emerald-400 uppercase tracking-wide">
                Varrendo Sub-redes em Paralelo...
              </p>
              <p className="text-xs text-zinc-400 font-mono mt-1">
                Testando portas RTSP (554), HTTP (80) e Dahua/Intelbras (37777) em {subnetsInput}
              </p>
            </div>
          </div>
        )}

        {/* Lista de Resultados de Câmeras e Dispositivos Encontrados */}
        {!scanning && scanPerformed && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-zinc-800/80 pb-2">
              <span className="text-xs font-bold uppercase tracking-wider text-zinc-300 font-mono flex items-center gap-2">
                <Camera className="w-4 h-4 text-emerald-400" /> Dispositivos Detectados ({foundCameras.length})
              </span>
              {scannedSubnets.length > 0 && (
                <span className="text-[10px] font-mono text-zinc-500">
                  Sub-redes: {scannedSubnets.join(', ')}
                </span>
              )}
            </div>

            {foundCameras.length === 0 ? (
              <div className="p-5 rounded-xl bg-[#0C1017] border border-zinc-800/80 space-y-3 text-xs font-mono text-zinc-400">
                <div className="flex items-start gap-2.5 text-zinc-300">
                  <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <div>
                    <p className="font-bold text-zinc-200">Nenhuma câmera detectada nas sub-redes varridas.</p>
                    <p className="text-zinc-400 mt-1">
                      Possíveis causas e como resolver:
                    </p>
                  </div>
                </div>
                <ul className="list-disc list-inside space-y-1.5 pl-2 text-[11px] text-zinc-400">
                  <li>
                    <span className="text-zinc-200 font-semibold">2º Modem em rede isolada:</span> Se o segundo modem estiver com roteamento próprio (NAT duplo), certifique-se de que a opção <span className="text-emerald-400">"Isolamento de AP"</span> (AP Isolation) está desativada nele.
                  </li>
                  <li>
                    <span className="text-zinc-200 font-semibold">Faixa de IP diferente:</span> Verifique se o IP do segundo modem não é por exemplo <span className="text-emerald-400">192.168.15.1</span> (Vivo Fibra) ou <span className="text-emerald-400">192.168.0.1</span> e adicione-o na lista acima.
                  </li>
                  <li>
                    <span className="text-zinc-200 font-semibold">Câmera recém-ligada:</span> A Intelbras leva cerca de 45 segundos para inicializar os serviços RTSP após ser ligada na energia.
                  </li>
                </ul>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {foundCameras.map((cam) => {
                  const isCamera = cam.is_camera !== false;
                  return (
                    <div
                      key={cam.ip}
                      className={`p-3.5 rounded-xl border flex flex-col justify-between gap-3 transition-all ${
                        isCamera
                          ? 'bg-[#0C111A] border-emerald-500/40 hover:border-emerald-500/70 shadow-[0_0_15px_rgba(16,185,129,0.05)]'
                          : 'bg-[#0E131C] border-zinc-800/90 hover:border-zinc-700'
                      }`}
                    >
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between gap-2">
                          <span
                            className={`text-[10px] font-mono px-2 py-0.5 rounded font-bold uppercase tracking-wider flex items-center gap-1.5 ${
                              isCamera
                                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                                : 'bg-zinc-800 text-zinc-400 border border-zinc-700'
                            }`}
                          >
                            <Camera className="w-3 h-3" />
                            {cam.name}
                          </span>
                          {cam.subnet && (
                            <span className="text-[10px] font-mono text-zinc-500">
                              {cam.subnet}
                            </span>
                          )}
                        </div>

                        <div className="flex items-baseline gap-2">
                          <span className="text-base font-bold font-mono text-zinc-100 tracking-wide">
                            {cam.ip}
                          </span>
                        </div>

                        <div className="flex flex-wrap gap-1.5 pt-1">
                          {cam.ports.map((p) => {
                            let label = `Porta ${p}`;
                            if (p === 554) label = '554 (RTSP)';
                            if (p === 80) label = '80 (HTTP)';
                            if (p === 37777) label = '37777 (Intelbras/Dahua)';
                            if (p === 8000) label = '8000 (Hikvision)';
                            if (p === 8899) label = '8899 (ONVIF)';
                            return (
                              <span
                                key={p}
                                className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-300"
                              >
                                {label}
                              </span>
                            );
                          })}
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => handleSelectCamera(cam)}
                        className={`w-full py-2 px-3 rounded-lg text-xs font-mono font-bold uppercase tracking-wider transition-all flex items-center justify-center gap-1.5 cursor-pointer ${
                          isCamera
                            ? 'bg-emerald-500 hover:bg-emerald-400 text-black shadow-md shadow-emerald-500/20'
                            : 'bg-zinc-800 hover:bg-zinc-700 text-zinc-200'
                        }`}
                      >
                        <ArrowUpRight className="w-3.5 h-3.5" />
                        <span>Usar esta Câmera</span>
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

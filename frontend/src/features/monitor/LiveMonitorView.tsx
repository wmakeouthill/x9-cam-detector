import { useState, useRef, useEffect, useCallback } from 'react';
import {
  Zap,
  Camera,
  Maximize2,
  Minimize2,
  RefreshCw,
  AlertCircle,
  CheckCircle,
  ShieldAlert,
  Users,
  Volume2,
  VolumeX,
  Volume1,
  Film,
  Sliders,
} from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';

interface LiveMonitorViewProps {
  status: string;
  fps: number;
  resolution: string;
  cameraIp: string;
  currentSubtype?: number;
  detectorActive?: boolean;
  personDetected?: boolean;
  peopleCount?: number;
  lastConfidence?: number;
  detectionsCount?: number;
  testMode?: boolean;
  onRefreshStatus: () => void;
}

export function LiveMonitorView({
  status,
  fps,
  resolution,
  cameraIp,
  currentSubtype = 1,
  detectorActive,
  personDetected,
  peopleCount,
  lastConfidence,
  detectionsCount,
  testMode,
  onRefreshStatus,
}: LiveMonitorViewProps) {
  const { token } = useAuthStore();
  const [streamKey, setStreamKey] = useState(Date.now());
  const [flashLoading, setFlashLoading] = useState(false);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [recordLoading, setRecordLoading] = useState(false);
  const [subtypeLoading, setSubtypeLoading] = useState(false);
  const [lastSnapshotUrl, setLastSnapshotUrl] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  // Estados de Controle de Áudio da Câmera
  const [audioEnabled, setAudioEnabled] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [volume, setVolume] = useState(80);
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [audioConnecting, setAudioConnecting] = useState(false);

  // Controles de Tela Cheia e Hover / Overlay
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(false);
  const controlsTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const gainNodeRef = useRef<GainNode | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Listener para sincronizar estado de Fullscreen nativo
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(Boolean(document.fullscreenElement));
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
      document.removeEventListener('webkitfullscreenchange', handleFullscreenChange);
    };
  }, []);

  // Mostra controles no hover ou toque e programa fade out após 4 segundos
  const triggerControls = useCallback(() => {
    setControlsVisible(true);
    if (controlsTimeoutRef.current) {
      clearTimeout(controlsTimeoutRef.current);
    }
    controlsTimeoutRef.current = setTimeout(() => {
      setControlsVisible(false);
    }, 4000);
  }, []);

  const handleRefreshStream = () => {
    setStreamKey(Date.now());
    onRefreshStatus();
  };

  const handleChangeSubtype = async (newSubtype: number) => {
    if (subtypeLoading || currentSubtype === newSubtype) return;
    setSubtypeLoading(true);
    setActionMessage(null);
    try {
      const res = await fetch('/api/camera/subtype', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ subtype: newSubtype }),
      });
      const data = await res.json();
      if (res.ok) {
        setActionMessage({
          text: `Resolução alterada para ${data.name || (newSubtype === 0 ? '1080p FHD' : '480p Substream')}! Reconectando stream...`,
          type: 'success',
        });
        setTimeout(() => {
          setStreamKey(Date.now());
          onRefreshStatus();
        }, 1200);
      } else {
        throw new Error(data.detail || 'Falha ao alterar resolução.');
      }
    } catch (err: any) {
      setActionMessage({ text: err.message || 'Erro ao alterar resolução da câmera.', type: 'error' });
    } finally {
      setSubtypeLoading(false);
    }
  };

  const handleTriggerFlash = async () => {
    setFlashLoading(true);
    setActionMessage(null);
    try {
      const res = await fetch('/api/camera/flash', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ seconds: 2.0 }),
      });
      if (res.ok) {
        setActionMessage({ text: 'Flash de luz branca acionado com sucesso (2s)!', type: 'success' });
      } else {
        throw new Error('Falha ao acionar flash na câmera.');
      }
    } catch (err: any) {
      setActionMessage({ text: err.message || 'Erro ao disparar flash.', type: 'error' });
    } finally {
      setFlashLoading(false);
    }
  };

  const handleTakeSnapshot = async (withFlash: boolean = false) => {
    setSnapshotLoading(true);
    setActionMessage(null);
    try {
      const res = await fetch('/api/camera/snapshot', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ with_flash: withFlash }),
      });
      const data = await res.json();
      if (res.ok && data.url) {
        setLastSnapshotUrl(`${data.url}?token=${token}`);
        if (withFlash) {
          setActionMessage({ text: 'Disparo duplo concluído (P&B instantâneo + Colorida com Flash)!', type: 'success' });
        } else {
          setActionMessage({ text: `Foto salva: ${data.filename}`, type: 'success' });
        }
        onRefreshStatus();
      } else {
        throw new Error(data.detail || 'Falha ao capturar foto.');
      }
    } catch (err: any) {
      setActionMessage({ text: err.message || 'Erro ao capturar snapshot.', type: 'error' });
    } finally {
      setSnapshotLoading(false);
    }
  };

  const handleRecordVideo = async () => {
    setRecordLoading(true);
    setActionMessage(null);
    try {
      const res = await fetch('/api/camera/record', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ seconds: 10 }),
      });
      const data = await res.json();
      if (res.ok) {
        setActionMessage({ text: 'Gravação iniciada! Vídeo de 10s será salvo e enviado ao Google Drive.', type: 'success' });
        setTimeout(() => {
          setRecordLoading(false);
          onRefreshStatus();
        }, 11000);
      } else {
        throw new Error(data.detail || 'Falha ao iniciar gravação.');
      }
    } catch (err: any) {
      setActionMessage({ text: err.message || 'Erro ao gravar clipe de vídeo.', type: 'error' });
      setRecordLoading(false);
    }
  };

  const handleToggleTestMode = async () => {
    try {
      await fetch('/api/camera/test-mode', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ enabled: !testMode }),
      });
      onRefreshStatus();
    } catch {
      // erro
    }
  };

  const handleToggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  };

  // Limpeza de conexões e contexto de áudio ao desmontar o componente
  useEffect(() => {
    return () => {
      stopLiveAudio();
      if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
        audioContextRef.current.close().catch(() => {});
      }
    };
  }, []);

  // Inicia streaming de áudio PCM de latência ultrabaixa (<50ms) via Web Audio API
  const startLiveAudio = async () => {
    try {
      setAudioConnecting(true);

      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      if (!audioContextRef.current) {
        audioContextRef.current = new AudioContextClass({ sampleRate: 16000 });
      }
      const ctx = audioContextRef.current;
      if (ctx.state === 'suspended') {
        await ctx.resume();
      }

      if (!gainNodeRef.current) {
        const gain = ctx.createGain();
        gain.connect(ctx.destination);
        gainNodeRef.current = gain;
      }
      gainNodeRef.current.gain.value = isMuted ? 0 : volume / 100;

      abortControllerRef.current?.abort();
      const controller = new AbortController();
      abortControllerRef.current = controller;

      const res = await fetch(`/api/stream/audio-raw?token=${token}&_t=${Date.now()}`, {
        signal: controller.signal,
      });

      if (!res.ok) {
        throw new Error(`Falha no streaming de áudio (HTTP ${res.status})`);
      }
      if (!res.body) {
        throw new Error('Corpo de áudio indisponível.');
      }

      setAudioEnabled(true);
      setAudioConnecting(false);
      setAudioPlaying(true);

      const reader = res.body.getReader();
      let nextPlayTime = ctx.currentTime;
      let remainder: Uint8Array | null = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (!value || value.length === 0) continue;

        let data = value;
        if (remainder && remainder.length > 0) {
          const combined = new Uint8Array(remainder.length + value.length);
          combined.set(remainder);
          combined.set(value, remainder.length);
          data = combined;
          remainder = null;
        }

        if (data.length % 2 !== 0) {
          remainder = data.slice(data.length - 1);
          data = data.slice(0, data.length - 1);
        }

        if (data.length === 0) continue;

        const dataView = new DataView(data.buffer, data.byteOffset, data.byteLength);
        const sampleCount = data.byteLength / 2;
        const float32 = new Float32Array(sampleCount);
        for (let i = 0; i < sampleCount; i++) {
          float32[i] = dataView.getInt16(i * 2, true) / 32768.0;
        }

        const audioBuf = ctx.createBuffer(1, float32.length, 16000);
        audioBuf.getChannelData(0).set(float32);

        const source = ctx.createBufferSource();
        source.buffer = audioBuf;
        if (gainNodeRef.current) {
          source.connect(gainNodeRef.current);
        }

        const now = ctx.currentTime;
        // Se a reprodução estiver no passado ou houver acúmulo de buffer (> 50ms à frente):
        if (nextPlayTime < now || nextPlayTime > now + 0.05) {
          nextPlayTime = now + 0.01; // alinha na ponta do ao vivo (<20ms)
        }
        source.start(nextPlayTime);
        nextPlayTime += audioBuf.duration;
      }
    } catch (err: any) {
      if (err.name === 'AbortError') return;
      console.warn('[Web Audio Live] Conexão finalizada:', err);
      setAudioPlaying(false);
      setAudioConnecting(false);
      setAudioEnabled(false);
    }
  };

  const stopLiveAudio = () => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.suspend().catch(() => {});
    }
    setAudioEnabled(false);
    setAudioPlaying(false);
    setAudioConnecting(false);
  };

  // Alterna ativação e mudo do áudio ao vivo
  const toggleAudio = () => {
    if (!audioEnabled) {
      setIsMuted(false);
      startLiveAudio();
    } else if (!isMuted) {
      setIsMuted(true);
      if (gainNodeRef.current) {
        gainNodeRef.current.gain.value = 0;
      }
    } else {
      // Se já estava mudo e clicou novamente, encerra o stream
      stopLiveAudio();
    }
  };

  // Altera o volume dinamicamente (0-100)
  const handleVolumeChange = (newVol: number) => {
    setVolume(newVol);
    if (newVol > 0 && isMuted) {
      setIsMuted(false);
    }
    if (gainNodeRef.current) {
      gainNodeRef.current.gain.value = (newVol === 0 || isMuted) ? 0 : newVol / 100;
    }
    if (!audioEnabled && newVol > 0) {
      startLiveAudio();
    }
  };

  const isOnline = status === 'ONLINE';

  return (
    <div className="flex-1 flex flex-col p-3 sm:p-6 space-y-3 sm:space-y-4 overflow-y-auto">

      {/* Barra de Notificação Rápida */}
      {actionMessage && (
        <div
          className={`p-2.5 sm:p-3 rounded-lg border text-xs flex items-center justify-between transition-all ${
            actionMessage.type === 'success'
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
              : 'bg-red-500/10 border-red-500/30 text-red-300'
          }`}
        >
          <div className="flex items-center gap-2">
            {actionMessage.type === 'success' ? (
              <CheckCircle className="w-4 h-4 shrink-0 text-emerald-400" />
            ) : (
              <AlertCircle className="w-4 h-4 shrink-0 text-red-400" />
            )}
            <span>{actionMessage.text}</span>
          </div>
          <button
            onClick={() => setActionMessage(null)}
            className="text-slate-400 hover:text-slate-200 cursor-pointer"
          >
            ✕
          </button>
        </div>
      )}

      {/* Container Principal do Monitor CCTV com Overlay de Controles */}
      <div
        ref={containerRef}
        onMouseMove={triggerControls}
        onMouseEnter={triggerControls}
        onTouchStart={triggerControls}
        onMouseLeave={() => setControlsVisible(false)}
        className="relative bg-[#05070A] border border-cyan-500/30 rounded-xl overflow-hidden shadow-[0_0_30px_rgba(0,0,0,0.8)] aspect-video flex items-center justify-center max-h-[55vh] sm:max-h-[70vh] group select-none"
      >
        {/* Stream de Vídeo com Auth Token */}
        <img
          key={streamKey}
          src={`/api/stream/live?token=${token}&_t=${streamKey}`}
          alt="Transmissão ao vivo da câmera Intelbras"
          className="w-full h-full object-contain pointer-events-none"
        />

        {/* Efeito scanline CCTV sutil */}
        <div className="absolute inset-0 cctv-scanline opacity-40 pointer-events-none" />

        {/* HUD Superior Esquerdo: Badge LIVE e Câmera */}
        <div className="absolute top-2 left-2 sm:top-4 sm:left-4 flex items-center gap-1.5 sm:gap-2 pointer-events-none flex-wrap max-w-[70%] sm:max-w-none z-10">
          <div className="flex items-center gap-1.5 sm:gap-2 px-2 py-0.5 sm:px-2.5 sm:py-1 rounded bg-black/70 backdrop-blur-md border border-red-500/40 text-red-400">
            <span className="w-2 h-2 sm:w-2.5 sm:h-2.5 rounded-full bg-red-500 animate-live shadow-[0_0_8px_#ef4444]" />
            <span className="text-[10px] sm:text-xs font-mono font-bold tracking-wider">AO VIVO</span>
          </div>

          <div className="hidden sm:inline-block px-2 py-0.5 sm:px-2.5 sm:py-1 rounded bg-black/70 backdrop-blur-md border border-slate-700/60 text-slate-300 text-[10px] sm:text-xs font-mono">
            INTELBRAS VIP 1230 FC+ • {cameraIp}
          </div>

          {/* Contador de Pessoas na Cena em Tempo Real */}
          {personDetected && (peopleCount || 0) > 0 ? (
            <div className="flex items-center gap-1 sm:gap-1.5 px-2 py-0.5 sm:px-2.5 sm:py-1 rounded bg-amber-500/95 backdrop-blur-md border border-amber-300 text-black text-[10px] sm:text-xs font-mono font-bold shadow-[0_0_15px_rgba(245,158,11,0.6)] animate-pulse">
              <Users className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-black" />
              <span>
                {peopleCount} {peopleCount === 1 ? 'PESSOA' : 'PESSOAS'} NA CENA ({lastConfidence}%)
              </span>
            </div>
          ) : (
            <div className="hidden sm:flex items-center gap-1 sm:gap-1.5 px-2 py-0.5 sm:px-2 sm:py-1 rounded bg-black/60 backdrop-blur-md border border-emerald-500/30 text-emerald-400 text-[10px] sm:text-xs font-mono">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              <span>CENA LIVRE</span>
            </div>
          )}

          {detectorActive && (
            <div className="flex items-center gap-1 sm:gap-1.5 px-2 py-0.5 sm:px-2.5 sm:py-1 rounded bg-cyan-950/80 backdrop-blur-md border border-cyan-500/50 text-cyan-300 text-[10px] sm:text-xs font-mono font-semibold shadow-[0_0_10px_rgba(6,182,212,0.2)]">
              <ShieldAlert className="w-3.5 h-3.5 text-cyan-400 animate-pulse" />
              <span>VIGIA: {detectionsCount || 0} ALARMES</span>
            </div>
          )}
        </div>

        {/* HUD Superior Direito: Seletor de Resolução & FPS */}
        <div className="absolute top-2 right-2 sm:top-4 sm:right-4 flex items-center gap-1 sm:gap-2 z-10">
          {/* Seletor Rápido de Resolução / Subtype */}
          <div className="flex items-center bg-black/80 backdrop-blur-md border border-slate-700/80 rounded-lg p-0.5 shadow-lg shadow-black/40">
            <button
              onClick={() => handleChangeSubtype(1)}
              disabled={subtypeLoading}
              className={`px-2 py-0.5 sm:px-2.5 sm:py-1 rounded text-[10px] sm:text-xs font-mono font-bold transition-all cursor-pointer ${
                currentSubtype === 1
                  ? 'bg-emerald-500/30 text-emerald-300 border border-emerald-500/50 shadow-[0_0_8px_rgba(16,185,129,0.3)]'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
              title="Substream (480p / D1) - Leve para CPU e rede"
            >
              480p
            </button>
            <button
              onClick={() => handleChangeSubtype(0)}
              disabled={subtypeLoading}
              className={`px-2 py-0.5 sm:px-2.5 sm:py-1 rounded text-[10px] sm:text-xs font-mono font-bold transition-all cursor-pointer ${
                currentSubtype === 0
                  ? 'bg-cyan-500/30 text-cyan-300 border border-cyan-500/50 shadow-[0_0_8px_rgba(6,182,212,0.3)]'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
              title="Stream Principal (1080p FHD) - Resolução nativa máxima"
            >
              1080p
            </button>
          </div>

          <div className="hidden sm:inline-block px-2 py-0.5 sm:px-2.5 sm:py-1 rounded bg-black/70 backdrop-blur-md border border-slate-700/60 text-slate-300 text-[10px] sm:text-xs font-mono pointer-events-none">
            {resolution}
          </div>
          <div className="px-2 py-0.5 sm:px-2.5 sm:py-1 rounded bg-black/70 backdrop-blur-md border border-cyan-500/30 text-cyan-400 text-[10px] sm:text-xs font-mono pointer-events-none">
            {fps} FPS
          </div>
        </div>

        {/* BARRA DE CONTROLE SOBREPOSTA NO VÍDEO (Hover no Desktop / Toque no Mobile) */}
        <div
          className={`absolute bottom-0 left-0 right-0 p-2.5 sm:p-4 bg-gradient-to-t from-black/95 via-black/75 to-transparent flex items-center justify-between gap-3 transition-opacity duration-300 z-20 ${
            controlsVisible ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
          }`}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Lado Esquerdo: Botão de Mudo/Ativar + Slider de Volume */}
          <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
            <button
              onClick={toggleAudio}
              className={`px-3 py-2 rounded-lg border flex items-center gap-2 font-mono text-xs transition-all cursor-pointer ${
                audioEnabled && !isMuted && volume > 0
                  ? 'bg-emerald-950/80 border-emerald-500/60 text-emerald-300 shadow-[0_0_15px_rgba(16,185,129,0.3)]'
                  : 'bg-black/80 border-zinc-700/80 text-zinc-300 hover:text-white hover:border-zinc-500'
              }`}
              title={!audioEnabled ? 'Ativar microfone da câmera' : isMuted ? 'Desmutar' : 'Silenciar'}
            >
              {!audioEnabled || isMuted || volume === 0 ? (
                <VolumeX className="w-4 h-4 text-amber-400 shrink-0" />
              ) : (
                <Volume2 className="w-4 h-4 text-emerald-400 shrink-0 animate-pulse" />
              )}
              <span className="font-semibold">
                {!audioEnabled
                  ? 'Ativar Áudio'
                  : audioConnecting
                  ? 'Conectando...'
                  : isMuted || volume === 0
                  ? 'Mudo'
                  : 'Ouvindo'}
              </span>
            </button>

            {/* Slider Deslizante de Aumento / Redução de Volume */}
            <div className="flex items-center gap-2 bg-black/80 border border-zinc-800/90 px-3 py-1.5 rounded-lg backdrop-blur-md">
              <Volume1 className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
              <input
                type="range"
                min="0"
                max="100"
                step="1"
                value={isMuted ? 0 : volume}
                onChange={(e) => handleVolumeChange(Number(e.target.value))}
                className="w-16 sm:w-28 accent-emerald-500 cursor-pointer h-1.5"
                title="Ajuste de volume do áudio ambiente"
              />
              <span className="text-[10px] sm:text-[11px] font-mono text-zinc-300 w-8 text-right font-semibold">
                {isMuted ? '0%' : `${volume}%`}
              </span>
            </div>

            {/* Badge de Status do Microfone da Intelbras */}
            {audioEnabled && !isMuted && audioPlaying && (
              <div className="hidden md:flex items-center gap-1.5 text-[9px] font-mono px-2 py-1 rounded bg-emerald-950/80 text-emerald-400 border border-emerald-500/40 font-semibold shadow-[0_0_10px_rgba(16,185,129,0.2)]">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
                <span>MIC AMBIENTE VIP 1230 ON</span>
              </div>
            )}
          </div>

          {/* Lado Direito: Botão de Tela Cheia Sobreposto */}
          <div className="flex items-center gap-2">
            <button
              onClick={handleToggleFullscreen}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-black/80 hover:bg-zinc-800 border border-zinc-700/80 hover:border-cyan-500/60 text-zinc-200 hover:text-cyan-300 text-xs font-mono transition-all cursor-pointer shadow-lg shadow-black/50"
              title={isFullscreen ? 'Sair da Tela Cheia' : 'Tela Cheia'}
            >
              {isFullscreen ? (
                <>
                  <Minimize2 className="w-4 h-4 text-cyan-400" />
                  <span className="hidden sm:inline">Restaurar</span>
                </>
              ) : (
                <>
                  <Maximize2 className="w-4 h-4 text-cyan-400" />
                  <span className="hidden sm:inline">Tela Cheia</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Barra de Ações Rápidas da Câmera */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2 sm:gap-3">
        {/* Alternar Resolução 480p / 1080p */}
        <button
          onClick={() => handleChangeSubtype(currentSubtype === 0 ? 1 : 0)}
          disabled={subtypeLoading || !isOnline}
          className="flex items-center justify-center gap-1.5 sm:gap-2 px-3 py-2.5 sm:px-4 sm:py-3 rounded-lg bg-[#141A26] border border-cyan-500/30 hover:border-cyan-400 hover:bg-cyan-950/40 text-cyan-300 font-semibold text-[11px] sm:text-xs uppercase tracking-wider transition-all disabled:opacity-40 cursor-pointer min-h-[44px]"
          title="Alterna entre 480p Substream (econômico) e 1080p FHD (alta definição)"
        >
          <Sliders className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-cyan-400 shrink-0" />
          <span className="truncate">{subtypeLoading ? 'Trocando...' : currentSubtype === 0 ? 'Modo: 1080p' : 'Modo: 480p'}</span>
        </button>

        {/* Disparar Flash */}
        <button
          onClick={handleTriggerFlash}
          disabled={flashLoading || !isOnline}
          className="flex items-center justify-center gap-1.5 sm:gap-2 px-3 py-2.5 sm:px-4 sm:py-3 rounded-lg bg-gradient-to-r from-amber-600/20 to-yellow-600/20 border border-amber-500/40 hover:border-amber-400 hover:bg-amber-600/30 text-amber-300 font-semibold text-[11px] sm:text-xs uppercase tracking-wider transition-all disabled:opacity-40 cursor-pointer shadow-[0_0_15px_rgba(245,158,11,0.1)] min-h-[44px]"
        >
          <Zap className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-amber-400 shrink-0" />
          <span className="truncate">{flashLoading ? 'Acendendo...' : 'Flash 2s'}</span>
        </button>

        {/* Capturar Snapshot Normal */}
        <button
          onClick={() => handleTakeSnapshot(false)}
          disabled={snapshotLoading || !isOnline}
          className="flex items-center justify-center gap-1.5 sm:gap-2 px-3 py-2.5 sm:px-4 sm:py-3 rounded-lg bg-[#141A26] border border-cyan-500/30 hover:border-cyan-400 hover:bg-cyan-950/40 text-cyan-300 font-semibold text-[11px] sm:text-xs uppercase tracking-wider transition-all disabled:opacity-40 cursor-pointer min-h-[44px]"
          title="Captura foto instantânea exatamente como está na tela"
        >
          <Camera className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-cyan-400 shrink-0" />
          <span className="truncate">{snapshotLoading ? 'Capturando...' : 'Foto 1080p'}</span>
        </button>

        {/* Capturar Snapshot Duplo com Flash */}
        <button
          onClick={() => handleTakeSnapshot(true)}
          disabled={snapshotLoading || !isOnline}
          className="flex items-center justify-center gap-1.5 sm:gap-2 px-3 py-2.5 sm:px-4 sm:py-3 rounded-lg bg-gradient-to-r from-cyan-600/20 to-amber-600/20 border border-amber-500/40 hover:border-amber-400 hover:bg-amber-600/30 text-amber-200 font-semibold text-[11px] sm:text-xs uppercase tracking-wider transition-all disabled:opacity-40 cursor-pointer shadow-[0_0_15px_rgba(245,158,11,0.15)] min-h-[44px]"
          title="Executa disparo duplo: 1 foto instantânea P&B + liga flash + 1 foto 1080p colorida iluminada"
        >
          <Zap className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-amber-400 shrink-0" />
          <span className="truncate">{snapshotLoading ? 'Fotografando...' : 'Foto c/ Flash'}</span>
        </button>

        {/* Gravar Vídeo Manual (10s) */}
        <button
          onClick={handleRecordVideo}
          disabled={recordLoading || !isOnline}
          className="flex items-center justify-center gap-1.5 sm:gap-2 px-3 py-2.5 sm:px-4 sm:py-3 rounded-lg bg-[#141A26] border border-emerald-500/40 hover:border-emerald-400 hover:bg-emerald-950/40 text-emerald-300 font-semibold text-[11px] sm:text-xs uppercase tracking-wider transition-all disabled:opacity-40 cursor-pointer shadow-[0_0_15px_rgba(16,185,129,0.1)] min-h-[44px]"
          title="Grava um clipe de 10s em alta definição com áudio sincronizado e envia ao Google Drive"
        >
          <Film className={`w-3.5 h-3.5 sm:w-4 sm:h-4 text-emerald-400 shrink-0 ${recordLoading ? 'animate-pulse' : ''}`} />
          <span className="truncate">{recordLoading ? 'Gravando 10s...' : 'Vídeo 10s'}</span>
        </button>

        {/* Alternar Modo Teste 24h */}
        <button
          onClick={handleToggleTestMode}
          className={`flex items-center justify-center gap-1.5 sm:gap-2 px-3 py-2.5 sm:px-4 sm:py-3 rounded-lg border font-semibold text-[11px] sm:text-xs uppercase tracking-wider transition-all cursor-pointer min-h-[44px] ${
            testMode
              ? 'bg-cyan-950/60 border-cyan-500 text-cyan-300'
              : 'bg-[#141A26] border-slate-700 text-slate-400'
          }`}
          title="Permite que o YOLO detecte pessoas e dispare o flash a qualquer hora do dia para testes"
        >
          <ShieldAlert className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-cyan-400 shrink-0" />
          <span className="truncate">Teste: {testMode ? '24h ATIVO' : 'HORÁRIO'}</span>
        </button>

        {/* Recarregar Stream */}
        <button
          onClick={handleRefreshStream}
          className="flex items-center justify-center gap-1.5 sm:gap-2 px-3 py-2.5 sm:px-4 sm:py-3 rounded-lg bg-[#141A26] border border-slate-700 hover:border-slate-500 text-slate-300 font-semibold text-[11px] sm:text-xs uppercase tracking-wider transition-all cursor-pointer min-h-[44px]"
        >
          <RefreshCw className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-slate-400 shrink-0" />
          <span className="truncate">Recarregar</span>
        </button>

        {/* Tela Cheia */}
        <button
          onClick={handleToggleFullscreen}
          className="col-span-2 sm:col-span-1 flex items-center justify-center gap-1.5 sm:gap-2 px-3 py-2.5 sm:px-4 sm:py-3 rounded-lg bg-[#141A26] border border-slate-700 hover:border-slate-500 text-slate-300 font-semibold text-[11px] sm:text-xs uppercase tracking-wider transition-all cursor-pointer min-h-[44px]"
        >
          <Maximize2 className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-slate-400 shrink-0" />
          <span className="truncate">{isFullscreen ? 'Sair Tela Cheia' : 'Tela Cheia'}</span>
        </button>
      </div>

      {/* Card da Última Foto Capturada (se houver) */}
      {lastSnapshotUrl && (
        <div className="p-3 sm:p-4 rounded-xl bg-[#0D111A] border border-slate-800 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <img
              src={lastSnapshotUrl}
              alt="Último snapshot"
              className="w-14 h-11 sm:w-16 sm:h-12 object-cover rounded border border-slate-700 shrink-0"
            />
            <div>
              <p className="text-xs font-semibold text-slate-200">Foto Capturada com Sucesso</p>
              <p className="text-[10px] sm:text-[11px] text-slate-400">Imagem salva no servidor em 1080p nativo</p>
            </div>
          </div>
          <a
            href={lastSnapshotUrl}
            target="_blank"
            rel="noreferrer"
            className="w-full sm:w-auto text-center px-3 py-2 sm:py-1.5 rounded bg-cyan-600/20 border border-cyan-500/40 text-cyan-300 text-xs font-medium hover:bg-cyan-600/30 transition-colors"
          >
            Visualizar Original
          </a>
        </div>
      )}
    </div>
  );
}

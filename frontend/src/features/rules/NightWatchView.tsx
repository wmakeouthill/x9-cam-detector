import React, { useState, useEffect } from 'react';
import { Clock, Zap, Camera, ShieldCheck, CheckCircle2, Sliders, CalendarDays, MessageSquare, Settings2, Cpu, Sparkles } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { WhatsAppConfigModal } from './WhatsAppConfigModal';

export function NightWatchView() {
  const { token } = useAuthStore();
  const [detectionMode, setDetectionMode] = useState<'hybrid' | 'camera' | 'yolo'>('hybrid');
  const [startTime, setStartTime] = useState('00:00');
  const [endTime, setEndTime] = useState('06:00');
  const [flashDuration, setFlashDuration] = useState('2.5');
  const [modelType, setModelType] = useState('yolov8s');
  const [confidence, setConfidence] = useState('0.45');
  const [retentionDays, setRetentionDays] = useState('15');
  const [enabled, setEnabled] = useState(true);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(false);
  const [isWhatsappModalOpen, setIsWhatsappModalOpen] = useState(false);
  const [whatsappStatus, setWhatsappStatus] = useState<{ enabled: boolean; target_number: string } | null>(null);

  const fetchWhatsappStatus = () => {
    if (!token) return;
    fetch('/api/camera/whatsapp', {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => res.json())
      .then((data) => {
        setWhatsappStatus({
          enabled: Boolean(data.enabled),
          target_number: data.target_number || '',
        });
      })
      .catch(() => {});
  };

  useEffect(() => {
    if (!token) return;
    fetch('/api/camera/nightwatch', {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.start_time) setStartTime(data.start_time);
        if (data.end_time) setEndTime(data.end_time);
        if (data.flash_duration) setFlashDuration(String(data.flash_duration));
        if (data.model_type) setModelType(data.model_type);
        if (data.confidence_threshold) setConfidence(String(data.confidence_threshold));
        if (data.retention_days) setRetentionDays(String(data.retention_days));
        if (data.enabled !== undefined) setEnabled(Boolean(data.enabled));
        if (data.detection_mode) setDetectionMode(data.detection_mode);
      })
      .catch(() => {});
    fetchWhatsappStatus();
  }, [token]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await fetch('/api/camera/nightwatch', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          enabled,
          start_time: startTime,
          end_time: endTime,
          flash_duration: Number(flashDuration),
          confidence_threshold: Number(confidence),
          model_type: modelType,
          retention_days: Number(retentionDays),
          detection_mode: detectionMode,
        }),
      });
      if (res.ok) {
        setSaved(true);
        setTimeout(() => setSaved(false), 3000);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 p-3.5 sm:p-6 md:p-8 max-w-3xl overflow-y-auto">
      <div className="flex items-center gap-2.5 sm:gap-3 mb-4 sm:mb-6">
        <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-lg bg-amber-600/20 border border-amber-500/30 flex items-center justify-center text-amber-400 shrink-0">
          <Clock className="w-5 h-5" />
        </div>
        <div>
          <h2 className="text-lg sm:text-xl font-bold font-['Chakra_Petch'] text-slate-100 uppercase tracking-wide">
            Vigia Noturno & Disparo Automático de Flash
          </h2>
          <p className="text-[11px] sm:text-xs text-slate-400">
            Monitoramento de pessoas no escuro, disparo de flash e retenção inteligente de 15 dias
          </p>
        </div>
      </div>

      {saved && (
        <div className="p-3 sm:p-4 rounded-xl border mb-4 sm:mb-6 text-xs flex items-center gap-3 bg-emerald-500/10 border-emerald-500/30 text-emerald-300">
          <CheckCircle2 className="w-5 h-5 shrink-0 text-emerald-400" />
          <span>Regras salvas e persistidas no SQLite com sucesso!</span>
        </div>
      )}

      <form onSubmit={handleSave} className="bg-[#0D111A] border border-slate-800 rounded-xl p-4 sm:p-6 space-y-5 sm:space-y-6">
        {/* Toggle Ativo/Inativo */}
        <div className="flex items-center justify-between p-3.5 sm:p-4 rounded-lg bg-[#141A26] border border-slate-800 gap-2">
          <div>
            <p className="text-xs font-semibold text-slate-200">Ativação do Vigia Noturno</p>
            <p className="text-[10px] sm:text-[11px] text-slate-400">Detectar pessoas e disparar flash apenas durante a madrugada configurada</p>
          </div>
          <button
            type="button"
            onClick={() => setEnabled(!enabled)}
            className={`w-12 h-6 flex items-center rounded-full p-1 cursor-pointer transition-colors shrink-0 ${
              enabled ? 'bg-cyan-600 justify-end' : 'bg-slate-700 justify-start'
            }`}
          >
            <div className="w-4 h-4 bg-white rounded-full shadow-md" />
          </button>
        </div>

        {/* Seletor de Modo de Detecção */}
        <div className="p-3.5 sm:p-4 rounded-lg bg-[#141A26] border border-slate-800 space-y-3">
          <div>
            <label className="block text-xs font-bold uppercase tracking-wider text-cyan-400 font-mono flex items-center gap-2">
              <Cpu className="w-4 h-4 text-cyan-400" /> Modo de Operação & Detecção
            </label>
            <p className="text-[11px] text-slate-400 mt-0.5">
              Escolha quem analisa a cena e quando o modelo YOLO é ativado
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {/* Modo Híbrido */}
            <div
              onClick={() => setDetectionMode('hybrid')}
              className={`p-3 rounded-lg border cursor-pointer transition-all flex flex-col justify-between ${
                detectionMode === 'hybrid'
                  ? 'bg-cyan-950/40 border-cyan-500 shadow-md shadow-cyan-950/40 ring-1 ring-cyan-500/50'
                  : 'bg-[#0D111A] border-slate-800 hover:border-slate-700 opacity-75'
              }`}
            >
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs font-bold text-slate-100 flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-cyan-400" /> Híbrido
                  </span>
                  <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                    PADRÃO
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  Câmera detecta evento (IVS/SMD) e acorda o YOLO por 20s para validar o flagrante.
                </p>
              </div>
              <p className="text-[10px] text-emerald-400 font-mono mt-2 font-semibold">
                ✓ Economiza ~98% de CPU
              </p>
            </div>

            {/* Modo 100% Câmera */}
            <div
              onClick={() => setDetectionMode('camera')}
              className={`p-3 rounded-lg border cursor-pointer transition-all flex flex-col justify-between ${
                detectionMode === 'camera'
                  ? 'bg-emerald-950/40 border-emerald-500 shadow-md shadow-emerald-950/40 ring-1 ring-emerald-500/50'
                  : 'bg-[#0D111A] border-slate-800 hover:border-slate-700 opacity-75'
              }`}
            >
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs font-bold text-slate-100 flex items-center gap-1.5">
                    <Camera className="w-3.5 h-3.5 text-emerald-400" /> 100% Câmera
                  </span>
                  <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                    ULTRA LEVE
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  Inteligência nativa Intelbras (IVS/SMD) aciona gravação e alertas direto.
                </p>
              </div>
              <p className="text-[10px] text-emerald-400 font-mono mt-2 font-semibold">
                ✓ 0% CPU / Sem YOLO
              </p>
            </div>

            {/* Modo YOLO Contínuo */}
            <div
              onClick={() => setDetectionMode('yolo')}
              className={`p-3 rounded-lg border cursor-pointer transition-all flex flex-col justify-between ${
                detectionMode === 'yolo'
                  ? 'bg-purple-950/40 border-purple-500 shadow-md shadow-purple-950/40 ring-1 ring-purple-500/50'
                  : 'bg-[#0D111A] border-slate-800 hover:border-slate-700 opacity-75'
              }`}
            >
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs font-bold text-slate-100 flex items-center gap-1.5">
                    <Sliders className="w-3.5 h-3.5 text-purple-400" /> YOLO 24/7
                  </span>
                  <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/30">
                    CONTÍNUO
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  Servidor analisa todos os quadros do vídeo ininterruptamente.
                </p>
              </div>
              <p className="text-[10px] text-amber-400 font-mono mt-2 font-semibold">
                ⚠ Uso contínuo de CPU
              </p>
            </div>
          </div>
        </div>

        {/* Janela de Horário */}
        <div>
          <label className="block text-xs font-bold uppercase tracking-wider text-cyan-400 mb-2 sm:mb-3 font-mono flex items-center gap-2">
            <Clock className="w-4 h-4" /> 1. Janela de Horário da Madrugada
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">Início do Vigia</label>
              <input
                type="time"
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                className="w-full bg-[#131824] border border-slate-700 rounded-lg p-2.5 text-xs text-slate-100 font-mono focus:border-cyan-500 focus:outline-none min-h-[42px]"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">Término do Vigia</label>
              <input
                type="time"
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
                className="w-full bg-[#131824] border border-slate-700 rounded-lg p-2.5 text-xs text-slate-100 font-mono focus:border-cyan-500 focus:outline-none min-h-[42px]"
              />
            </div>
          </div>
          <p className="text-[10px] text-slate-500 mt-2">
            Pessoas detectadas entre {startTime} e {endTime} acionarão o flash de luz branca e a foto.
          </p>
        </div>

        {/* Retenção de Vídeos e Fotos */}
        <div className="pt-4 border-t border-slate-800">
          <label className="block text-xs font-bold uppercase tracking-wider text-cyan-400 mb-2 sm:mb-3 font-mono flex items-center gap-2">
            <CalendarDays className="w-4 h-4" /> 2. Retenção & Limpeza Automática
          </label>
          <div className="p-3.5 sm:p-4 rounded-lg bg-[#141A26] border border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold text-slate-200">Período de Retenção</p>
              <p className="text-[10px] sm:text-[11px] text-slate-400">Arquivos mais antigos são removidos automaticamente para poupar disco na VPS</p>
            </div>
            <div className="flex items-center gap-2 self-start sm:self-auto">
              <input
                type="number"
                min="5"
                max="60"
                value={retentionDays}
                onChange={(e) => setRetentionDays(e.target.value)}
                className="w-16 bg-[#0D111A] border border-slate-700 rounded p-1.5 text-xs text-center text-slate-100 font-mono"
              />
              <span className="text-xs text-slate-300 font-mono">dias</span>
            </div>
          </div>
        </div>

        {/* Modelo YOLO & Sensibilidade */}
        <div className={`pt-4 border-t border-slate-800 transition-opacity ${detectionMode === 'camera' ? 'opacity-40 pointer-events-none' : 'opacity-100'}`}>
          <div className="flex items-center justify-between mb-2 sm:mb-3">
            <label className="block text-xs font-bold uppercase tracking-wider text-cyan-400 font-mono flex items-center gap-2">
              <Sliders className="w-4 h-4" /> 3. Modelo YOLO & Sensibilidade Noturna
            </label>
            {detectionMode === 'camera' && (
              <span className="text-[10px] font-mono text-slate-400 bg-slate-800 px-2 py-0.5 rounded">
                Desativado no modo 100% Câmera
              </span>
            )}
          </div>

          <div className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">Modelo de IA</label>
              <select
                value={modelType}
                onChange={(e) => setModelType(e.target.value)}
                className="w-full bg-[#131824] border border-slate-700 rounded-lg p-2.5 text-xs text-slate-100 font-mono focus:border-cyan-500 focus:outline-none cursor-pointer min-h-[42px]"
              >
                <option value="yolov8n">YOLOv8n (Nano) - Ultra leve (~15ms CPU, ~250MB RAM)</option>
                <option value="yolov8s">YOLOv8s (Small - Recomendado) - Equilíbrio ideal leveza x qualidade</option>
                <option value="yolov8m">YOLOv8m (Medium) - Alta precisão para silhuetas distantes</option>
              </select>
            </div>

            <div>
              <div className="flex justify-between items-center mb-1.5">
                <label className="text-xs font-semibold text-slate-300">Confiança Mínima de Detecção</label>
                <span className="text-xs font-mono text-cyan-400">{Math.round(Number(confidence) * 100)}%</span>
              </div>
              <input
                type="range"
                min="0.2"
                max="0.8"
                step="0.05"
                value={confidence}
                onChange={(e) => setConfidence(e.target.value)}
                className="w-full accent-cyan-500 cursor-pointer h-2"
              />
            </div>
          </div>
        </div>

        {/* Ações ao Detectar Pessoa */}
        <div className="pt-4 border-t border-slate-800">
          <label className="block text-xs font-bold uppercase tracking-wider text-cyan-400 mb-2 sm:mb-3 font-mono flex items-center gap-2">
            <Zap className="w-4 h-4 text-amber-400" /> 4. Ações ao Detectar Pessoa
          </label>

          <div className="space-y-3">
            <div className="p-3 sm:p-3.5 rounded-lg bg-[#131824] border border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <Zap className="w-5 h-5 text-amber-400 shrink-0" />
                <div>
                  <p className="text-xs font-semibold text-slate-200">Disparar Flash (LED Luz Branca)</p>
                  <p className="text-[10px] text-slate-400">Acende a iluminação da câmera para espantar intruso e iluminar a cena</p>
                </div>
              </div>
              <div className="flex items-center gap-2 self-start sm:self-auto">
                <span className="text-xs text-slate-400">Duração:</span>
                <input
                  type="number"
                  step="0.5"
                  min="1"
                  max="10"
                  value={flashDuration}
                  onChange={(e) => setFlashDuration(e.target.value)}
                  className="w-16 bg-[#0D111A] border border-slate-700 rounded p-1 text-xs text-center text-slate-100 font-mono"
                />
                <span className="text-xs text-slate-400">s</span>
              </div>
            </div>

            <div className="p-3 sm:p-3.5 rounded-lg bg-[#131824] border border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <Camera className="w-5 h-5 text-cyan-400 shrink-0" />
                <div>
                  <p className="text-xs font-semibold text-slate-200">Foto Instantânea em Alta Resolução (1080p Nativo)</p>
                  <p className="text-[10px] text-slate-400">Salva snapshot em alta qualidade do sensor enquanto o flash está aceso</p>
                </div>
              </div>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 self-start sm:self-auto">
                HABILITADO
              </span>
            </div>

            {/* Integração WhatsApp Evolution API v2 */}
            <div className="p-3 sm:p-3.5 rounded-lg bg-[#131824] border border-slate-800 hover:border-emerald-500/30 transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <MessageSquare className="w-5 h-5 text-emerald-400 shrink-0" />
                <div>
                  <div className="flex items-center gap-2">
                    <p className="text-xs font-semibold text-slate-200">Alertas no WhatsApp (Evolution API v2)</p>
                    {whatsappStatus?.enabled ? (
                      <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 font-bold">
                        ATIVO
                      </span>
                    ) : (
                      <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 font-bold">
                        DESATIVADO
                      </span>
                    )}
                  </div>
                  <p className="text-[10px] text-slate-400">
                    {whatsappStatus?.enabled && whatsappStatus?.target_number
                      ? `Enviando flagrantes com foto e vídeo para +${whatsappStatus.target_number}`
                      : 'Receba fotos de flagrante e vídeo de 10s direto no WhatsApp ao detectar invasão'}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setIsWhatsappModalOpen(true)}
                className="flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/30 border border-emerald-500/40 text-emerald-300 text-xs font-semibold transition-all cursor-pointer shadow-sm shadow-emerald-500/10 min-h-[38px] self-start sm:self-auto"
              >
                <Settings2 className="w-4 h-4 text-emerald-400" />
                <span>Configurar WhatsApp</span>
              </button>
            </div>
          </div>
        </div>

        <div className="pt-4 border-t border-slate-800 flex justify-end">
          <button
            type="submit"
            disabled={loading}
            className="w-full sm:w-auto flex items-center justify-center gap-2 px-6 py-3 sm:py-2.5 rounded-lg bg-gradient-to-r from-amber-600 to-yellow-600 hover:from-amber-500 hover:to-yellow-500 text-slate-900 font-bold text-xs uppercase tracking-wider transition-all cursor-pointer shadow-lg shadow-amber-600/20 disabled:opacity-50 min-h-[44px]"
          >
            <ShieldCheck className="w-4 h-4" />
            <span>{loading ? 'Salvando...' : 'Salvar Regras Noturnas'}</span>
          </button>
        </div>
      </form>

      {/* Modal de Configuração do WhatsApp */}
      <WhatsAppConfigModal
        isOpen={isWhatsappModalOpen}
        onClose={() => setIsWhatsappModalOpen(false)}
        onSaved={fetchWhatsappStatus}
      />
    </div>
  );
}

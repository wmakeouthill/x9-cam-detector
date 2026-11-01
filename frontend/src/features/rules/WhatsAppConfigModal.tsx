import React, { useState, useEffect } from 'react';
import {
  MessageSquare,
  X,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Send,
  Eye,
  EyeOff,
  Camera,
  Film,
  Zap,
} from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';

interface WhatsAppConfigModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

export function WhatsAppConfigModal({ isOpen, onClose, onSaved }: WhatsAppConfigModalProps) {
  const { token } = useAuthStore();
  const [enabled, setEnabled] = useState(false);
  const [apiUrl, setApiUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [instanceName, setInstanceName] = useState('');
  const [targetNumber, setTargetNumber] = useState('');
  const [sendPhoto, setSendPhoto] = useState(true);
  const [sendVideo, setSendVideo] = useState(true);
  const [showKey, setShowKey] = useState(false);

  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [connectionState, setConnectionState] = useState<{ connected: boolean; state: string; message: string } | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const fetchConfig = async () => {
    if (!token) return;
    setLoading(true);
    try {
      const res = await fetch('/api/camera/whatsapp', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setEnabled(Boolean(data.enabled));
        setApiUrl(data.api_url || '');
        setInstanceName(data.instance_name || '');
        setTargetNumber(data.target_number || '');
        setSendPhoto(data.send_photo !== undefined ? Boolean(data.send_photo) : true);
        setSendVideo(data.send_video !== undefined ? Boolean(data.send_video) : true);
        setApiKey(data.api_key || '');
        if (data.connection) {
          setConnectionState(data.connection);
        }
      }
    } catch {
      // offline
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      setTestResult(null);
      setSaveSuccess(false);
      fetchConfig();
    }
  }, [isOpen, token]);

  if (!isOpen) return null;

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setTestResult(null);
    try {
      const res = await fetch('/api/camera/whatsapp', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          enabled,
          api_url: apiUrl,
          api_key: apiKey,
          instance_name: instanceName,
          target_number: targetNumber,
          send_photo: sendPhoto,
          send_video: sendVideo,
        }),
      });
      if (res.ok) {
        setSaveSuccess(true);
        setTimeout(() => setSaveSuccess(false), 3000);
        if (onSaved) onSaved();
        fetchConfig();
      }
    } finally {
      setLoading(false);
    }
  };

  const handleTestNotification = async () => {
    if (!apiUrl || !instanceName || !targetNumber) {
      setTestResult({
        success: false,
        message: 'Preencha a URL, Instância e Número de WhatsApp antes de testar.',
      });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch('/api/camera/whatsapp/test', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          api_url: apiUrl,
          api_key: apiKey,
          instance_name: instanceName,
          target_number: targetNumber,
        }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setTestResult({ success: true, message: data.message });
      } else {
        setTestResult({
          success: false,
          message: data.message || data.detail || 'Falha ao conectar com a Evolution API.',
        });
      }
      if (data.connection) {
        setConnectionState(data.connection);
      }
    } catch (err: any) {
      setTestResult({
        success: false,
        message: `Erro de rede: ${err.message || 'Servidor inalcançável'}`,
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/85 backdrop-blur-md animate-fade-in select-none">
      <div className="relative w-full max-w-xl bg-[#090D14] border border-emerald-500/40 rounded-2xl shadow-[0_0_50px_rgba(16,185,129,0.15)] flex flex-col max-h-[92vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 sm:p-5 border-b border-zinc-800 bg-[#0C121D]/70">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0 shadow-[0_0_15px_rgba(16,185,129,0.2)]">
              <MessageSquare className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base sm:text-lg font-bold text-slate-100 font-['Chakra_Petch'] flex items-center gap-2">
                <span>Alertas WhatsApp</span>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 font-semibold">
                  Evolution API v2
                </span>
              </h3>
              <p className="text-xs text-slate-400">
                Receba foto de flagrante com flash e vídeo de invasão em tempo real
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-zinc-800 transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Corpo com Scroll */}
        <form onSubmit={handleSave} className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-5">
          {/* Status de Conexão com a Evolution API */}
          {connectionState && (
            <div
              className={`p-3 rounded-xl border flex items-center justify-between gap-3 text-xs font-mono ${
                connectionState.connected
                  ? 'bg-emerald-950/40 border-emerald-500/50 text-emerald-300'
                  : 'bg-amber-950/30 border-amber-500/40 text-amber-300'
              }`}
            >
              <div className="flex items-center gap-2">
                <span
                  className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                    connectionState.connected ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'
                  }`}
                />
                <span className="font-semibold">{connectionState.message}</span>
              </div>
              <button
                type="button"
                onClick={fetchConfig}
                disabled={loading}
                className="text-[11px] px-2 py-1 rounded bg-black/40 hover:bg-black/60 text-slate-300 border border-slate-700/60 transition-colors flex items-center gap-1 cursor-pointer shrink-0"
                title="Recarregar status da Evolution API"
              >
                <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
                <span>Atualizar</span>
              </button>
            </div>
          )}

          {/* Ativação Principal */}
          <div className="p-3.5 rounded-xl bg-[#0F1624] border border-slate-800 flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-bold text-slate-200">Ativar Notificações no WhatsApp</p>
              <p className="text-[11px] text-slate-400">
                Dispara mensagens automáticas quando o Modo Vigia detectar uma pessoa
              </p>
            </div>
            <label className="relative inline-flex items-center cursor-pointer shrink-0">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-11 h-6 bg-zinc-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-zinc-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-emerald-600 shadow-inner"></div>
            </label>
          </div>

          {/* Campos de Conexão com Evolution API */}
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5 font-mono">
                URL da Evolution API
              </label>
              <input
                type="text"
                placeholder="http://localhost:8080 ou http://seu-ip:8080"
                value={apiUrl}
                onChange={(e) => setApiUrl(e.target.value)}
                className="w-full bg-[#0D121B] border border-slate-700/80 rounded-lg px-3 py-2 text-xs text-slate-100 font-mono focus:outline-none focus:border-emerald-500 transition-colors"
                required={enabled}
              />
              <p className="text-[10px] text-slate-500 mt-1 font-mono">
                Endereço do container ou servidor onde a Evolution API está em execução.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1.5 font-mono">
                  Nome da Instância
                </label>
                <input
                  type="text"
                  placeholder="ex: mirassol-ti ou vigia-x9"
                  value={instanceName}
                  onChange={(e) => setInstanceName(e.target.value)}
                  className="w-full bg-[#0D121B] border border-slate-700/80 rounded-lg px-3 py-2 text-xs text-slate-100 font-mono focus:outline-none focus:border-emerald-500 transition-colors"
                  required={enabled}
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1.5 font-mono">
                  WhatsApp(s) Destinatário(s)
                </label>
                <input
                  type="text"
                  placeholder="(11) 99999-9999, (11) 98888-8888"
                  value={targetNumber}
                  onChange={(e) => setTargetNumber(e.target.value)}
                  className="w-full bg-[#0D121B] border border-slate-700/80 rounded-lg px-3 py-2 text-xs text-slate-100 font-mono focus:outline-none focus:border-emerald-500 transition-colors"
                  required={enabled}
                />
                {targetNumber.trim() ? (
                  <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                    <span className="text-[10px] text-slate-400 font-mono">Reconhecido:</span>
                    {targetNumber
                      .split(/[,;\n/]/)
                      .map((n) => n.replace(/\D/g, ''))
                      .filter((n) => n.length >= 10)
                      .map((n) => (n.length === 10 || n.length === 11 ? `55${n}` : n))
                      .map((num, idx) => (
                        <span
                          key={idx}
                          className="inline-flex items-center text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/20 border border-emerald-500/35 text-emerald-300 font-medium"
                        >
                          +{num}
                        </span>
                      ))}
                  </div>
                ) : (
                  <p className="text-[10px] text-slate-500 mt-1 font-mono">
                    Digite com DDD (ex: 11 99999-9999). Pode colocar vários separados por vírgula.
                  </p>
                )}
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5 font-mono">
                Chave de Autenticação (API Key)
              </label>
              <div className="relative">
                <input
                  type={showKey ? 'text' : 'password'}
                  placeholder="AUTHENTICATION_API_KEY da Evolution API"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  className="w-full bg-[#0D121B] border border-slate-700/80 rounded-lg pl-3 pr-10 py-2 text-xs text-slate-100 font-mono focus:outline-none focus:border-emerald-500 transition-colors"
                  required={enabled}
                />
                <button
                  type="button"
                  onClick={() => setShowKey(!showKey)}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
                >
                  {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              <p className="text-[10px] text-slate-500 mt-1 font-mono">
                Protegido e criptografado com chave AES-256 no banco local.
              </p>
            </div>
          </div>

          {/* Opções de Mídia no Alerta */}
          <div className="pt-2">
            <label className="block text-xs font-bold uppercase tracking-wider text-emerald-400 mb-2 font-mono flex items-center gap-1.5">
              <Zap className="w-3.5 h-3.5 text-emerald-400" /> Mídias do Flagrante
            </label>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              <label className="flex items-center gap-3 p-3 rounded-lg bg-[#0F1624] border border-slate-800 cursor-pointer hover:border-slate-700 transition-colors">
                <input
                  type="checkbox"
                  checked={sendPhoto}
                  onChange={(e) => setSendPhoto(e.target.checked)}
                  className="w-4 h-4 rounded text-emerald-600 focus:ring-emerald-500 focus:ring-offset-0 bg-[#0D121B] border-slate-700"
                />
                <div className="flex items-center gap-2">
                  <Camera className="w-4 h-4 text-cyan-400 shrink-0" />
                  <div>
                    <p className="text-xs font-semibold text-slate-200">Foto Colorida com Flash</p>
                    <p className="text-[10px] text-slate-400">Enviada no 1º segundo da detecção</p>
                  </div>
                </div>
              </label>

              <label className="flex items-center gap-3 p-3 rounded-lg bg-[#0F1624] border border-slate-800 cursor-pointer hover:border-slate-700 transition-colors">
                <input
                  type="checkbox"
                  checked={sendVideo}
                  onChange={(e) => setSendVideo(e.target.checked)}
                  className="w-4 h-4 rounded text-emerald-600 focus:ring-emerald-500 focus:ring-offset-0 bg-[#0D121B] border-slate-700"
                />
                <div className="flex items-center gap-2">
                  <Film className="w-4 h-4 text-emerald-400 shrink-0" />
                  <div>
                    <p className="text-xs font-semibold text-slate-200">Vídeo MP4 (10s)</p>
                    <p className="text-[10px] text-slate-400">Enviado logo após a gravação</p>
                  </div>
                </div>
              </label>
            </div>
          </div>

          {/* Feedback de Teste */}
          {testResult && (
            <div
              className={`p-3 rounded-xl border flex items-start gap-2.5 text-xs font-mono ${
                testResult.success
                  ? 'bg-emerald-950/50 border-emerald-500/60 text-emerald-200'
                  : 'bg-red-950/50 border-red-500/60 text-red-200'
              }`}
            >
              {testResult.success ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
              ) : (
                <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
              )}
              <div className="flex-1 break-words leading-relaxed">{testResult.message}</div>
            </div>
          )}

          {/* Feedback de Salvo */}
          {saveSuccess && (
            <div className="p-3 rounded-xl bg-emerald-950/60 border border-emerald-500/60 text-emerald-300 text-xs font-mono flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>Configurações do WhatsApp salvas com sucesso!</span>
            </div>
          )}

          {/* Footer / Ações */}
          <div className="pt-3 border-t border-slate-800 flex flex-col sm:flex-row items-center justify-between gap-3">
            <button
              type="button"
              onClick={handleTestNotification}
              disabled={testing || loading}
              className="w-full sm:w-auto flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 hover:border-slate-500 text-slate-200 font-semibold text-xs transition-all cursor-pointer disabled:opacity-50 min-h-[40px]"
            >
              <Send className={`w-3.5 h-3.5 text-emerald-400 ${testing ? 'animate-bounce' : ''}`} />
              <span>{testing ? 'Disparando teste...' : 'Enviar Teste no WhatsApp'}</span>
            </button>

            <div className="flex items-center gap-2 w-full sm:w-auto">
              <button
                type="button"
                onClick={onClose}
                className="flex-1 sm:flex-initial px-4 py-2.5 rounded-lg bg-transparent hover:bg-zinc-800 text-slate-400 hover:text-slate-200 font-semibold text-xs transition-colors cursor-pointer min-h-[40px]"
              >
                Fechar
              </button>
              <button
                type="submit"
                disabled={loading}
                className="flex-1 sm:flex-initial flex items-center justify-center gap-2 px-6 py-2.5 rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-bold text-xs uppercase tracking-wider transition-all cursor-pointer shadow-lg shadow-emerald-600/25 disabled:opacity-50 min-h-[40px]"
              >
                <span>{loading ? 'Salvando...' : 'Salvar'}</span>
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

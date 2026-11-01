import React, { useState } from 'react';
import { Shield, Lock, User, AlertCircle, CheckCircle2, Terminal } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';

export function LoginPage() {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const { login, isLoading, error } = useAuthStore();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await login(username.trim(), password.trim());
  };

  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-[#040608] p-3 sm:p-4 relative overflow-hidden terminal-grid">
      {/* Luz de fundo verde terminal sutil */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-emerald-500/10 rounded-full blur-3xl pointer-events-none" />

      <div className="w-full max-w-md bg-[#080B10] border border-emerald-500/30 rounded-xl p-5 sm:p-8 shadow-[0_0_50px_rgba(0,0,0,0.9)] relative z-10 backdrop-blur-md">
        {/* Cabeçalho com o Novo Logo Gerado */}
        <div className="flex flex-col items-center mb-6 sm:mb-8">
          <div className="w-16 h-16 sm:w-20 sm:h-20 rounded-2xl bg-black border border-emerald-500/50 overflow-hidden flex items-center justify-center mb-3 shadow-[0_0_25px_rgba(16,185,129,0.25)] relative">
            <img src="/logo.png" alt="X9 Logo" className="w-full h-full object-cover" />
            <div className="absolute inset-0 bg-emerald-500/5 pointer-events-none" />
          </div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-wider text-zinc-100 font-['Chakra_Petch'] uppercase flex items-center gap-2">
            X9 Cam Detector
          </h1>
          <p className="text-[11px] sm:text-xs text-zinc-400 font-mono tracking-wider uppercase mt-1 flex items-center gap-1.5 text-center">
            <Shield className="w-3.5 h-3.5 text-emerald-400 shrink-0" /> NOC Sentinel • Intelbras VIP 1230 FC+
          </p>
        </div>

        {/* Mensagem de Erro */}
        {error && (
          <div className="mb-6 p-3 rounded-lg bg-red-500/10 border border-red-500/30 flex items-center gap-3 text-red-400 text-sm font-mono">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-2 font-mono flex items-center gap-1.5">
              <Terminal className="w-3.5 h-3.5 text-emerald-400" /> Identificação (User)
            </label>
            <div className="relative">
              <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                required
                className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg py-2.5 pl-10 pr-4 text-sm text-zinc-100 font-mono placeholder-zinc-600 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-colors"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-2 font-mono flex items-center gap-1.5">
              <Lock className="w-3.5 h-3.5 text-emerald-400" /> Senha de Acesso
            </label>
            <div className="relative">
              <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                required
                className="w-full bg-[#0E131C] border border-zinc-700/60 rounded-lg py-2.5 pl-10 pr-4 text-sm text-zinc-100 font-mono placeholder-zinc-600 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-colors"
              />
            </div>
            <p className="text-[11px] text-zinc-500 mt-1.5 flex items-center gap-1 font-mono">
              <CheckCircle2 className="w-3 h-3 text-emerald-500" /> Argon2id + JWT Session Guard
            </p>
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full py-3 px-4 bg-gradient-to-r from-emerald-500 to-green-500 hover:from-emerald-400 hover:to-green-400 text-black font-bold font-mono text-sm rounded-lg shadow-lg shadow-emerald-500/20 transition-all flex items-center justify-center gap-2 uppercase tracking-wider disabled:opacity-50 cursor-pointer"
          >
            {isLoading ? (
              <span className="flex items-center gap-2">
                <div className="w-4 h-4 border-2 border-black/30 border-t-black rounded-full animate-spin" />
                Autenticando...
              </span>
            ) : (
              'Acessar Console'
            )}
          </button>
        </form>

        <div className="mt-8 pt-6 border-t border-zinc-800/80 text-center text-xs text-zinc-500 font-mono">
          Credencial padrão: <code className="text-emerald-400 bg-emerald-950/40 border border-emerald-500/30 px-1.5 py-0.5 rounded">admin</code> / <code className="text-emerald-400 bg-emerald-950/40 border border-emerald-500/30 px-1.5 py-0.5 rounded">admin123</code>
        </div>
      </div>
    </div>
  );
}

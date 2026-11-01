import subprocess
import sys
import os
import time
import socket
import shutil

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def main():
    root = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(root, "backend")
    frontend_dir = os.path.join(root, "frontend")
    dist_dir = os.path.join(frontend_dir, "dist")
    local_ip = get_local_ip()

    has_pnpm = shutil.which("pnpm") is not None
    has_dist = os.path.exists(dist_dir)

    print("=" * 64)
    print("🚀 X9 CAM DETECTOR INICIADO (Modo Sem Docker)")
    print("=" * 64)
    print("🏠 Acesso neste computador:")
    if has_pnpm:
        print("   Frontend Web:  http://localhost:5173")
    print("   Backend / App: http://localhost:8000")
    print()
    print("🌐 Acesso na sua Rede Local (Celular / Tablet / Outro PC):")
    if has_pnpm:
        print(f"   Frontend Web:  http://{local_ip}:5173")
    print(f"   Direto na API: http://{local_ip}:8000")
    if has_dist:
        print(f"   App Completo:  http://{local_ip}:8000 (Static Dist)")
    print()
    print("🔐 Credenciais de Acesso: admin / admin123")
    print("⌨️  Pressione Ctrl+C para encerrar os serviços.")
    print("=" * 64)

    # Inicia Backend FastAPI (0.0.0.0 escuta em todas as placas de rede)
    p_backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
        cwd=backend_dir,
    )

    p_front = None
    if has_pnpm:
        # Inicia Frontend Vite com --host para aceitar conexões da rede local
        p_front = subprocess.Popen(
            ["cmd.exe", "/c", "pnpm", "dev", "--host", "0.0.0.0"],
            cwd=frontend_dir,
        )
    else:
        print("[!] pnpm/node não detectado. Servindo frontend compilado direto pelo Backend na porta 8000.")

    try:
        while True:
            time.sleep(1)
            if p_backend.poll() is not None:
                print("Backend encerrou.")
                break
            if p_front and p_front.poll() is not None:
                print("Frontend encerrou.")
                break
    except KeyboardInterrupt:
        print("\nEncerrando serviços do X9...")
    finally:
        p_backend.terminate()
        if p_front:
            p_front.terminate()

if __name__ == "__main__":
    main()

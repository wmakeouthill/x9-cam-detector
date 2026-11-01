import sys
import time
import argparse
import requests
from requests.auth import HTTPDigestAuth
import os

# Carrega valores do .env se existir
def load_env_defaults():
    env_file = ".env"
    defaults = {
        "ip": "192.168.1.108",
        "user": "admin",
        "password": "",
        "port": 80
    }
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == "CAMERA_IP":
                    defaults["ip"] = v
                elif k == "CAMERA_USER":
                    defaults["user"] = v
                elif k == "CAMERA_PASSWORD":
                    defaults["password"] = v
                elif k == "CAMERA_HTTP_PORT":
                    try:
                        defaults["port"] = int(v)
                    except ValueError:
                        pass
    return defaults

def get_lighting_config(ip, port, user, password):
    url = f"http://{ip}:{port}/cgi-bin/configManager.cgi?action=getConfig&name=Lighting"
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(user, password), timeout=5)
        print("Configuração atual de iluminação:")
        print(resp.text.strip())
        return resp.text
    except Exception as e:
        print(f"Erro ao consultar iluminação: {e}")
        return None

import hashlib

def set_flash_rpc(ip, port, user, password, mode="Manual", intensity=100, timeout=5):
    """
    Controla o LED de Luz Branca (Full Color / Flash) da Intelbras VIP 1230 FC+
    utilizando a API nativa JSON-RPC 2.0 (/RPC2_Login e /RPC2) com autenticação Digest duplo-MD5.
    """
    on = mode.lower() in ["manual", "on"]
    try:
        s = requests.Session()
        # 1. Challenge inicial
        r1 = s.post(f"http://{ip}:{port}/RPC2_Login", json={
            "method": "global.login",
            "params": {"userName": user, "password": "", "clientType": "Web3.0", "loginType": "Direct"},
            "id": 1,
            "session": 0
        }, timeout=timeout)
        d1 = r1.json()
        sid = d1.get("session")
        params = d1.get("params", {})
        realm = params.get("realm")
        random_nonce = params.get("random")

        if not realm or not random_nonce:
            print("[!] Falha ao obter challenge RPC da câmera.")
            return False

        # 2. Resposta com duplo MD5
        inner = hashlib.md5(f"{user}:{realm}:{password}".encode("utf-8")).hexdigest().upper()
        final_pwd = hashlib.md5(f"{user}:{random_nonce}:{inner}".encode("utf-8")).hexdigest().upper()

        r2 = s.post(f"http://{ip}:{port}/RPC2_Login", json={
            "method": "global.login",
            "params": {
                "userName": user,
                "password": final_pwd,
                "realm": realm,
                "random": random_nonce,
                "clientType": "Web3.0",
                "loginType": "Direct"
            },
            "id": 2,
            "session": sid
        }, timeout=timeout)

        if not r2.json().get("result"):
            print("[!] Autenticação RPC2 falhou com as credenciais da câmera.")
            return False

        def _rpc(method, rpc_params=None):
            return s.post(
                f"http://{ip}:{port}/RPC2",
                json={"method": method, "params": rpc_params or {}, "id": 10, "session": sid},
                timeout=timeout
            ).json()

        scheme_mode = "WhiteMode" if on else "InfraredMode"
        color_mode = "Color" if on else "Night"

        # 1. LightingScheme
        scheme_cfg = _rpc("configManager.getConfig", {"name": "LightingScheme"})
        if scheme_cfg.get("result"):
            scheme = scheme_cfg["params"]["table"]
            for p in range(len(scheme[0])):
                scheme[0][p]["LightingMode"] = scheme_mode
            _rpc("configManager.setConfig", {"name": "LightingScheme", "table": scheme})

        # 2. VideoInDayNight
        daynight_cfg = _rpc("configManager.getConfig", {"name": "VideoInDayNight"})
        if daynight_cfg.get("result"):
            daynight = daynight_cfg["params"]["table"]
            for p in range(len(daynight[0])):
                daynight[0][p]["Mode"] = color_mode
            _rpc("configManager.setConfig", {"name": "VideoInDayNight", "table": daynight})

        # 3. Lighting_V2
        lighting_v2_cfg = _rpc("configManager.getConfig", {"name": "Lighting_V2"})
        if lighting_v2_cfg.get("result"):
            lighting_v2 = lighting_v2_cfg["params"]["table"]
            for p in range(len(lighting_v2[0])):
                # Index 1 = WhiteLight (Luz Branca / Flash)
                lighting_v2[0][p][1]["Mode"] = "Manual" if on else "Off"
                lighting_v2[0][p][1]["PercentOfMaxBrightness"] = 100
                lighting_v2[0][p][1]["NearLight"][0]["Light"] = intensity if on else 0
                lighting_v2[0][p][1]["NearLight"][0]["Angle"] = 50
                if on:
                    # Desliga IR durante o flash para imagem em cores puras
                    lighting_v2[0][p][0]["MiddleLight"][0]["Light"] = 0
                    lighting_v2[0][p][0]["Mode"] = "Off"
                else:
                    # Mantém luz vermelha (IR) 100% no modo de espera
                    lighting_v2[0][p][0]["Mode"] = "Manual"
                    lighting_v2[0][p][0]["PercentOfMaxBrightness"] = 100
                    lighting_v2[0][p][0]["MiddleLight"][0]["Light"] = 100
                    lighting_v2[0][p][0]["MiddleLight"][0]["Angle"] = 50
            _rpc("configManager.setConfig", {"name": "Lighting_V2", "table": lighting_v2})

        print(f"[>] Flash Luz Branca ({'LIGADO' if on else 'DESLIGADO'}): OK")
        return True
    except Exception as e:
        print(f"[!] Erro ao enviar comando RPC de flash: {e}")
        return False

def set_flash(ip, port, user, password, mode="Manual", intensity=100):
    return set_flash_rpc(ip, port, user, password, mode=mode, intensity=intensity)

def take_snapshot(ip, port, user, password, output_file="snapshot_camera.jpg"):
    url = f"http://{ip}:{port}/cgi-bin/snapshot.cgi?channel=1"
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(user, password), timeout=5)
        if resp.status_code == 200:
            with open(output_file, "wb") as f:
                f.write(resp.content)
            print(f"[+] Snapshot em alta resolução salvo em: {output_file}")
            return True
        else:
            print(f"[-] Erro ao capturar snapshot: {resp.status_code}")
            return False
    except Exception as e:
        print(f"[!] Erro ao capturar snapshot: {e}")
        return False

def main():
    defaults = load_env_defaults()

    parser = argparse.ArgumentParser(description="Teste de Flash e Captura - Intelbras VIP 1230 FC+")
    # Aceita tanto -flash quanto --flash
    parser.add_argument("-flash", "--flash", action="store_true", help="Apenas testa o flash (acende e apaga), sem tirar foto/snapshot")
    parser.add_argument("--ip", default=defaults["ip"], help=f"IP da câmera (padrão: {defaults['ip']})")
    parser.add_argument("--port", type=int, default=defaults["port"], help=f"Porta HTTP da câmera (padrão: {defaults['port']})")
    parser.add_argument("--user", default=defaults["user"], help="Usuário da câmera (padrão: admin)")
    parser.add_argument("--password", "-p", default=defaults["password"], help="Senha da câmera")
    parser.add_argument("--seconds", "-s", type=float, default=3.0, help="Tempo em segundos que o flash permanece aceso (padrão: 3.0s)")

    args = parser.parse_args()

    ip = args.ip
    port = args.port
    user = args.user
    password = args.password

    # Se a senha não estiver no .env ou argumentos, pede via input
    if not password:
        password = input("Digite a senha da câmera: ").strip()

    print("=" * 60)
    print(f"[*] CÂMERA: {ip}:{port} (Usuário: {user})")
    print("=" * 60)

    if args.flash:
        print(f"\n[+] MODO EXCLUSIVO DE FLASH ATIVADO (-flash):")
        print(f"[1] Enviando comando para LIGAR flash por {args.seconds} segundos...")
        set_flash(ip, port, user, password, mode="Manual", intensity=100)
        
        print(f"[*] Aguardando {args.seconds}s com a luz acesa...")
        time.sleep(args.seconds)

        print("[2] Enviando comando para DESLIGAR flash e restaurar modo Auto...")
        set_flash(ip, port, user, password, mode="Auto", intensity=0)
        print("[+] Teste de flash concluído!")
        return

    # Modo completo padrão (com duplo snapshot: P&B e Flash Colorido)
    print("\n--- 1. Tirando Foto Instantânea Flagrante (Preto e Branco / IR) ---")
    take_snapshot(ip, port, user, password, output_file="snapshot_pb.jpg")

    print(f"\n--- 2. Acionando Flash de Luz Branca ({args.seconds} segundos) ---")
    set_flash(ip, port, user, password, mode="Manual", intensity=100)

    # Intervalo de 1.2s para comutação mecânica do filtro IR-cut, acendimento do LED e exposição
    print("[*] Aguardando 1.2s para estabilização de luz branca, auto-exposição e foco...")
    time.sleep(1.2)

    print("\n--- 3. Tirando Foto Garantida com Flash (Full Color 1080p) ---")
    take_snapshot(ip, port, user, password, output_file="snapshot_flash.jpg")

    # Aguarda o restante do tempo de flash se o usuário pediu mais tempo
    remaining = args.seconds - 1.2
    if remaining > 0:
        time.sleep(remaining)

    print("\n--- 4. Desligando Flash e Restaurando Modo Noturno Contínuo (Luz Vermelha / IR) ---")
    set_flash(ip, port, user, password, mode="Auto", intensity=0)
    print("\n[+] Teste completo finalizado com sucesso!")
    print("    - Foto 1 (Preto e Branco flagrante): snapshot_pb.jpg")
    print("    - Foto 2 (Full Color com Flash): snapshot_flash.jpg")

if __name__ == "__main__":
    main()

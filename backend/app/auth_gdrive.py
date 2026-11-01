import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

def main():
    client_secret_file = "client_secret.json"
    if not os.path.exists(client_secret_file) and os.path.exists(os.path.join("..", client_secret_file)):
        client_secret_file = os.path.join("..", client_secret_file)

    if not os.path.exists(client_secret_file):
        print(f"[!] Arquivo '{client_secret_file}' não encontrado.")
        print("    Acesse o Google Cloud Console -> APIs e Serviços -> Credenciais")
        print("    Crie um 'ID do cliente OAuth' do tipo 'App para Computador (Desktop)' e salve o JSON como client_secret.json na raiz do projeto.")
        sys.exit(1)

    print("[*] Iniciando autenticação OAuth com sua conta pessoal do Google Drive...")
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
    creds = flow.run_local_server(port=0)

    token_path = "token.json"
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"[+] Autenticação concluída com sucesso! Credencial salva em: {token_path}")
    print("[+] Agora o X9 tem permissão permanente para enviar e gerenciar fotos/vídeos no seu Google Drive pessoal!")

if __name__ == "__main__":
    main()

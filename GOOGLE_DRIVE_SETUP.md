# ☁️ Guia de Configuração: Backup em Nuvem com Google Drive

O **X9 Cam Detector** possui integração nativa em segundo plano com o Google Drive para salvar snapshots de pessoas detectadas pelo detector YOLO.

---

### Passo a Passo Rápido (3 a 5 minutos)

#### 1. Criar a Chave da Service Account no Google Cloud (Gratuito)
1. Acesse o [Google Cloud Console](https://console.cloud.google.com/).
2. Crie ou selecione um projeto (ex: `x9-security`).
3. No menu lateral, acesse **APIs e Serviços** > **Biblioteca**.
4. Pesquise por **Google Drive API** e clique em **Ativar**.
5. Acesse **IAM e Administração** > **Contas de Serviço** (Service Accounts) e clique em **Criar Conta de Serviço**.
6. Nomeie como `x9-uploader` e finalize.
7. Na lista, clique na conta criada, vá na aba **Chaves** (Keys) > **Adicionar Chave** > **Criar Nova Chave** > escolha **JSON**.
8. O arquivo `.json` será baixado no seu computador.
9. Renomeie o arquivo para `service_account.json` e cole na pasta raiz do projeto (`D:\x9-cam-detector\service_account.json`).

#### 2. Criar e Compartilhar a Pasta no Google Drive
1. Acesse o [Google Drive](https://drive.google.com/).
2. Crie a pasta onde quer receber os alertas (ex: `X9-Alertas`).
3. Clique com o botão direito na pasta > **Compartilhar**.
4. Abra o arquivo `service_account.json` e copie o `client_email` (ex: `x9-uploader@seu-projeto.iam.gserviceaccount.com`).
5. Cole no campo de compartilhamento do Google Drive e dê permissão de **Editor**.
6. Entre na pasta no navegador e copie o ID que aparece no final da URL:
   `https://drive.google.com/drive/folders/1a2b3c4d5e6f7g8h...` *(o código final é o seu Folder ID)*.

#### 3. Configurar o `.env`
No arquivo `.env` da raiz do projeto, defina:
```env
GOOGLE_DRIVE_ENABLED=true
GOOGLE_DRIVE_FOLDER_ID=1a2b3c4d5e6f7g8h...
GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE=service_account.json
```

#### 4. Instalar as Dependências (1 comando)
No terminal:
```bash
pip install google-api-python-client google-auth
```

---

### Como Testar
Você pode testar a conexão a qualquer momento pelo backend chamando:
* `GET /api/camera/gdrive/status`
* `POST /api/camera/gdrive/test`

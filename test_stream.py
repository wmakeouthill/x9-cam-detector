import cv2
import sys
import time

def test_rtsp(url):
    print(f"[*] Tentando conectar ao stream: {url}")
    # Usa TCP para evitar perda de pacotes e artifacts
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        print("[-] Falha ao abrir stream RTSP. Verifique IP, porta, usuario e senha.")
        return False

    print("[+] Conectado com sucesso! Capturando 1 frame de teste...")
    ret, frame = cap.read()
    if ret and frame is not None:
        cv2.imwrite("snapshot_teste.jpg", frame)
        print("[+] Frame salvo com sucesso em: snapshot_teste.jpg")
        h, w, _ = frame.shape
        print(f"[+] Resolução do stream: {w}x{h}")
    else:
        print("[-] Nao foi possivel ler frame do stream.")

    print("[*] Abrindo janela de visualizacao (pressione 'q' na janela para sair)...")
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        cv2.imshow("Intelbras VIP 1230 FC+ - Live", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    return True

if __name__ == "__main__":
    if len(sys.argv) > 1:
        rtsp_url = sys.argv[1]
    else:
        # Exemplo padrão
        ip = input("Digite o IP da câmera (ex: 192.168.1.108): ").strip()
        user = input("Digite o usuário (padrão: admin): ").strip() or "admin"
        password = input("Digite a senha da câmera: ").strip()
        stream_type = input("Stream [0=Principal / 1=Substream (leve)]: ").strip() or "1"
        rtsp_url = f"rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel=1&subtype={stream_type}"

    test_rtsp(rtsp_url)

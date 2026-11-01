import socket
import concurrent.futures

def check_ip(ip):
    ports = [37777, 80, 554]
    found_ports = []
    for port in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        try:
            res = s.connect_ex((ip, port))
            if res == 0:
                found_ports.append(port)
        except Exception:
            pass
        finally:
            s.close()
    
    if found_ports:
        return ip, found_ports
    return None

def scan_subnet(base_subnet="192.168.1"):
    print(f"[*] Escaneando sub-rede {base_subnet}.0/24 nas portas 80 (HTTP), 554 (RTSP), 37777 (Dahua/Intelbras)...")
    ips = [f"{base_subnet}.{i}" for i in range(1, 255)]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(check_ip, ip) for ip in ips]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                results.append(res)
                ip, ports = res
                print(f"[+] Dispositivo encontrado: {ip} | Portas abertas: {ports}")
    return results

if __name__ == "__main__":
    scan_subnet()

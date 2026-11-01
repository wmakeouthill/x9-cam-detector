import socket

for ip in ['192.168.1.200', '192.168.1.230']:
    for port in [80, 554]:
        s = socket.socket()
        s.settimeout(3.0)
        res = s.connect_ex((ip, port))
        s.close()
        print(f"{ip}:{port} -> {'OPEN' if res == 0 else f'CLOSED ({res})'}")

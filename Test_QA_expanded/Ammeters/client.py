from socket import socket, AF_INET, SOCK_STREAM


def request_current_from_ammeter(
    port: int,
    command: bytes,
    timeout_seconds: float = 1.0,
):
    with socket(AF_INET, SOCK_STREAM) as s:
        s.settimeout(timeout_seconds)
        s.connect(('localhost', port))
        s.sendall(command)
        data = s.recv(1024)
        if data:
            decoded_data = data.decode('utf-8')
            current = float(decoded_data)
            print(f"Received current measurement from port {port}: {current} A")
            return current

        raise RuntimeError(f"No data received from ammeter on port {port}.")

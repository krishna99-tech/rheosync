import socket

UDP_IP = "0.0.0.0"
UDP_PORT = 63363

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((UDP_IP, UDP_PORT))

print(f"📡 LISTENING FOR UDP PACKETS ON PORT {UDP_PORT}...")
print("If you trigger the ESP32 and nothing appears here, your firewall/network is still blocking it!")
print("-" * 75)

while True:
    try:
        data, addr = sock.recvfrom(1024)
        print(f"✅ DATA RECEIVED! From {addr[0]}:{addr[1]} -> '{data.decode('ascii', errors='ignore')}'")
    except Exception as e:
        print(f"Error: {e}")

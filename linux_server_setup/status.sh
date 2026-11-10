#!/bin/bash
# Check status of the service, ports, and logs
echo "================================================================="
echo "  WALVOIL VISCOSITY SYSTEM STATUS"
echo "================================================================="

echo -e "\n[1] Systemd Service Status:"
sudo systemctl status walvoil-viscosity --no-pager

echo -e "\n[2] Port 5001 (Web Dashboard TCP):"
ss -tulpn | grep 5001 || echo "Port 5001 not listening!"

echo -e "\n[3] Port 63363 (IoT UDP Telemetry):"
ss -tulpn | grep 63363 || echo "Port 63363 (UDP) not listening!"

echo -e "\n[4] Active Gunicorn Process:"
ps aux | grep "[g]unicorn" || echo "Gunicorn process not found!"

echo -e "\n================================================================="


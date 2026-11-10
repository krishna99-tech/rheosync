#!/bin/bash
# Stream real-time application and systemd logs
echo "Streaming logs for Walvoil Viscosity System (Press Ctrl+C to exit)..."
echo "---------------------------------------------------------------------"
sudo journalctl -u walvoil-viscosity -f


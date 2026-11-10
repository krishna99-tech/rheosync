#!/bin/bash
# Restart the Walvoil Viscosity service (e.g. after code update)
echo "Restarting Walvoil Viscosity System..."
sudo systemctl restart walvoil-viscosity
sleep 1
sudo systemctl status walvoil-viscosity --no-pager


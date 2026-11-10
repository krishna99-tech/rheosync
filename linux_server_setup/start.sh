#!/bin/bash
# Start the Walvoil Viscosity service
echo "Starting Walvoil Viscosity System..."
sudo systemctl start walvoil-viscosity
sudo systemctl status walvoil-viscosity --no-pager


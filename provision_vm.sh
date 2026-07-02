#!/usr/bin/env bash
set -euo pipefail

az group create --name catalyst-pilot-rg --location centralindia

az vm create \
  --resource-group catalyst-pilot-rg \
  --name catalyst-pilot-vm \
  --image Ubuntu2204 \
  --size Standard_D2s_v3 \
  --admin-username aditya \
  --generate-ssh-keys \
  --os-disk-size-gb 30

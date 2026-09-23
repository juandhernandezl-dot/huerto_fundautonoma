#!/usr/bin/env bash
cd "$(dirname "$0")"
salida="logs/docker-$(date +%Y%m%d-%H%M%S).log"
docker compose logs --no-color --timestamps > "$salida"
echo "Logs guardados en $salida"

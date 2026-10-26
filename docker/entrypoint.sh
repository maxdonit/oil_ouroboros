#!/bin/sh
# Точка входа образов oilo/app и oilo/ml:
#   tools     — сервис инструментов (MCP для Ouroboros + API для консоли)
#   console   — консоль: чат, ходы, мониторинг, качество, источники, архитектура
#   gateway   — LLM-шлюз
#   encoder   — кодировщик BGE-M3 + загрузка отчётов (AUTO_INGEST: если индекс пуст — строится в фоне)
#   bootstrap — разовая настройка агента (Ouroboros) до его первого запуска
#   schedules — засев расписаний Ouroboros (ouroboros/schedules) после его старта
#   egress    — выход агента наружу для канала владельца, профиль owner (только CONNECT :443 к EGRESS_ALLOW)
#   любая другая команда — CLI `oilo …` (ingest, catalog, forecast, eval, …)
set -e
cd /app
case "$1" in
  tools)     exec python -m oil_ouroboros.tools_app ;;
  console)   exec python -m oil_ouroboros.console.app ;;
  gateway)   exec python -m oil_ouroboros.gateway.app ;;
  encoder)   exec python -m oil_ouroboros.rag.encoder_service ;;
  bootstrap) shift; exec python -m oil_ouroboros.bootstrap "$@" ;;
  schedules) shift; exec python -m oil_ouroboros.schedules "$@" ;;
  egress)    exec python -m oil_ouroboros.egress ;;
  *)         exec oilo "$@" ;;
esac

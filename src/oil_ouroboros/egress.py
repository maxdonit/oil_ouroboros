"""
Выход агента Ouroboros наружу для канала владельца (Telegram, навыки): HTTP-прокси только с CONNECT на порт 443 и
только к хостам из EGRESS_ALLOW (по умолчанию api.telegram.org; шаблоны вида *.example.com).

Ouroboros живёт в сети agent без шлюза наружу; этот сервис (профиль owner) стоит в двух сетях и пропускает только то,
что разрешено. Если у хоста нет прямого выхода, туннель идёт через готовый прокси EGRESS_UPSTREAM=host:port
(deploy/compose/proxy.yml) — список разрешённых хостов действует и тогда. Содержимое не читается: внутри CONNECT —
TLS до самого хоста.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import logging
import os

log = logging.getLogger("oilo.egress")
PORT = 3128
TIMEOUT_S = 15


def allow_list() -> list[str]:
    return [h.strip().lower() for h in os.environ.get("EGRESS_ALLOW", "api.telegram.org").split(",") if h.strip()]


def allowed(host: str, port: int, allow: list[str] | None = None) -> bool:
    return port == 443 and any(fnmatch.fnmatchcase(host.lower(), p) for p in (allow or allow_list()))


def upstream() -> tuple[str, int] | None:
    """Готовый прокси, через который идёт туннель (EGRESS_UPSTREAM=host:port); None — прямой выход."""
    raw = os.environ.get("EGRESS_UPSTREAM", "").strip()
    if not raw:
        return None
    host, _, port = raw.rpartition(":")
    return host, int(port)


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while data := await src.read(65536):
            dst.write(data)
            await dst.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        dst.close()


async def _reply(writer: asyncio.StreamWriter, status: str) -> None:
    """Ответ без туннеля и аккуратное закрытие: сначала FIN, чтобы клиент успел прочитать статус (без него Windows
    иногда обрывает соединение сбросом раньше, чем клиент дочитал ответ)."""
    writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\n\r\n".encode())
    with contextlib.suppress(ConnectionError):
        await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
        writer.close()
        await writer.wait_closed()


async def _open(target: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Соединение с целью: напрямую или туннелем CONNECT через готовый прокси."""
    up = upstream()
    if up is None:
        host, _, port = target.rpartition(":")
        return await asyncio.open_connection(host, int(port))
    reader, writer = await asyncio.open_connection(*up)
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode("latin-1")
    if " 200" not in status:
        writer.close()
        raise OSError(f"прокси {up[0]}:{up[1]} ответил «{status}»")
    return reader, writer


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), TIMEOUT_S)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
        writer.close()
        return
    parts = head.split(b"\r\n", 1)[0].decode("latin-1").split()
    if len(parts) != 3 or parts[0].upper() != "CONNECT":
        return await _reply(writer, "405 Method Not Allowed")  # только HTTPS-туннель: открытого HTTP-прокси нет
    host, _, port = parts[1].rpartition(":")
    if not port.isdigit() or not allowed(host, int(port)):
        log.warning("отказано: %s (разрешены: %s, порт 443)", parts[1], ", ".join(allow_list()))
        return await _reply(writer, "403 Forbidden")
    try:
        up_reader, up_writer = await asyncio.wait_for(_open(parts[1]), TIMEOUT_S)
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
        log.warning("%s недоступен: %s", parts[1], exc or type(exc).__name__)
        return await _reply(writer, "502 Bad Gateway")
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def serve(port: int = PORT) -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", port)
    up = upstream()
    log.info(
        "egress: :%d, разрешены %s, выход %s",
        port,
        ", ".join(allow_list()),
        f"через {up[0]}:{up[1]}" if up else "прямой",
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(serve())


if __name__ == "__main__":
    main()

"""Прямой выход канала владельца (egress.py): только CONNECT на :443 и только к разрешённым хостам — без сети."""

from __future__ import annotations

import asyncio

from oil_ouroboros import egress


def test_allow_list_matches_hosts_and_port_443_only():
    allow = ["api.telegram.org", "*.githubusercontent.com"]
    assert egress.allowed("api.telegram.org", 443, allow) and egress.allowed("RAW.githubusercontent.com", 443, allow)
    assert not egress.allowed("api.telegram.org", 80, allow) and not egress.allowed("evil.org", 443, allow)
    assert not egress.allowed("api.telegram.org.evil.org", 443, allow)


def test_proxy_refuses_plain_http_and_foreign_hosts(monkeypatch):
    monkeypatch.setenv("EGRESS_ALLOW", "api.telegram.org")

    async def ask(request: bytes) -> bytes:
        server = await asyncio.start_server(egress.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(request)
        await writer.drain()
        line = await reader.readline()
        writer.close()
        server.close()
        await server.wait_closed()
        return line

    assert b"405" in asyncio.run(ask(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n"))
    assert b"403" in asyncio.run(ask(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n"))
    assert b"403" in asyncio.run(ask(b"CONNECT api.telegram.org:22 HTTP/1.1\r\n\r\n"))


def test_tunnel_goes_through_upstream_proxy_and_keeps_the_allow_list(monkeypatch):
    """За корпоративным прокси (EGRESS_UPSTREAM) туннель идёт через него, а чужой хост всё равно не пускается."""
    monkeypatch.setenv("EGRESS_ALLOW", "api.telegram.org")
    seen: list[bytes] = []

    async def upstream_proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        seen.append(head.split(b"\r\n", 1)[0])
        ok = b"api.telegram.org:443" in head
        writer.write(b"HTTP/1.1 200 OK\r\n\r\n" if ok else b"HTTP/1.1 403 Forbidden\r\n\r\n")
        await writer.drain()
        if ok:
            writer.write(await reader.readexactly(4))  # эхо — «удалённый хост»
            await writer.drain()
        writer.close()

    async def scenario(request: bytes) -> tuple[bytes, bytes]:
        up = await asyncio.start_server(upstream_proxy, "127.0.0.1", 0)
        monkeypatch.setenv("EGRESS_UPSTREAM", f"127.0.0.1:{up.sockets[0].getsockname()[1]}")
        proxy = await asyncio.start_server(egress.handle, "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.sockets[0].getsockname()[1])
        writer.write(request)
        await writer.drain()
        status = await reader.readuntil(b"\r\n\r\n")
        echo = b""
        if b" 200 " in status:
            writer.write(b"ping")
            await writer.drain()
            echo = await reader.readexactly(4)
        writer.close()
        for s in (proxy, up):
            s.close()
            await s.wait_closed()
        return status, echo

    status, echo = asyncio.run(scenario(b"CONNECT api.telegram.org:443 HTTP/1.1\r\n\r\n"))
    assert b" 200 " in status and echo == b"ping" and seen == [b"CONNECT api.telegram.org:443 HTTP/1.1"]
    status, _ = asyncio.run(scenario(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n"))
    assert b"403" in status and len(seen) == 1  # до прокси чужой хост не доходит

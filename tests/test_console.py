"""Консоль: офлайн-демо (записанные ходы → проигрыватель, без агента и LLM), вход без паролей, разговоры по id."""

from __future__ import annotations

import importlib.util
import json
import re

from fastapi.testclient import TestClient

from oil_ouroboros.config import ROOT
from oil_ouroboros.console import demo


def _turn(tid: str, question: str, conv: str, tools: list[str]) -> dict:
    return {
        "turn_id": tid,
        "conversation_id": conv,
        "question": question,
        "answer": f"Ответ на «{question}»",
        "ms": 1000,
        "cost_usd": 0.01,
        "task": {"huge": "x" * 1000},
        "fingerprint": {"agent_version": "v1", "catalog": "c1", "settings": {"secretish": "no"}},
        "tool_calls": [
            {"tool": t, "ts": f"2026-10-22T08:00:0{i}Z", "mcp": t != "finish_task"} for i, t in enumerate(tools)
        ],
        "llm_rounds": [{"ts": "2026-10-22T08:00:00Z", "round": 1, "tokens_in": 100}],
    }


def test_export_keeps_scenario_order_titles_and_drops_task(monkeypatch, tmp_path):
    q4, q5 = demo.SCENARIOS[3][1], demo.SCENARIOS[4][1]
    saved = {
        "t5": _turn("t5", q5, "demo-a", ["research", "forecast_price", "verify_answer", "finish_task"]),
        "t4": _turn("t4", q4, "demo-a", ["research", "forecast_price", "verify_answer", "finish_task"]),
    }
    rows = [{"turn_id": "t5", "started": 2.0}, {"turn_id": "t4", "started": 1.0}]  # журнал отдаёт новые первыми
    monkeypatch.setattr(demo.turns, "listing", lambda limit, source: rows[:limit] if source == "demo" else [])
    monkeypatch.setattr(demo.turns, "get", lambda tid: saved[tid])
    rep = demo.export(tmp_path / "turns.json")
    data = json.loads((tmp_path / "turns.json").read_text(encoding="utf-8"))
    assert rep["turns"] == 2 and [t["turn_id"] for t in data["turns"]] == ["t4", "t5"]
    assert [t["title"] for t in data["turns"]] == [demo.SCENARIOS[3][0], demo.SCENARIOS[4][0]]
    assert "task" not in data["turns"][0] and data["turns"][0]["fingerprint"] == {
        "agent_version": "v1",
        "catalog": "c1",
    }


def test_demo_api_serves_replay(monkeypatch, tmp_path):
    from oil_ouroboros.console import app as console

    f = tmp_path / "turns.json"
    f.write_text(
        json.dumps(
            {
                "recorded": "2026-10-22 08:00",
                "turns": [
                    {
                        "title": "по отчётам",
                        **_turn("t1", "Вопрос 1", "c1", ["research", "verify_answer", "finish_task"]),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(demo, "REPLAY_FILE", f)
    console._replay.cache_clear()
    c = TestClient(console.app)
    lst = c.get("/api/demo").json()
    assert lst["recorded"] == "2026-10-22 08:00" and lst["turns"][0]["tools"] == ["research", "verify_answer"]
    assert c.get("/api/demo/0").json()["answer"] == "Ответ на «Вопрос 1»" and c.get("/api/demo/5").status_code == 404
    console._replay.cache_clear()


def test_repo_replay_file_has_all_demo_scenarios():
    rec = demo.replay()
    titles = [t["title"] for t in rec["turns"]]
    assert titles == [k for k, _, _ in demo.SCENARIOS]
    for t in rec["turns"]:
        assert t["answer"] and t["tool_calls"] is not None and "task" not in t


def test_conversation_is_reached_by_its_id(monkeypatch):
    """Логинов нет: новый разговор — c-<10 hex>, продолжить и прочитать его может любой, кто знает id; прежние id
    вида c-<12 hex>-<10 hex> работают как есть."""
    from oil_ouroboros.console import app as console

    started = []
    monkeypatch.setattr(
        console.threading,
        "Thread",
        lambda target, args, daemon: type("T", (), {"start": lambda s: started.append(args)})(),
    )
    c = TestClient(console.app)
    r = c.post("/api/chat", json={"question": "Прогноз Brent?"}).json()
    assert re.fullmatch(r"c-[0-9a-f]{10}", r["conversation_id"]) and re.fullmatch(r"[0-9a-f]{12}", r["turn_id"])
    old = "c-0123456789ab-0123456789"
    r2 = c.post("/api/chat", json={"question": "А WTI?", "conversation_id": old})
    assert r2.status_code == 200 and r2.json()["conversation_id"] == old and started[-1][1] == old
    saved = {"t1": {"turn_id": "t1", "conversation_id": old}, "t2": {"turn_id": "t2", "conversation_id": old}}
    monkeypatch.setattr(
        console.turns, "listing", lambda limit, source="", conversation="": [{"turn_id": "t2"}, {"turn_id": "t1"}]
    )
    monkeypatch.setattr(console.turns, "get", saved.get)
    assert [t["turn_id"] for t in c.get(f"/api/conversations/{old}").json()] == ["t1", "t2"]  # от старых к новым


def test_console_opens_without_login(monkeypatch):
    """Паролей и ролей нет: без авторизации открыты журнал ходов, параметры и запуск eval; эндпоинта «кто вошёл»
    больше нет, заголовок Basic ничего не меняет."""
    from oil_ouroboros.console import app as console

    monkeypatch.setattr(console.net, "tools_api", lambda path, params=None, body=None, timeout=60.0: {})
    monkeypatch.setattr(console, "_eval_job", {})
    monkeypatch.setattr(console.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda s: None})())
    c = TestClient(console.app)
    assert c.get("/api/turns").status_code == 200 and c.get("/api/params").status_code == 200
    assert c.post("/api/evals/run", json={"only": ["R01"]}).json() == {"started": True}
    assert c.get("/docs/guide/operations.md").status_code == 200
    assert c.get("/api/turns", headers={"Authorization": "Basic Zm9vOmJhcg=="}).status_code == 200
    assert not [r for r in console.app.routes if getattr(r, "path", "").endswith("/me")]
    r = c.get("/")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


def test_health_has_details_without_login(monkeypatch):
    """Здоровье — всем и с подробностями: недоступный шлюз виден с текстом ошибки, система — degraded."""
    from oil_ouroboros.console import app as console

    class Ouroboros:
        def health(self) -> dict:
            return {"status": "ok"}

    def gateway(path, params=None, timeout=15.0):
        raise RuntimeError("шлюз молчит")

    monkeypatch.setattr(console.net, "tools_api", lambda path, params=None, body=None, timeout=60.0: {"status": "ok"})
    monkeypatch.setattr(console.net, "gateway_api", gateway)
    monkeypatch.setattr(console.agent, "Ouroboros", Ouroboros)
    c = TestClient(console.app)
    h = c.get("/api/health").json()
    assert (
        h["status"] == "degraded"
        and h["gateway"]["status"] == "unavailable"
        and "шлюз молчит" in h["gateway"]["error"]
    )
    assert h["tools"] == {"status": "ok"} and h["ouroboros"] == {"status": "ok"}
    monkeypatch.setattr(console.net, "gateway_api", lambda path, params=None, timeout=15.0: {"status": "ok"})
    assert c.get("/api/health").json()["status"] == "ok"


def test_init_env_creates_tokens_and_names_the_ouroboros_password(monkeypatch, tmp_path, capsys):
    """scripts/init_env.py генерирует шесть внутренних токенов; логинов консоли нет, пароль UI Ouroboros — из шаблона
    (ouroboros), и скрипт печатает его вместе с адресом UI."""
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))  # init_env импортирует соседний llm_profile
    spec = importlib.util.spec_from_file_location("init_env", ROOT / "scripts" / "init_env.py")
    init_env = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(init_env)
    text, done = init_env.fill((ROOT / ".env.example").read_text(encoding="utf-8"))
    assert sorted(done) == sorted(init_env.GENERATED) and len(done) == 6  # только внутренние токены
    assert re.search(r"^TOOLS_API_TOKEN=[0-9a-f]{48}$", text, flags=re.M)
    assert re.search(r"^OUROBOROS_PASSWORD=ouroboros$", text, flags=re.M)
    (tmp_path / ".env.example").write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(init_env, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["init_env.py"])
    assert init_env.main() == 0 and (tmp_path / ".env").is_file()
    out = capsys.readouterr().out
    assert "консоль — без пароля: http://127.0.0.1:38620" in out
    assert "UI Ouroboros (профиль owner) — http://127.0.0.1:38630, пароль ouroboros" in out
    # пароль стёрт вручную: --fill его не генерирует (молча подставленный пароль снова пришлось бы искать в .env)
    env = (
        (tmp_path / ".env").read_text(encoding="utf-8").replace("OUROBOROS_PASSWORD=ouroboros", "OUROBOROS_PASSWORD=")
    )
    (tmp_path / ".env").write_text(env, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["init_env.py", "--fill"])
    assert init_env.main() == 0 and "OUROBOROS_PASSWORD=\n" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "впишите OUROBOROS_PASSWORD в .env — без него агент не стартует" in capsys.readouterr().out


def test_architecture_describes_every_container_network_and_volume():
    """Вкладка «Архитектура»: развёртывание — из docker-compose.yml, и у каждого контейнера, сети и тома
    есть «зачем»."""
    from oil_ouroboros.console import deployment

    d = deployment.describe()
    assert [s["name"] for s in d["services"] if not (s["role"] and s["why"])] == []
    assert all(n["why"] for n in d["networks"]) and all(v["why"] for v in d["volumes"])
    order = [n for step in d["order"] for n in step]  # ядро: сервисы профилей сюда не входят
    assert order.index("gateway") < order.index("tools") < order.index("ouroboros") < order.index("console")
    assert (
        "schedules" not in order
        and next(s for s in d["services"] if s["name"] == "egress")["when"] == "по профилю owner"
    )
    import yaml

    from oil_ouroboros.config import ROOT

    proxy = yaml.safe_load((ROOT / "deploy/compose/proxy.yml").read_text(encoding="utf-8"))
    assert all(s.get("x-role") for n, s in proxy["services"].items() if n != "egress")
    assert deployment._env("${OILO_BIND:-127.0.0.1}:${OILO_PORT:-38620}:8000") == "127.0.0.1:38620:8000"
    assert deployment._env("${GATEWAY_TOKEN_OUROBOROS:?задайте в .env}") == "GATEWAY_TOKEN_OUROBOROS"


def test_architecture_map_links_connect_known_parts():
    """Карта «Архитектуры»: у каждой связи оба конца — сервис compose (или дополнения proxy.yml) или внешний участник,
    и каждый сервис ядра на карте связан хотя бы с кем-то."""
    from oil_ouroboros.console import deployment

    d = deployment.describe()
    known = {s["name"] for s in d["services"]} | {e["id"] for e in d["externals"]} | {"proxy"}
    ends = {x for link in d["links"] for x in (link["from"], link["to"])}
    assert ends <= known, ends - known
    core = {s["name"] for s in d["services"] if s["when"] in ("постоянно", "разово")}
    assert core <= ends, core - ends
    assert all(link["what"] for link in d["links"])
    svc = {s["name"]: s for s in d["services"]}
    # режим «Развёртывание»: имена секретов сервиса — только его собственные (значения не показываются)
    assert {"MCP_TOKEN", "TOOLS_API_TOKEN"} <= set(svc["tools"]["secrets"]) and "OUROBOROS_PASSWORD" not in svc[
        "tools"
    ]["secrets"]
    assert "OPENROUTER_API_KEY" in svc["gateway"]["secrets"] and svc["encoder"]["secrets"] == []
    assert {"name": "gateway", "condition": "healthy"} in svc["tools"]["depends"] and svc["tools"]["health"]["test"]


def test_architecture_map_profiles_follow_compose_profiles(monkeypatch):
    """Переключатели профилей на карте сначала стоят как в этом развёртывании: COMPOSE_PROFILES из .env, без имён,
    которых нет в docker-compose.yml."""
    import yaml

    from oil_ouroboros.config import ROOT
    from oil_ouroboros.console import deployment

    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    monkeypatch.setenv("COMPOSE_PROFILES", "owner, gigachat,unknown")
    assert deployment._profiles_on(services) == ["gigachat", "owner"]
    monkeypatch.setenv("COMPOSE_PROFILES", "")
    assert deployment._profiles_on(services) == []


def test_each_service_gets_only_its_own_secrets():
    """.env общий, поэтому чужие секреты у сервиса обнулены: ключ провайдера — только у шлюза, пароль Ouroboros — у
    консоли и засева, но не у инструментов, которые читают недоверенный веб."""
    import yaml

    from oil_ouroboros.config import ROOT

    svc = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    env = {n: s.get("environment") or {} for n, s in svc.items()}
    has = lambda name, key: env[name].get(key, "") != ""  # noqa: E731
    assert has("gateway", "OPENROUTER_API_KEY") and not any(
        has(n, "OPENROUTER_API_KEY") for n in ("tools", "console", "bootstrap")
    )
    assert (
        has("tools", "MCP_TOKEN")
        and not has("tools", "OUROBOROS_PASSWORD")
        and not has("tools", "GATEWAY_TOKEN_CONSOLE")
    )
    assert (
        has("console", "OUROBOROS_PASSWORD")
        and not has("console", "MCP_TOKEN")
        and not has("console", "TAVILY_API_KEY")
    )
    assert not any(has("schedules", k) for k in ("MCP_TOKEN", "TOOLS_API_TOKEN", "TELEGRAM_BOT_TOKEN"))
    assert "OILO_BIND" not in env["console"]  # адрес публикации консоли больше не нужен: входа нет


def test_every_console_get_endpoint_answers_without_server_error(monkeypatch):
    """Все GET-адреса консоли без входа отвечают без 500: сервис tools, шлюз и Ouroboros подменены заглушками.
    Ловит ошибки, которых не видит линтер (неверный атрибут модуля в обработчике)."""
    from oil_ouroboros.console import app as console

    class Ouroboros:
        def health(self) -> dict:
            return {"status": "ok"}

    monkeypatch.setattr(console.net, "tools_api", lambda path, params=None, body=None, timeout=60.0: {})
    monkeypatch.setattr(console.net, "gateway_api", lambda path, params=None, timeout=15.0: {"calls": []})
    monkeypatch.setattr(console.agent, "Ouroboros", Ouroboros)
    c = TestClient(console.app)
    paths = [
        r.path
        for r in console.app.routes
        if "GET" in getattr(r, "methods", ()) and r.path.startswith("/api/") and "{" not in r.path
    ]
    assert "/api/params" in paths and len(paths) > 15
    failed = {p: c.get(p).status_code for p in paths}
    assert {p: s for p, s in failed.items() if s >= 500} == {}

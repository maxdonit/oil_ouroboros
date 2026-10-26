"""
Инструменты агента — графы LangGraph: четыре модуля на восемь инструментов-графов MCP (mcp/server.py; девятый,
list_reports, — обычная функция без графа):

    research, search_reports                              → research.py
    search_web                                            → search_web.py
    forecast_price, supply_shock_scenario, price_history,
    price_quote                                           → calc.py
    verify_answer                                         → verify_answer.py (текст для показа — render.py)

kit.py — сборка графа, трейс узлов, запись решений; runs.py — журнал прогонов (по нему консоль показывает ход
инструмента).
"""

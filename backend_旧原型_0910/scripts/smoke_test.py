"""端到端冒烟测试：验证三大功能在本地模拟模式下可完整跑通。

脚本会自动检测本地服务：若未启动则临时拉起 uvicorn，测试结束后自动关闭。

用法：
    python scripts/smoke_test.py
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8000"
PORT = 8000


# ---------------------------------------------------------------- 服务管理
def _port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), 1):
            return True
    except OSError:
        return False


def _wait_ready(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE}/api/system/health", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def _ensure_server() -> subprocess.Popen | None:
    """确保有可用服务；返回本脚本拉起的进程（需负责关闭）。"""
    if _wait_ready(timeout=3):
        print("检测到已运行的服务，直接复用。")
        return None
    print("未检测到服务，正在启动 uvicorn…")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT),
    )
    if not _wait_ready(timeout=30):
        proc.terminate()
        raise RuntimeError("服务启动失败")
    return proc


# ---------------------------------------------------------------- SSE 读取
def consume(client: httpx.Client, path: str) -> dict:
    counts: dict[str, int] = {}
    events: list[dict] = []
    final: dict = {}
    with client.stream("GET", f"{BASE}{path}") as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            events.append(event)
            etype = event.get("type", "?")
            counts[etype] = counts.get(etype, 0) + 1
            if etype in {"done", "error"}:
                final = event
    return {"counts": counts, "final": final, "events": events}


def find_event(events: list[dict], etype: str) -> dict:
    for event in reversed(events):
        if event.get("type") == etype:
            return event
    return {}


# ---------------------------------------------------------------- 主流程
def main() -> int:
    proc = _ensure_server()
    ok = False
    try:
        with httpx.Client(timeout=180.0) as client:
            print("== 健康检查 ==")
            print(client.get(f"{BASE}/api/system/health").json())
            cfg = client.get(f"{BASE}/api/system/config").json()
            print("运行模式:", "本地模拟" if cfg["mockMode"] else "真实模型",
                  f"({cfg['readyCount']}/{cfg['totalCount']} 就绪)")
            print("AI 列表:", [a["id"] for a in client.get(f"{BASE}/api/system/ai-list").json()["items"]])

            # -------- 模块一 --------
            print("\n== 模块一：文档 → Prompt ==")
            r = client.post(f"{BASE}/api/module1/generate",
                            data={"need": "帮我做一个面向 B 端客户的产品介绍页面文案"})
            r.raise_for_status()
            m1 = consume(client, f"/api/module1/stream/{r.json()['taskId']}")
            m1_text = find_event(m1["events"], "result").get("content", "")
            print("事件统计:", m1["counts"])
            print("最终 Prompt 长度:", len(m1_text))

            # -------- 模块二 --------
            print("\n== 模块二：多 AI 讨论 ==")
            r = client.post(
                f"{BASE}/api/module2/discussions",
                json={
                    "topic": "如何在三线城市低成本启动一个社区咖啡空间",
                    "mode": "planning",
                    "participants": ["deepseek", "claude", "zhipu", "kimi"],
                    "maxRounds": 2,
                    "untilConsensus": False,
                },
            )
            r.raise_for_status()
            m2 = consume(client, f"/api/module2/stream/{r.json()['taskId']}")
            board = m2["final"].get("board", [])
            print("事件统计:", m2["counts"])
            print("计分榜 Top3:", [(b["aiName"], b["score"]) for b in board[:3]])
            print("行动方案长度:", len(m2["final"].get("actionPlan", "")))

            # -------- 模块三 --------
            print("\n== 模块三：三级文章生成 ==")
            r = client.post(
                f"{BASE}/api/module3/generate",
                json={"topic": "远程办公对团队协作的影响", "genre": "议论文",
                      "style": "理性克制", "length": "中篇"},
            )
            r.raise_for_status()
            m3 = consume(client, f"/api/module3/stream/{r.json()['taskId']}")
            m3_text = m3["final"].get("final", "")
            print("事件统计:", m3["counts"])
            print("最终定稿长度:", len(m3_text))

            for name, res in (("模块一", m1), ("模块二", m2), ("模块三", m3)):
                if res["counts"].get("error"):
                    print(f"{name} 存在错误事件: {res['final']}")
                    return 1
            if not (m1_text and board and m3_text):
                print("存在空结果，请检查")
                return 1
        print("\n全部模块冒烟测试通过")
        ok = True
        return 0
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())

"""
沙丘终局盘面识别(云端视觉模型)
============================

支持两种截图:
  A. 结算页(settlement): 显示"胜利/失败"+ 第1-4名标签。
     -> 游戏已排好名次(含平局判定), 直接信标签, 只读 昵称/角色/分数/资源。
  B. 终局盘面(endgame_board): 牌桌画面, 无第1-4名标签(如对局结束瞬间)。
     -> 读每人 分数+资源, 由我方四级平局规则自动定名次。

四级平局规则(分数相同时, 依次比较, 分出即止):
  1) 香料(美琅脂, 橙色六角)多者靠前
  2) 帝国索 Solari(银白圆形, 钱)多者靠前
  3) 水资源多者靠前
  4) 驻守兵营士兵数多者靠前
  四项全同 -> 真正平局(并列)

recognize() 返回:
{
  "ok": True,
  "kind": "settlement" | "endgame_board",
  "players": [
    {"name","character","score","spice","solari","water","troops","rank"}, ...
  ],
  "raw": "<模型原始文本>"
}
其中 settlement 的 rank 来自游戏标签; endgame_board 的 rank 由本模块计算。
失败: {"ok": False, "error": "...", "raw": "..."}
"""

from __future__ import annotations
import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

MINIMAX_DEFAULT_BASE_URL = "https://api.minimax.io/v1"


def _clean_env_value(value: Optional[str]) -> str:
    return (value or "").strip().strip("'\"")

SYSTEM_PROMPT = """你是《沙丘:帝国(Dune: Imperium)》对局截图识别助手。
游戏每局 4 名玩家。你要先判断截图类型, 再提取信息。只输出 JSON, 不要任何解释、不要 Markdown 代码块。

【截图类型 kind】
- "settlement": 结算画面。特征: 顶部有大字"胜利"或"失败", 右侧每名玩家行首有"第1名/第2名/第3名/第4名"标签, 每行有昵称、角色名(中文)、一个大分数(发光圆章里的数字)。
- "endgame_board": 游戏牌桌画面(进行中或刚结束), 左侧竖排显示各玩家昵称与分数/资源, 但【没有】"第N名"名次标签。

【字段说明】每名玩家尽量提取:
- name: 昵称(英文/拼音原文照抄)
- character: 所属角色中文名(如"哥尼·哈莱克""雷托·厄崔迪公爵"), 读不到填 null
- score: 终局胜利点数(大分数), 整数
- spice: 香料/美琅脂数量(橙色六角形图标旁数字), 读不到填 null
- solari: 帝国索/金币数量(银白色圆形图标旁数字), 读不到填 null
- water: 水资源数量(蓝色水滴图标旁数字), 读不到填 null
- troops: 驻守兵营的士兵数(兵营/驻军数字), 读不到填 null
- rank: 仅当 kind="settlement" 时, 填游戏标注的名次(1-4); kind="endgame_board" 时一律填 null(名次由系统另算)。

【输出格式】
{"kind":"settlement","players":[
  {"name":"...","character":"...","score":13,"spice":null,"solari":null,"water":null,"troops":null,"rank":1},
  ...共4个...
]}

【要求】
- 严格 4 名玩家。读不到的数值字段填 null, 不要瞎猜。
- settlement 必须给出 rank(直接用画面里的"第N名")。
- 完全无法识别 -> {"error":"无法识别"}。
"""

USER_PROMPT = "识别这张沙丘截图, 判断类型并按 JSON 格式输出 4 名玩家信息。"


def resolve_ranks_by_tiebreak(players: list[dict]) -> list[dict]:
    """
    对 endgame_board: 用 分数 + 四级平局规则 计算名次, 写回每个 player 的 rank。
    并列(四项全同)时 rank 相同。
    比较键: 分数 -> 香料 -> 金币 -> 水 -> 兵营, 全部"多者靠前"。
    None 视为 0 参与比较(但保留原值用于记录)。
    """
    def key(p):
        g = lambda k: p.get(k) if isinstance(p.get(k), (int, float)) else 0
        # 取负: 数值大的排前
        return (-g("score"), -g("spice"), -g("solari"), -g("water"), -g("troops"))

    ordered = sorted(players, key=key)
    rank = 0
    prev_key = object()
    for i, p in enumerate(ordered):
        k = key(p)
        if k != prev_key:
            rank = i + 1          # 标准竞赛排名: 并列后跳号
            prev_key = k
        p["rank"] = rank
    return ordered


class VisionRecognizer:
    def __init__(self, api_key: Optional[str] = None,
                 model: Optional[str] = None,
                 base_url: Optional[str] = None,
                 timeout: int = 60,
                 max_tokens: int = 1500) -> None:
        self.api_key = _clean_env_value(api_key or os.environ.get("MINIMAX_API_KEY"))
        if not self.api_key:
            raise RuntimeError("请设置环境变量 MINIMAX_API_KEY")
        self.model = _clean_env_value(
            model or os.environ.get("MINIMAX_MODEL") or "MiniMax-M3"
        )
        self.base_url = (
            _clean_env_value(base_url or os.environ.get("MINIMAX_BASE_URL"))
            or MINIMAX_DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens

    @staticmethod
    def _media_type(image_bytes: bytes) -> str:
        if image_bytes[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            return "image/webp"
        return "image/jpeg"

    def _chat_completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    @staticmethod
    def _extract_text(content) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts).strip()
        return ""

    def recognize(self, image_bytes: bytes) -> dict:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        media_type = self._media_type(image_bytes)
        payload = {
            "model": self.model,
            "max_completion_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": USER_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{b64}",
                            },
                        },
                    ],
                },
            ],
        }
        req = urllib.request.Request(
            self._chat_completions_url(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 401:
                return {
                    "ok": False,
                    "error": (
                        "MiniMax 鉴权失败: API Key 无效或服务未加载最新 "
                        "MINIMAX_API_KEY。请检查 Key 是否来自 MiniMax 控制台、"
                        "是否复制完整, 并重启机器人服务。"
                    ),
                    "raw": body,
                }
            return {"ok": False,
                    "error": f"MiniMax 调用失败 HTTP {e.code}: {body[:500]}",
                    "raw": body}
        except Exception as e:
            return {"ok": False, "error": f"MiniMax 调用失败: {e}", "raw": ""}

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return {"ok": False,
                    "error": f"MiniMax 响应不是 JSON: {e}", "raw": body}

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return {"ok": False, "error": "MiniMax 响应缺少 choices", "raw": body}

        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        text = self._extract_text(message.get("content"))
        if not text:
            return {"ok": False, "error": "MiniMax 响应缺少文本内容", "raw": body}
        return self._parse(text)

    @staticmethod
    def _json_object_candidates(text: str):
        for start in range(len(text)):
            if text[start] != "{":
                continue
            depth = 0
            in_string = False
            escaped = False
            for pos in range(start, len(text)):
                ch = text[pos]
                if escaped:
                    escaped = False
                    continue
                if ch == "\\":
                    escaped = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        yield text[start:pos + 1]
                        break

    @staticmethod
    def _parse(text: str) -> dict:
        cleaned = re.sub(r"^```[a-zA-Z]*|```$", "", text.strip()).strip()
        cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
        errors = []
        for json_text in VisionRecognizer._json_object_candidates(cleaned):
            try:
                data = json.loads(json_text)
                break
            except json.JSONDecodeError as e:
                errors.append(str(e))
        else:
            if errors:
                return {"ok": False,
                        "error": f"JSON 解析失败: {errors[-1]}", "raw": text}
            return {"ok": False, "error": "未返回 JSON", "raw": text}

        if "error" in data:
            return {"ok": False, "error": str(data["error"]), "raw": text}

        kind = data.get("kind")
        if kind not in ("settlement", "endgame_board"):
            return {"ok": False, "error": f"未知截图类型: {kind}", "raw": text}

        players = data.get("players")
        if not isinstance(players, list) or len(players) != 4:
            return {"ok": False, "error": "玩家数量不是 4", "raw": text}

        norm = []
        for p in players:
            name = str(p.get("name", "")).strip()
            if not name:
                return {"ok": False, "error": "存在空昵称", "raw": text}
            def num(v):
                return v if isinstance(v, (int, float)) else None
            norm.append({
                "name": name,
                "character": (str(p["character"]).strip()
                              if p.get("character") not in (None, "") else None),
                "score": num(p.get("score")),
                "spice": num(p.get("spice")),
                "solari": num(p.get("solari")),
                "water": num(p.get("water")),
                "troops": num(p.get("troops")),
                "rank": p.get("rank") if p.get("rank") in (1, 2, 3, 4) else None,
            })

        if kind == "settlement":
            ranks = [p["rank"] for p in norm]
            if sorted(r for r in ranks if r) != [1, 2, 3, 4]:
                return {"ok": False,
                        "error": f"结算页名次不完整: {ranks}", "raw": text}
            norm.sort(key=lambda p: p["rank"])
        else:
            # endgame_board: 需要分数, 再按四级规则定名次
            if any(p["score"] is None for p in norm):
                return {"ok": False,
                        "error": "终局盘面缺少分数, 无法定名次", "raw": text}
            norm = resolve_ranks_by_tiebreak(norm)

        return {"ok": True, "kind": kind, "players": norm, "raw": text}

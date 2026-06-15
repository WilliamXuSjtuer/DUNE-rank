"""
服务层
======

把"识别 -> 映射 -> 待确认 -> 入榜"串起来, 并提供机器人需要的所有操作。

设计要点:
- 规则6(截图为准 + 漏发不记): 识别成功后先进入"待确认队列", 由发起者或管理员
  确认无误再正式入榜; 避免识别错误污染榜单。可通过 config 关闭直接入榜。
- 所有状态持久化到 data/ 目录, 重启不丢。
"""

from __future__ import annotations
from datetime import date, datetime
import os
import json
import time
import uuid

from .ladder import Ladder
from .registry import Registry
from .vision import VisionRecognizer


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _res_str(p: dict) -> str:
    """把资源拼成一行: 香料/金币/水/兵, None 显示为 -。"""
    def v(k):
        x = p.get(k)
        return str(x) if isinstance(x, (int, float)) else "-"
    return f"香料{v('spice')} 金币{v('solari')} 水{v('water')} 兵{v('troops')}"


def _render_recognition(record: dict) -> str:
    kind_label = "结算页" if record["kind"] == "settlement" else "终局盘面(自动定名次)"
    lines = [f"识别结果 (编号 {record['token']}) — {kind_label}",
             f"时间: {record['record_time']}"]
    for p in record["players"]:
        ch = f" [{p['character']}]" if p.get("character") else ""
        score = p.get("score")
        score_str = f"{score}分" if score is not None else "?分"
        lines.append(f"  第{p['rank']}名 {p['name']}{ch} {score_str} | {_res_str(p)}")
    lines.append(f"确认入榜请回复: 确认 {record['token']}")
    lines.append(f"识别有误请回复: 取消 {record['token']}")
    return "\n".join(lines)


class DuneService:
    def __init__(self, data_dir: str, vision: VisionRecognizer | None = None,
                 require_confirm: bool = True) -> None:
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.ladder_path = os.path.join(data_dir, "ladder.json")
        self.registry_path = os.path.join(data_dir, "registry.json")
        self.pending_path = os.path.join(data_dir, "pending.json")
        self.history_path = os.path.join(data_dir, "history.jsonl")

        self.ladder = Ladder.load(self.ladder_path)
        self.registry = Registry.load(self.registry_path)
        self.pending: dict[str, dict] = self._load_pending()
        self.vision = vision
        self.require_confirm = require_confirm

    # ---------- 持久化 ----------
    def _save_all(self) -> None:
        self.ladder.save(self.ladder_path)
        self.registry.save(self.registry_path)
        self._save_pending()

    def _load_pending(self) -> dict:
        try:
            with open(self.pending_path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _refresh_pending(self) -> None:
        """Reload pending confirmations so parallel bot instances do not use stale memory."""
        self.pending = self._load_pending()

    def _save_pending(self) -> None:
        with open(self.pending_path, "w", encoding="utf-8") as f:
            json.dump(self.pending, f, ensure_ascii=False, indent=2)

    def _pending_missing_msg(self, token: str) -> str:
        if not self.pending:
            return f"找不到编号 {token}。当前没有待确认记录。"
        tokens = "、".join(sorted(self.pending.keys()))
        return f"找不到编号 {token}。当前待确认编号: {tokens}"

    def _log_history(self, entry: dict) -> None:
        entry["ts"] = time.time()
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---------- 识别一张截图 ----------
    def recognize_image(self, image_bytes: bytes, submitter: str) -> dict:
        """识别截图 -> 生成一条待确认记录。返回给机器人展示。"""
        if self.vision is None:
            return {"ok": False, "msg": "未配置视觉模型"}
        res = self.vision.recognize(image_bytes)
        if not res["ok"]:
            return {"ok": False, "msg": f"识别失败: {res.get('error')}"}

        players = sorted(res["players"], key=lambda p: (p["rank"], -(p.get("score") or 0)))
        now = time.time()
        token = uuid.uuid4().hex[:6]
        record = {
            "token": token,
            "submitter": submitter,
            "kind": res["kind"],          # settlement | endgame_board
            "players": players,           # 含 name/character/score/spice/solari/water/troops/rank
            "date": date.today().isoformat(),
            "created": now,
            "record_time": _fmt_time(now),  # 截图记录时间(可读)
        }

        if not self.require_confirm:
            return self._commit(record)

        self._refresh_pending()
        self.pending[token] = record
        self._save_pending()
        return {"ok": True, "msg": _render_recognition(record), "token": token}

    # ---------- 确认入榜 ----------
    def confirm(self, token: str) -> dict:
        token = token.strip().lower()
        self._refresh_pending()
        record = self.pending.get(token)
        if not record:
            return {"ok": False, "msg": self._pending_missing_msg(token)}
        out = self._commit(record)
        self.pending.pop(token, None)
        self._save_pending()
        return out

    def cancel(self, token: str) -> dict:
        token = token.strip().lower()
        self._refresh_pending()
        if self.pending.pop(token, None):
            self._save_pending()
            return {"ok": True, "msg": f"已取消 {token}"}
        return {"ok": False, "msg": self._pending_missing_msg(token)}

    def _commit(self, record: dict) -> dict:
        results = []
        detail = []                       # 完整对局明细(存档用)
        for p in record["players"]:
            pid = self.registry.resolve(p["name"])
            results.append((pid, p["name"], p["rank"]))
            detail.append({**p, "pid": pid})
        on_date = date.fromisoformat(record["date"])
        out = self.ladder.apply_match(results, on_date=on_date)

        # 存一条完整对局明细(玩家/角色/分数/资源/顺位/时间)
        self._log_history({
            "type": "match",
            "kind": record.get("kind"),
            "record_time": record.get("record_time"),
            "submitter": record.get("submitter"),
            "players": detail,
            "result": out,
        })
        self._save_all()

        if not out["recorded"]:
            return {"ok": False, "msg": f"未入榜: {out['reason']}"}
        msg = [f"✅ 已入榜 — {out['reason']}", f"胜者: {out['winner']}"]
        if out.get("entered"):
            msg.append(f"🎉 {out['entered']} 首胜, 进入 T 榜!")
        if out["changes"]:
            for c in out["changes"]:
                frm = "榜外" if c["from"] is None else f"T{c['from']}"
                to = "榜外" if c["to"] is None else f"T{c['to']}"
                msg.append(f"  {c['name']}: {frm} → {to}")
        elif not out.get("entered"):
            msg.append("  (无升降级变化)")
        return {"ok": True, "msg": "\n".join(msg)}

    # ---------- 榜单 ----------
    def board_text(self, max_tier: int | None = None) -> str:
        return self.ladder.render_board(max_tier)

    # ---------- 查询最近对局明细 ----------
    def recent_matches(self, n: int = 3) -> str:
        """读取最近 n 局完整明细(玩家/角色/分数/资源/顺位/时间)。"""
        try:
            with open(self.history_path, encoding="utf-8") as f:
                lines = [json.loads(x) for x in f if x.strip()]
        except FileNotFoundError:
            return "暂无对局记录"
        matches = [e for e in lines if e.get("type") == "match"
                   and e.get("result", {}).get("recorded")]
        if not matches:
            return "暂无已入榜对局"
        out = []
        for e in matches[-n:][::-1]:
            kind = "结算页" if e.get("kind") == "settlement" else "终局盘面"
            out.append(f"🕐 {e.get('record_time','?')} ({kind})")
            for p in sorted(e["players"], key=lambda x: x.get("rank") or 9):
                ch = f" [{p['character']}]" if p.get("character") else ""
                sc = p.get("score")
                out.append(f"  第{p.get('rank','?')}名 {p['name']}{ch} "
                           f"{sc if sc is not None else '?'}分 | {_res_str(p)}")
            out.append("")
        return "\n".join(out).strip()

    # ---------- 管理员: 结算 ----------
    def settle(self) -> dict:
        out = self.ladder.settle()
        self._log_history({"type": "settle", "result": {"kept": out["kept"],
                          "removed": len(out["removed"])}})
        self._save_all()
        return {"ok": True,
                "msg": f"结算完成: 保留 {out['kept']} 人(T1-T10), "
                       f"移除 {len(out['removed'])} 人。\n\n" + self.board_text(10)}

    # ---------- 管理员: 别名/合并/改名 ----------
    def admin_merge(self, keep_name: str, drop_name: str) -> dict:
        keep = self.registry.resolve(keep_name, auto_create=False)
        drop = self.registry.resolve(drop_name, auto_create=False)
        if not keep or not drop:
            return {"ok": False, "msg": "有玩家不存在"}
        if not self.registry.merge(keep, drop):
            return {"ok": False, "msg": "合并失败"}
        # 榜单层: 把 drop 的位置删掉(保留 keep)
        self.ladder.players.pop(drop, None)
        self._save_all()
        return {"ok": True, "msg": f"已将「{drop_name}」并入「{keep_name}」"}

    def admin_set_tier(self, name: str, tier: int) -> dict:
        name = name.strip()
        if not name:
            return {"ok": False, "msg": "玩家名不能为空"}
        if not 1 <= tier <= 10:
            return {"ok": False, "msg": "T级必须在 1-10 之间"}

        pid = self.registry.resolve(name)
        changed = self.ladder.set_player_tier(pid, name, tier)
        self._log_history({
            "type": "admin_set_tier",
            "name": name,
            "pid": pid,
            "from": changed["from"],
            "to": changed["to"],
            "requested": tier,
        })
        self._save_all()

        frm = "榜外" if changed["from"] is None else f"T{changed['from']}"
        msg = f"已设置「{name}」: {frm} → T{changed['to']}"
        if changed["to"] != tier:
            msg += f"\n提示: 因规则要求无悬空层, 请求的 T{tier} 已压缩为 T{changed['to']}。"
        return {"ok": True, "msg": msg}

    def admin_remove_from_board(self, name: str) -> dict:
        name = name.strip()
        if not name:
            return {"ok": False, "msg": "玩家名不能为空"}
        pid = self.registry.resolve(name, auto_create=False)
        if not pid:
            return {"ok": False, "msg": f"找不到玩家「{name}」"}

        changed = self.ladder.remove_player_from_board(pid)
        if not changed["removed"]:
            return {"ok": False, "msg": f"「{name}」不在 T 榜上"}

        self._log_history({
            "type": "admin_remove_from_board",
            "name": name,
            "pid": pid,
            "from": changed["from"],
        })
        self._save_all()
        return {"ok": True, "msg": f"已将「{name}」从 T{changed['from']} 移出 T 榜"}

    def admin_clear_board(self) -> dict:
        out = self.ladder.clear_board()
        self._log_history({"type": "admin_clear_board", "removed": out["removed"]})
        self._save_all()
        return {"ok": True,
                "msg": f"已清空 T 榜, 共移出 {out['removed']} 人。玩家注册表和历史明细已保留。"}

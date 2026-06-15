"""
沙丘终局 T 榜核心引擎
==================

规则:
0. 不在榜玩家首胜后进入当前最低 T 级垫底; 不因新人入榜自动新开下一层
1. 普通对局: 胜者若为本局四人中 T 级最低(可并列), 升一级
2. 某一级 0 人时, 下一级整体顶上, 无悬空层
3. 全 T1 对局: 胜者保留 T1, 本局其余 3 人降到 T2, 原 T2->T3, 依次后推一级
4. 同一级内按"升级先后顺序"排序, 越晚升上来排越后
5. 同样四个人(人员集合)一天只算前三把
6. 截图为准(上层保证)
7. 结算只保留 T1-T10
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional
import json


@dataclass
class Player:
    pid: str          # 唯一 ID
    name: str         # 显示昵称
    tier: int         # 所在 T 级, 1 = 最高 (未进榜时无意义)
    order_seq: int    # 进入当前 tier 的全局序号, 越大越晚 -> 排越后
    on_board: bool = False   # 是否已进入 T 榜(需首胜才进)

    def to_dict(self) -> dict:
        return asdict(self)


class Ladder:
    def __init__(self) -> None:
        self.players: dict[str, Player] = {}
        self._seq: int = 0
        self._daily_counts: dict[str, int] = {}   # "date|sorted_pids" -> count
        self.winner_t1_position: str = "last"     # "last" 或 "first"

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            "players": {pid: p.to_dict() for pid, p in self.players.items()},
            "seq": self._seq,
            "daily_counts": self._daily_counts,
            "winner_t1_position": self.winner_t1_position,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Ladder":
        lad = cls()
        players = {}
        for pid, pd in d.get("players", {}).items():
            # 兼容旧数据: 无 on_board 字段的视为已在榜
            if "on_board" not in pd:
                pd = {**pd, "on_board": True}
            players[pid] = Player(**pd)
        lad.players = players
        lad._seq = d.get("seq", 0)
        lad._daily_counts = d.get("daily_counts", {})
        lad.winner_t1_position = d.get("winner_t1_position", "last")
        return lad

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "Ladder":
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return cls()

    # ---------- 内部工具 ----------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _max_tier(self) -> int:
        return max((p.tier for p in self.players.values() if p.on_board), default=0)

    def _ensure_player(self, pid: str, name: str) -> Player:
        """登记玩家(不自动进榜)。新玩家 on_board=False, 需首胜才进榜。"""
        if pid in self.players:
            self.players[pid].name = name
            return self.players[pid]
        p = Player(pid=pid, name=name, tier=0, order_seq=0, on_board=False)
        self.players[pid] = p
        return p

    def _enter_board(self, player: Player) -> None:
        """新规则: 玩家首胜后进入 T 榜, 落在当前最低级垫底。"""
        bottom = self._max_tier() or 1
        player.on_board = True
        player.tier = bottom
        player.order_seq = self._next_seq()

    def _compact(self) -> None:
        """消除悬空层(规则2): 若某级 0 人, 其下各级整体上移。仅处理在榜玩家。"""
        on_board = [p for p in self.players.values() if p.on_board]
        present = sorted({p.tier for p in on_board})
        remap = {old: new for new, old in enumerate(present, start=1)}
        for p in on_board:
            p.tier = remap[p.tier]

    # ---------- 每日去重(规则5) ----------

    def _daily_key(self, on_date: date, pids: list[str]) -> str:
        return on_date.isoformat() + "|" + ",".join(sorted(pids))

    def daily_count(self, on_date: date, pids: list[str]) -> int:
        return self._daily_counts.get(self._daily_key(on_date, pids), 0)

    def can_record(self, on_date: date, pids: list[str]) -> bool:
        return self.daily_count(on_date, pids) < 3

    def _bump_daily(self, on_date: date, pids: list[str]) -> None:
        k = self._daily_key(on_date, pids)
        self._daily_counts[k] = self._daily_counts.get(k, 0) + 1

    # ---------- 核心: 应用一局 ----------

    def apply_match(
        self,
        results: list[tuple[str, str, int]],   # [(pid, name, rank)], rank 1 = 胜者
        on_date: Optional[date] = None,
        force: bool = False,
    ) -> dict:
        on_date = on_date or date.today()
        pids = [r[0] for r in results]

        if len(set(pids)) != 4:
            return {"recorded": False, "reason": "需要正好 4 名不同玩家", "changes": []}

        if not force and not self.can_record(on_date, pids):
            return {
                "recorded": False,
                "reason": f"该四人组合今日已记满 3 把, 本局不计",
                "changes": [],
            }

        for pid, name, _ in results:
            self._ensure_player(pid, name)

        results_sorted = sorted(results, key=lambda r: r[2])
        winner = self.players[results_sorted[0][0]]
        match_players = [self.players[pid] for pid, _, _ in results]
        before = {p.pid: (p.tier if p.on_board else None) for p in match_players}

        entered = None   # 本局是否有新人首胜进榜

        if not winner.on_board:
            # 新规则: 胜者尚未进榜 -> 首胜进当前最低 T 级垫底, 不新开一层
            self._enter_board(winner)
            entered = winner.name
            rule = "新规则: 胜者首胜, 进入当前最低 T 级垫底"
        else:
            # 升降级只看本局"在榜"玩家的 T 级
            board_tiers = [p.tier for p in match_players if p.on_board]
            all_t1 = len(board_tiers) >= 1 and all(t == 1 for t in board_tiers) \
                and len(board_tiers) == 4
            if all_t1:
                self._apply_all_t1(winner, match_players)
                rule = "规则3: 全 T1 对局, 胜者留 T1, 余者降级, 其余级顺延"
            else:
                # "T 级最低" = 段位最低 = 在榜玩家里 tier 数字最大(可并列)
                lowest_tier = max(board_tiers)
                if winner.tier == lowest_tier:
                    self._promote(winner)
                    rule = "规则1: 胜者为本局最低级, 升一级"
                else:
                    rule = "胜者非本局最低级, 不升级"

        self._compact()
        self._bump_daily(on_date, pids)

        changes = []
        for p in match_players:
            now_tier = p.tier if p.on_board else None
            if before[p.pid] != now_tier:
                changes.append({"name": p.name,
                                "from": before[p.pid], "to": now_tier})

        return {"recorded": True, "reason": rule, "changes": changes,
                "winner": winner.name, "entered": entered}

    def _promote(self, player: Player) -> None:
        """普通升级(规则1): 上移一级, 取新序号 -> 排新级末位。"""
        if player.tier > 1:
            player.tier -= 1
            player.order_seq = self._next_seq()

    def _apply_all_t1(self, winner: Player, match_players: list[Player]) -> None:
        """
        全 T1 对局结算(规则3):
        - 本局其余 3 人 -> T2
        - 原所有 tier>=2 的玩家整体 +1 (T2->T3, T3->T4, ...) 给位
        - 胜者保留 T1
        实现顺序: 先把所有 tier>=2 的人 +1, 腾出 T2; 再把 3 名败者放进 T2;
        胜者刷新序号决定其在 T1 内的位置。
        """
        losers = [p for p in match_players if p.pid != winner.pid]

        # 1) 给 T2 腾位: 现有在榜且 tier>=2 全体 +1
        for p in self.players.values():
            if p.on_board and p.tier >= 2:
                p.tier += 1
        # 2) 三名败者放入 T2, 保留它们原有相对先后(按 order_seq)
        for p in sorted(losers, key=lambda x: x.order_seq):
            p.tier = 2
            p.order_seq = self._next_seq()
        # 3) 胜者保留 T1, 决定其在 T1 内位置
        if self.winner_t1_position == "last":
            winner.order_seq = self._next_seq()
        # "first" 则不改 seq(保持原相对靠前); 也可设为极小值
        # 这里若要绝对置顶, 取消注释下一行:
        # else: winner.order_seq = -1

    # ---------- 结算(规则7) ----------

    def settle(self) -> dict:
        """管理员结算: 只保留 T1-T10 的在榜玩家。未进榜玩家保留(尚未进榜)。"""
        self._compact()
        removed = [p.to_dict() for p in self.players.values()
                   if p.on_board and p.tier > 10]
        removed_pids = {p["pid"] for p in removed}
        self.players = {pid: p for pid, p in self.players.items()
                        if pid not in removed_pids}
        self._compact()
        kept = sum(1 for p in self.players.values() if p.on_board)
        return {"removed": removed, "kept": kept}

    # ---------- 展示 ----------

    def board(self, max_tier: Optional[int] = None) -> list[dict]:
        """返回排好序的榜单: [{tier, players:[name,...]}]。"""
        self._compact()
        top = max_tier or self._max_tier()
        result = []
        for t in range(1, top + 1):
            members = [p for p in self.players.values() if p.tier == t]
            members.sort(key=lambda p: p.order_seq)
            result.append({"tier": t, "players": [m.name for m in members]})
        return result

    def render_board(self, max_tier: Optional[int] = None) -> str:
        lines = ["📊 沙丘终局 T 榜"]
        for row in self.board(max_tier):
            names = "、".join(row["players"]) if row["players"] else "(空)"
            lines.append(f"T{row['tier']}: {names}")
        return "\n".join(lines)

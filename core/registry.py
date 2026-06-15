"""
玩家注册表
=========

视觉模型识别出来的是"昵称文本", 但榜单需要稳定 ID(否则改名/识别误差会变成新玩家)。
这里维护 昵称别名 -> 稳定 pid 的映射。

- 第一次见到某昵称时, 自动生成一个 pid, 并把该昵称登记为它的别名。
- 之后同一昵称命中同一 pid。
- 管理员可以手动合并别名(同一个人多个名字)或重命名。
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
import json
import uuid


@dataclass
class PlayerIdentity:
    pid: str
    canonical: str            # 主显示名
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Registry:
    def __init__(self) -> None:
        self.by_pid: dict[str, PlayerIdentity] = {}
        self.alias_index: dict[str, str] = {}   # normalized alias -> pid

    @staticmethod
    def _norm(name: str) -> str:
        return name.strip().lower()

    def resolve(self, name: str, auto_create: bool = True) -> str | None:
        key = self._norm(name)
        if key in self.alias_index:
            return self.alias_index[key]
        if not auto_create:
            return None
        pid = "p_" + uuid.uuid4().hex[:8]
        ident = PlayerIdentity(pid=pid, canonical=name.strip(), aliases=[name.strip()])
        self.by_pid[pid] = ident
        self.alias_index[key] = pid
        return pid

    def display_name(self, pid: str) -> str:
        ident = self.by_pid.get(pid)
        return ident.canonical if ident else pid

    def add_alias(self, pid: str, alias: str) -> bool:
        if pid not in self.by_pid:
            return False
        key = self._norm(alias)
        if key in self.alias_index and self.alias_index[key] != pid:
            return False   # 已属于别人, 需先解绑
        self.by_pid[pid].aliases.append(alias.strip())
        self.alias_index[key] = pid
        return True

    def merge(self, keep_pid: str, drop_pid: str) -> bool:
        """把 drop_pid 的所有别名并入 keep_pid。榜单层需同步迁移。"""
        if keep_pid not in self.by_pid or drop_pid not in self.by_pid:
            return False
        drop = self.by_pid.pop(drop_pid)
        for a in drop.aliases:
            self.alias_index[self._norm(a)] = keep_pid
            if a not in self.by_pid[keep_pid].aliases:
                self.by_pid[keep_pid].aliases.append(a)
        return True

    def rename(self, pid: str, new_canonical: str) -> bool:
        if pid not in self.by_pid:
            return False
        self.by_pid[pid].canonical = new_canonical.strip()
        self.add_alias(pid, new_canonical)
        return True

    # ---- 序列化 ----
    def to_dict(self) -> dict:
        return {"by_pid": {pid: i.to_dict() for pid, i in self.by_pid.items()},
                "alias_index": self.alias_index}

    @classmethod
    def from_dict(cls, d: dict) -> "Registry":
        r = cls()
        r.by_pid = {pid: PlayerIdentity(**i) for pid, i in d.get("by_pid", {}).items()}
        r.alias_index = d.get("alias_index", {})
        return r

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "Registry":
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return cls()

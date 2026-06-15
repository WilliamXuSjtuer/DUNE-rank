import shutil
import sys

sys.path.insert(0, ".")

from core.service import DuneService


def names(board):
    return {r["tier"]: r["players"] for r in board}


tmp = "/tmp/dune_admin_test"
shutil.rmtree(tmp, ignore_errors=True)

svc = DuneService(tmp, vision=None, require_confirm=True)

out = svc.admin_set_tier("A", 1)
assert out["ok"], out
out = svc.admin_set_tier("B", 2)
assert out["ok"], out
assert names(svc.ladder.board()) == {1: ["A"], 2: ["B"]}, svc.ladder.board()

out = svc.admin_set_tier("C", 11)
assert not out["ok"], out

out = svc.admin_remove_from_board("A")
assert out["ok"], out
assert names(svc.ladder.board()) == {1: ["B"]}, svc.ladder.board()

svc2 = DuneService(tmp, vision=None, require_confirm=True)
assert names(svc2.ladder.board()) == {1: ["B"]}, svc2.ladder.board()

out = svc2.admin_clear_board()
assert out["ok"], out
assert out["msg"].startswith("已清空 T 榜, 共移出 1 人。"), out
assert svc2.ladder.board() == [], svc2.ladder.board()

svc3 = DuneService(tmp, vision=None, require_confirm=True)
assert svc3.ladder.board() == [], svc3.ladder.board()

print("管理员手动改榜/清空 OK")

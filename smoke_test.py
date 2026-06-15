import sys; sys.path.insert(0,'.')
import shutil, os
from core.service import DuneService

class FakeVision:
    def __init__(self, players): self.players=players
    def recognize(self, image_bytes):
        return {"ok":True,"kind":"settlement","players":self.players,"raw":"fake"}

tmp="/tmp/dune_smoke"
shutil.rmtree(tmp, ignore_errors=True)

fv = FakeVision([{"name":"阿杰","character":"哥尼·哈莱克","score":13,"spice":5,"solari":2,"water":1,"troops":3,"rank":1},
                 {"name":"小美","character":"穆阿迪布","score":10,"spice":None,"solari":None,"water":None,"troops":None,"rank":2},
                 {"name":"老王","character":None,"score":8,"spice":None,"solari":None,"water":None,"troops":None,"rank":3},
                 {"name":"阿强","character":None,"score":6,"spice":None,"solari":None,"water":None,"troops":None,"rank":4}])
stale_svc = DuneService(tmp, vision=fv, require_confirm=True)
svc = DuneService(tmp, vision=fv, require_confirm=True)

# 提交截图 -> 待确认
r1 = svc.recognize_image(b"\xff\xd8\xff fake jpg", submitter="u1")
print("=== 识别 ==="); print(r1["msg"]); print()
token = r1["token"]

# 确认入榜
r2 = stale_svc.confirm(token)
print("=== 确认 ==="); print(r2["msg"]); print()

# 查榜
print("=== 榜单 ==="); print(stale_svc.board_text()); print()

# 重启后数据仍在
svc2 = DuneService(tmp, vision=fv, require_confirm=True)
print("=== 重启后榜单 ==="); print(svc2.board_text()); print()

# 结算
print("=== 结算 ==="); print(svc2.settle()["msg"])

import sys
from datetime import date
sys.path.insert(0, "/home/claude/dune_bot")
from core.ladder import Ladder

def names(board):
    return {r["tier"]: r["players"] for r in board}

# --- 规则1: 普通对局, 胜者为本局最低级则升一级 ---
lad = Ladder()
# 手动布置: A=T1, B=T2, C=T3, D=T3
lad._ensure_player("A","A"); lad.players["A"].tier=1; lad.players["A"].on_board=True
lad._ensure_player("B","B"); lad.players["B"].tier=2; lad.players["B"].on_board=True
lad._ensure_player("C","C"); lad.players["C"].tier=3; lad.players["C"].on_board=True
lad._ensure_player("D","D"); lad.players["D"].tier=3; lad.players["D"].on_board=True
lad._compact()
# D 赢(D 是最低级之一 T3) -> D 升到 T2
r = lad.apply_match([("D","D",1),("A","A",2),("B","B",3),("C","C",4)], on_date=date(2025,1,1))
b = names(lad.board())
assert lad.players["D"].tier == 2, ("规则1失败", lad.players["D"].tier)
print("规则1 OK: 最低级胜者升级 ->", lad.render_board())

# --- 胜者非最低级, 不升 ---
lad2 = Ladder()
for n,t in [("A",1),("B",2),("C",3),("D",3)]:
    lad2._ensure_player(n,n); lad2.players[n].tier=t; lad2.players[n].on_board=True
lad2._compact()
r = lad2.apply_match([("A","A",1),("B","B",2),("C","C",3),("D","D",4)], on_date=date(2025,1,1))
assert lad2.players["A"].tier == 1
print("非最低级不升 OK")

# --- 规则2: 悬空层消除 ---
lad3 = Ladder()
for n,t in [("A",1),("B",3),("C",3),("D",5)]:
    lad3._ensure_player(n,n); lad3.players[n].tier=t; lad3.players[n].on_board=True
lad3._compact()
b = names(lad3.board())
assert set(b.keys()) == {1,2,3}, ("规则2失败", b)
print("规则2 OK: 悬空层消除 ->", lad3.render_board().replace("\n"," | "))

# --- 规则3: 全 T1 对局 ---
lad4 = Ladder()
# 8 人都是 T1, 其中 4 人对局
for n in ["A","B","C","D","E","F","G","H"]:
    lad4._ensure_player(n,n); lad4.players[n].tier=1; lad4.players[n].on_board=True
# 另外有原 T2 玩家
for n in ["X","Y"]:
    lad4._ensure_player(n,n); lad4.players[n].tier=2; lad4.players[n].on_board=True
lad4._compact()
# A B C D 对局, A 胜
r = lad4.apply_match([("A","A",1),("B","B",2),("C","C",3),("D","D",4)], on_date=date(2025,2,1))
# A 留 T1; B C D -> T2; 原 T2(X,Y) -> T3; 未参赛 T1(E,F,G,H)仍 T1
assert lad4.players["A"].tier == 1, lad4.players["A"].tier
assert all(lad4.players[n].tier==2 for n in ["B","C","D"]), [lad4.players[n].tier for n in "BCD"]
assert all(lad4.players[n].tier==3 for n in ["X","Y"]), [lad4.players[n].tier for n in ["X","Y"]]
assert all(lad4.players[n].tier==1 for n in ["E","F","G","H"])
print("规则3 OK:\n" + lad4.render_board())

# --- 规则4: 同级按升级先后排序 ---
# 在 lad4 中 B,C,D 同时进 T2, order_seq 递增; E~H 仍 T1
b = lad4.board()
t2 = [r for r in b if r["tier"]==2][0]["players"]
assert t2 == ["B","C","D"], t2  # 按放入顺序
print("规则4 OK: T2 顺序", t2)

# --- 规则5: 一天同四人前三把有效 ---
lad5 = Ladder()
for n in ["A","B","C","D"]:
    lad5._ensure_player(n,n); lad5.players[n].tier=2; lad5.players[n].on_board=True
lad5._compact()
d = date(2025,3,1)
res = [("A","A",1),("B","B",2),("C","C",3),("D","D",4)]
o1 = lad5.apply_match(res, on_date=d); assert o1["recorded"]
o2 = lad5.apply_match(res, on_date=d); assert o2["recorded"]
o3 = lad5.apply_match(res, on_date=d); assert o3["recorded"]
o4 = lad5.apply_match(res, on_date=d); assert not o4["recorded"], "第4把应不计"
print("规则5 OK: 第4把被拒 ->", o4["reason"])
# 换一天可继续
o5 = lad5.apply_match(res, on_date=date(2025,3,2)); assert o5["recorded"]
print("规则5 OK: 次日可继续")

# --- 规则7: 结算只留 T1-T10 ---
lad6 = Ladder()
for i in range(60):
    n=f"P{i}"; lad6._ensure_player(n,n); lad6.players[n].tier=(i//4)+1
lad6._compact()
out = lad6.settle()
assert lad6._max_tier() <= 10
assert out["kept"] <= 40
print(f"规则7 OK: 结算后保留 {out['kept']} 人, 移除 {len(out['removed'])} 人, 最大级 {lad6._max_tier()}")

print("\n✅ 全部规则测试通过")

# --- 新规则: 不在榜玩家首胜才进榜 ---
print("\n--- 新规则: 首胜进榜 ---")
ladN = Ladder()
# A B C D 分别只赢过榜外玩家, 都应并列进入 T1, 不应依次变成 T1/T2/T3/T4。
for idx, winner in enumerate(["A","B","C","D"], start=1):
    results = [(winner,winner,1)]
    results += [(f"{winner}_N{i}",f"{winner}_N{i}",i + 2) for i in range(3)]
    out = ladN.apply_match(results, on_date=date(2025,6,idx))
    assert out["entered"]==winner, out
    assert ladN.players[winner].on_board and ladN.players[winner].tier==1, (winner, ladN.players[winner].tier)

b0 = names(ladN.board())
assert b0.get(1)==["A","B","C","D"] and set(b0.keys())=={1}, b0
print("A/B/C/D 首胜入榜均为 T1 ->", ladN.render_board().replace("\n"," | "))

# A B C D 全 T1 对局, A 胜: A 留 T1, B C D 进入 T2。
r = ladN.apply_match([("A","A",1),("B","B",2),("C","C",3),("D","D",4)], on_date=date(2025,6,10))
assert r["recorded"], r
assert ladN.players["A"].tier==1, ladN.players["A"].tier
assert all(ladN.players[n].tier==2 for n in ["B","C","D"]), [ladN.players[n].tier for n in "BCD"]
print("全 T1 对局后 ->", ladN.render_board().replace("\n"," | "))

# 此时已有 T2, 新人 E 首胜应进入当前最低级 T2, 不新开 T3。
r2 = ladN.apply_match([("E","E",1),("E_N0","E_N0",2),("E_N1","E_N1",3),("E_N2","E_N2",4)], on_date=date(2025,6,11))
assert r2["entered"]=="E", r2
assert ladN.players["E"].on_board and ladN.players["E"].tier==2, ladN.players["E"].tier
assert names(ladN.board())[2]==["B","C","D","E"], names(ladN.board())
print("E 首胜入榜进入 T2 ->", ladN.render_board().replace("\n"," | "))

# 验证: 在榜玩家的升降级不受榜外玩家影响
# 单独布置: A(T1), C(T2), 两个榜外新人。C 再赢且 C 是在榜最低 -> C 升级。
ladM = Ladder()
ladM._ensure_player("A","A"); ladM.players["A"].tier=1; ladM.players["A"].on_board=True
ladM._ensure_player("C","C"); ladM.players["C"].tier=2; ladM.players["C"].on_board=True
ladM._ensure_player("N1","N1"); ladM._ensure_player("N2","N2")  # 两个榜外新人
# C 赢, 本局在榜的是 A(T1) C(T2), C 是最低级 -> C 升到 T1
r3 = ladM.apply_match([("C","C",1),("A","A",2),("N1","N1",3),("N2","N2",4)], on_date=date(2025,7,1))
assert ladM.players["C"].tier==1, ladM.players["C"].tier
assert not ladM.players["N1"].on_board
print("在榜最低级胜者升级(忽略榜外人) ->", ladM.render_board().replace("\n"," | "))

print("\n✅ 新规则(首胜进榜)测试通过")

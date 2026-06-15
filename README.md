# 沙丘终局 T 榜 QQ 机器人

频道/QQ群 @机器人并发对局结算截图 → MiniMax-M3 识别四人名次 → 按 T 榜规则自动升降级 → 持久化记录。好友/C2C/私信可直接发送文本指令。

## 目录结构

```
dune_bot/
├── core/
│   ├── ladder.py      # T 榜核心引擎(7 条规则)
│   ├── registry.py    # 昵称 → 稳定玩家 ID 映射
│   ├── vision.py      # 云端视觉模型识别截图
│   └── service.py     # 服务层: 识别→待确认→入榜→结算
├── bot/
│   └── qqbot.py       # 官方 QQ 频道机器人 (qq-botpy)
├── data/              # 运行时自动生成: 榜单/注册表/历史
├── test_ladder.py     # 规则单元测试
├── smoke_test.py      # 端到端冒烟测试(假视觉)
└── requirements.txt
```

## 安装

```bash
pip install -r requirements.txt
```

## 配置(环境变量)

| 变量 | 说明 |
|------|------|
| `QQ_APPID` | 机器人 AppID(QQ 开放平台) |
| `QQ_SECRET` | 机器人 AppSecret |
| `MINIMAX_API_KEY` | MiniMax API Key |
| `MINIMAX_MODEL` | MiniMax 模型名,默认 `MiniMax-M3` |
| `MINIMAX_BASE_URL` | MiniMax OpenAI 兼容地址,默认 `https://api.minimax.io/v1` |
| `DUNE_ADMINS` | 管理员 user id,逗号分隔 |
| `DUNE_DATA_DIR` | 数据目录,默认 `./data` |
| `DUNE_ENABLE_GUILD_MESSAGES` | 私域频道机器人监听频道内全部消息,公域机器人不要开启;默认只监听 @ |

## 运行

```bash
cd dune_bot
python -m bot.qqbot
```

## 截图类型与识别

机器人会自动判断截图属于哪一类:

| 类型 | 特征 | 名次来源 |
|------|------|----------|
| 结算页 `settlement` | 顶部「胜利/失败」+ 行首「第1-4名」标签 | **直接信游戏标签**(游戏已完成平局判定) |
| 终局盘面 `endgame_board` | 牌桌画面,无名次标签(如图4) | **由四级平局规则自动定名次** |

每局记录的字段:记录时间、玩家昵称、使用角色、终局分数、资源(香料/帝国索/水/兵营)、终局顺位。

### 四级平局规则(仅用于终局盘面无名次时)

分数相同时,依次比较,分出即止:香料(美琅脂)→ 帝国索 Solari → 水资源 → 驻守兵营士兵数。四项全同即真正并列(rank 相同,后续跳号)。

> 结算页上游戏已经算过平局并排好序,所以不会再用这套规则重排——只读取并记录数据。

## 指令

| 操作 | 说明 |
|------|------|
| 频道/QQ群 @机器人 + 截图 | 识别盘面,生成待确认记录(含角色/分数/资源) |
| `确认 <编号>` | 确认识别结果入榜 |
| `取消 <编号>` | 丢弃识别结果 |
| `榜单` / `查榜` | 查看当前 T 榜 |
| `明细 [n]` | 查看最近 n 局对局明细(默认 3) |
| `结算` (管理员) | 只保留 T1–T10 |
| `合并 <保留名> <并入名>` (管理员) | 合并同一玩家的不同昵称 |
| `设置T <玩家名> <T级>` (管理员) | 手动设置玩家 T 位,如 `设置T ShipitHolla T1` |
| `设置T <玩家名> 榜外` (管理员) | 手动将玩家移出 T 榜 |
| `清空T榜` (管理员) | 清空当前 T 榜,保留玩家注册表和历史明细 |
| `帮助` | 指令说明 |

## 没有回应时先看日志

重启后日志会显示已启用的 intents,正常至少应包含:

```text
已启用 intents: public_guild_messages, public_messages, direct_message
```

发送消息后如果日志里没有 `收到 ... 消息`,说明事件没有从 QQ 开放平台推到服务:

- QQ频道: 必须在频道里真正 @ 机器人;当前默认监听 `on_at_message_create`。
- QQ群: 需要使用群里 @ 机器人的方式触发 `on_group_at_message_create`。
- 好友/C2C: 直接发文本会触发 `on_c2c_message_create`。
- 私域频道如果想不 @ 也响应命令,设置 `DUNE_ENABLE_GUILD_MESSAGES=1`;公域机器人不要开启。

如果日志里有 `收到 ... 消息` 但没有回复,继续看紧随其后的 `发送回复失败`、`图片下载失败` 或 `未配置视觉模型` 日志。

## T 榜规则实现说明

0. **进榜规则** 不在榜内的玩家,参与对局只记录明细、不进榜、不参与升降级;**首次拿到一局第1名(首胜)后才进入 T 榜**,落在当前最低级垫底。新人入榜不会自动新开下一层: 当前只有 T1 时进 T1, 已有 T2 时进 T2。在榜玩家的升降级只与本局其他在榜玩家的 T 级比较,不受榜外新人影响。

1. **规则1** 普通对局,胜者若为本局四人中 T 级最低(段位最低/数字最大,可并列)→ 升一级。
2. **规则2** 某级 0 人时,下级整体上移,无悬空层(`_compact`)。
3. **规则3** 全 T1 对局:胜者保留 T1,本局其余 3 人 → T2,原 T2→T3 依次后推。**优先于规则1**。
4. **规则4** 同级内按升级先后排序(`order_seq`),越晚升上来排越后。
5. **规则5** 同一四人集合,一天只记前三把(`_daily_counts`)。
6. **规则6** 截图为准 + 漏发不记:识别后进入待确认队列,确认才入榜(`require_confirm`)。
7. **规则7** 结算只保留 T1–T10(`settle`)。

### 可调参数

- `Ladder.winner_t1_position`:全 T1 对局胜者在 T1 内的排位,`"last"`(默认,排末位)或 `"first"`。
- `DuneService(require_confirm=...)`:`False` 则识别后直接入榜,不走确认。

## 提高识别准确率

`core/vision.py` 里的 `SYSTEM_PROMPT` 是识别准确率的关键。请根据你游戏结算截图的**实际版式**(名次怎么显示、昵称在哪、有无积分)调整提示词。默认调用 MiniMax OpenAI-compatible Chat Completions:

```bash
export MINIMAX_API_KEY=你的MiniMaxKey
export MINIMAX_MODEL=MiniMax-M3
```

## 测试

```bash
python test_ladder.py    # 规则单测
python smoke_test.py     # 端到端(无需真实 API)
```

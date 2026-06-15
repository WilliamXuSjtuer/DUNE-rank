"""
QQ 机器人主程序 (官方 SDK: qq-botpy)
==================================

功能:
- 频道/QQ群 @机器人 并附带截图 -> 识别盘面 -> 生成待确认记录
- 频道/QQ群 @机器人 或好友/C2C/私信 + 文本指令:
    榜单 / 查榜              -> 显示当前 T 榜
    确认 <编号>             -> 确认某条识别结果入榜
    取消 <编号>             -> 取消某条识别结果
    结算                    -> (管理员)只保留 T1-T10
    合并 <保留名> <并入名>  -> (管理员)合并同一玩家的不同昵称
    帮助                    -> 指令说明

环境变量:
    QQ_APPID, QQ_SECRET       机器人凭据
    MINIMAX_API_KEY           MiniMax API Key
    MINIMAX_MODEL             MiniMax 模型名(默认 MiniMax-M3)
    MINIMAX_BASE_URL          MiniMax OpenAI 兼容地址(默认 https://api.minimax.io/v1)
    DUNE_ADMINS               管理员 user id, 逗号分隔
    DUNE_DATA_DIR             数据目录(默认 ./data)
    DUNE_ENABLE_GUILD_MESSAGES=1
                              私域频道机器人监听频道内全部消息(默认只监听 @)

运行:
    pip install qq-botpy aiohttp
    python -m bot.qqbot
"""

from __future__ import annotations
import os
import re
import asyncio
import logging

import aiohttp
import botpy
from botpy.message import Message

from core.service import DuneService
from core.vision import VisionRecognizer

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dune_bot")

AT_PATTERN = re.compile(r"<@!?\d+>")
ADMINS = set(filter(None, os.environ.get("DUNE_ADMINS", "").split(",")))
DATA_DIR = os.environ.get("DUNE_DATA_DIR", os.path.join(os.getcwd(), "data"))

HELP_TEXT = (
    "沙丘终局 T 榜机器人指令:\n"
    "• @我 并发对局结算截图 → 自动识别\n"
    "• 确认 <编号> → 确认识别结果入榜\n"
    "• 取消 <编号> → 丢弃该识别结果\n"
    "• 榜单 / 查榜 → 查看当前 T 榜\n"
    "• 明细 [n] → 查看最近 n 局对局明细(默认3)\n"
    "• 帮助 → 显示本说明\n"
    "管理员指令: 结算 / 合并 <保留名> <并入名>"
)


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _author_id(message) -> str:
    author = getattr(message, "author", None)
    if author is None:
        return "unknown"
    for attr in ("id", "user_openid", "member_openid"):
        value = getattr(author, attr, None)
        if value:
            return str(value)
    return "unknown"


def _is_admin(message: Message) -> bool:
    return _author_id(message) in ADMINS


def _is_from_bot(message) -> bool:
    return bool(getattr(getattr(message, "author", None), "bot", False))


def _clean_content(message) -> str:
    return AT_PATTERN.sub("", getattr(message, "content", None) or "").strip()


def _is_image_attachment(attachment) -> bool:
    content_type = str(getattr(attachment, "content_type", "") or "").lower()
    url = str(getattr(attachment, "url", "") or "").split("?", 1)[0].lower()
    filename = str(getattr(attachment, "filename", "") or "").lower()
    return (
        content_type.startswith("image")
        or url.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
        or filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
    )


def _image_attachments(message) -> list:
    attachments = getattr(message, "attachments", None) or []
    return [a for a in attachments if _is_image_attachment(a)]


def _looks_like_command(content: str) -> bool:
    if content in (
        "帮助", "help", "菜单", "榜单", "查榜", "排行", "t榜", "T榜", "结算"
    ):
        return True
    return any(re.match(pattern, content) for pattern in (
        r"^明细(?:\s+\d+)?$",
        r"^确认\s+[0-9a-fA-F]{4,8}$",
        r"^取消\s+[0-9a-fA-F]{4,8}$",
        r"^合并\s+\S+\s+\S+$",
    ))


async def _download(url: str) -> bytes | None:
    # attachment url 可能缺协议头
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http"):
        url = "https://" + url
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 200:
                    return await r.read()
                log.warning("下载图片失败 status=%s url=%s", r.status, url)
    except Exception as e:
        log.warning("下载图片异常: %s", e)
    return None


class DuneBot(botpy.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        vision = None
        if os.environ.get("MINIMAX_API_KEY"):
            vision = VisionRecognizer()
        # require_confirm=True: 识别后需人工确认, 防止误识别污染榜单(规则6)
        self.service = DuneService(DATA_DIR, vision=vision, require_confirm=True)

    async def on_ready(self):
        log.info("机器人 %s 已上线", self.robot.name)

    async def on_at_message_create(self, message: Message):
        await self._handle_incoming(message, "guild_at")

    async def on_group_at_message_create(self, message):
        await self._handle_incoming(message, "group_at")

    async def on_c2c_message_create(self, message):
        await self._handle_incoming(message, "c2c")

    async def on_direct_message_create(self, message):
        await self._handle_incoming(message, "direct")

    async def on_message_create(self, message: Message):
        # 仅在 DUNE_ENABLE_GUILD_MESSAGES=1 且机器人具备私域权限时会收到。
        await self._handle_incoming(message, "guild_message", reply_unknown=False)

    async def _handle_incoming(
        self,
        message,
        source: str,
        reply_unknown: bool = True,
    ) -> None:
        if _is_from_bot(message):
            return

        content = _clean_content(message)
        image_atts = _image_attachments(message)
        log.info(
            "收到 %s 消息 author=%s content=%r attachments=%s images=%s",
            source,
            _author_id(message),
            content,
            len(getattr(message, "attachments", None) or []),
            len(image_atts),
        )

        # 1) 带图片 -> 识别
        if image_atts:
            await self._handle_image(message, image_atts[0])
            return

        if not reply_unknown and not _looks_like_command(content):
            return

        # 2) 文本指令
        await self._handle_command(message, content, reply_unknown=reply_unknown)

    async def _reply_text(self, message, content: str) -> None:
        try:
            await message.reply(content=content)
        except Exception:
            log.exception(
                "发送回复失败 author=%s message_id=%s",
                _author_id(message),
                getattr(message, "id", None),
            )
            raise

    async def _handle_image(self, message, attachment):
        if self.service.vision is None:
            await self._reply_text(message, "未配置视觉模型, 无法识别截图")
            return
        url = getattr(attachment, "url", "")
        data = await _download(url)
        if not data:
            await self._reply_text(message, "图片下载失败, 请重发")
            return
        submitter = _author_id(message)
        # 视觉调用是阻塞的, 丢到线程池避免卡住事件循环
        res = await asyncio.to_thread(
            self.service.recognize_image, data, submitter)
        await self._reply_text(message, res["msg"])

    async def _handle_command(
        self,
        message,
        content: str,
        reply_unknown: bool = True,
    ) -> None:
        if not content or content in ("帮助", "help", "菜单"):
            await self._reply_text(message, HELP_TEXT)
            return

        if content in ("榜单", "查榜", "排行", "t榜", "T榜"):
            await self._reply_text(message, self.service.board_text())
            return

        m = re.match(r"^明细(?:\s+(\d+))?$", content)
        if m:
            n = int(m.group(1)) if m.group(1) else 3
            await self._reply_text(message, self.service.recent_matches(min(n, 10)))
            return

        m = re.match(r"^确认\s+([0-9a-fA-F]{4,8})$", content)
        if m:
            res = self.service.confirm(m.group(1))
            await self._reply_text(message, res["msg"])
            return

        m = re.match(r"^取消\s+([0-9a-fA-F]{4,8})$", content)
        if m:
            res = self.service.cancel(m.group(1))
            await self._reply_text(message, res["msg"])
            return

        if content == "结算":
            if not _is_admin(message):
                await self._reply_text(message, "只有管理员可以结算")
                return
            res = self.service.settle()
            await self._reply_text(message, res["msg"])
            return

        m = re.match(r"^合并\s+(\S+)\s+(\S+)$", content)
        if m:
            if not _is_admin(message):
                await self._reply_text(message, "只有管理员可以合并玩家")
                return
            res = self.service.admin_merge(m.group(1), m.group(2))
            await self._reply_text(message, res["msg"])
            return

        if reply_unknown:
            await self._reply_text(message, "未识别的指令。发送「帮助」查看用法。")


def _build_intents():
    flags = {
        "public_guild_messages": True,  # QQ频道 @ 消息
        "public_messages": True,        # QQ群 @ 消息、好友/C2C 消息
        "direct_message": True,         # 频道私信
    }
    if _env_enabled("DUNE_ENABLE_GUILD_MESSAGES"):
        # 仅私域机器人可用；公域机器人不要开启。
        flags["guild_messages"] = True

    try:
        intents = botpy.Intents(**flags)
    except TypeError as e:
        log.warning("当前 qq-botpy 不支持部分 intents: %s", e)
        log.warning("回退为仅监听频道 @ 消息；如需QQ群/C2C请升级 qq-botpy>=1.2.0")
        flags = {"public_guild_messages": True}
        intents = botpy.Intents(**flags)

    log.info("已启用 intents: %s", ", ".join(k for k, v in flags.items() if v))
    return intents


def main():
    appid = os.environ.get("QQ_APPID")
    secret = os.environ.get("QQ_SECRET")
    if not appid or not secret:
        raise SystemExit("请设置环境变量 QQ_APPID 和 QQ_SECRET")
    intents = _build_intents()
    client = DuneBot(intents=intents)
    client.run(appid=appid, secret=secret)


if __name__ == "__main__":
    main()

"""
QQ 频道机器人主程序 (官方 SDK: qq-botpy)
======================================

功能:
- @机器人 并附带截图 -> 识别盘面 -> 生成待确认记录
- @机器人 + 文本指令:
    榜单 / 查榜              -> 显示当前 T 榜
    确认 <编号>             -> 确认某条识别结果入榜
    取消 <编号>             -> 取消某条识别结果
    结算                    -> (管理员)只保留 T1-T10
    合并 <保留名> <并入名>  -> (管理员)合并同一玩家的不同昵称
    帮助                    -> 指令说明

环境变量:
    QQ_APPID, QQ_SECRET       机器人凭据
    ANTHROPIC_API_KEY         视觉模型密钥
    DUNE_ADMINS               管理员 user id, 逗号分隔
    DUNE_DATA_DIR             数据目录(默认 ./data)

运行:
    pip install qq-botpy anthropic aiohttp
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


def _is_admin(message: Message) -> bool:
    uid = getattr(message.author, "id", "") or ""
    return uid in ADMINS


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
        if os.environ.get("ANTHROPIC_API_KEY"):
            vision = VisionRecognizer()
        # require_confirm=True: 识别后需人工确认, 防止误识别污染榜单(规则6)
        self.service = DuneService(DATA_DIR, vision=vision, require_confirm=True)

    async def on_ready(self):
        log.info("机器人 %s 已上线", self.robot.name)

    async def on_at_message_create(self, message: Message):
        content = AT_PATTERN.sub("", message.content or "").strip()
        attachments = getattr(message, "attachments", None) or []

        # 1) 带图片 -> 识别
        image_atts = [a for a in attachments
                      if str(getattr(a, "content_type", "")).startswith("image")
                      or str(getattr(a, "url", "")).lower().endswith(
                          (".png", ".jpg", ".jpeg", ".webp", ".gif"))]
        if image_atts:
            await self._handle_image(message, image_atts[0])
            return

        # 2) 文本指令
        await self._handle_command(message, content)

    async def _handle_image(self, message: Message, attachment):
        if self.service.vision is None:
            await message.reply(content="未配置视觉模型, 无法识别截图")
            return
        url = getattr(attachment, "url", "")
        data = await _download(url)
        if not data:
            await message.reply(content="图片下载失败, 请重发")
            return
        submitter = getattr(message.author, "id", "") or "unknown"
        # 视觉调用是阻塞的, 丢到线程池避免卡住事件循环
        res = await asyncio.to_thread(
            self.service.recognize_image, data, submitter)
        await message.reply(content=res["msg"])

    async def _handle_command(self, message: Message, content: str):
        if not content or content in ("帮助", "help", "菜单"):
            await message.reply(content=HELP_TEXT)
            return

        if content in ("榜单", "查榜", "排行", "t榜", "T榜"):
            await message.reply(content=self.service.board_text())
            return

        m = re.match(r"^明细(?:\s+(\d+))?$", content)
        if m:
            n = int(m.group(1)) if m.group(1) else 3
            await message.reply(content=self.service.recent_matches(min(n, 10)))
            return

        m = re.match(r"^确认\s+([0-9a-fA-F]{4,8})$", content)
        if m:
            res = self.service.confirm(m.group(1))
            await message.reply(content=res["msg"])
            return

        m = re.match(r"^取消\s+([0-9a-fA-F]{4,8})$", content)
        if m:
            res = self.service.cancel(m.group(1))
            await message.reply(content=res["msg"])
            return

        if content == "结算":
            if not _is_admin(message):
                await message.reply(content="只有管理员可以结算")
                return
            res = self.service.settle()
            await message.reply(content=res["msg"])
            return

        m = re.match(r"^合并\s+(\S+)\s+(\S+)$", content)
        if m:
            if not _is_admin(message):
                await message.reply(content="只有管理员可以合并玩家")
                return
            res = self.service.admin_merge(m.group(1), m.group(2))
            await message.reply(content=res["msg"])
            return

        await message.reply(content="未识别的指令。发送「帮助」查看用法。")


def main():
    appid = os.environ.get("QQ_APPID")
    secret = os.environ.get("QQ_SECRET")
    if not appid or not secret:
        raise SystemExit("请设置环境变量 QQ_APPID 和 QQ_SECRET")
    intents = botpy.Intents(public_guild_messages=True)
    client = DuneBot(intents=intents)
    client.run(appid=appid, secret=secret)


if __name__ == "__main__":
    main()

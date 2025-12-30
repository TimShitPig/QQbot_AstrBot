from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import AstrBotConfig
import time
import re
from typing import Tuple

# [ENCRYPTED MODE ACTIVE]
# Core logic modules have been compiled to .pyc binaries.
# [SSL BYPASS APPLIED] rental_manager.py ignores ssl certificate errors.
from .logic.search_manager import SearchManager
from .logic.transfer_manager import TransferManager
from .logic.rental_manager import RentalManager

@register("mantou_bot", "馒头", "集合搜、存、检于一体的强力网盘助手。", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.search_manager = SearchManager(config)
        self.transfer_manager = TransferManager(config)
        self.rental_manager = RentalManager(context, config)

    async def initialize(self):
        """异步初始化"""
        # 如果从旧文件迁移了数据，保存一下配置
        self.config.save_config()
        # 启动时进行一次校验
        await self.rental_manager.check_rental_status()

    async def _check_rental(self, event: AstrMessageEvent) -> Tuple[bool, str]:
        """检查激活状态，返回 (是否激活, 错误消息)"""
        return await self.rental_manager.check_rental_status()

    def _get_rental_err_msg(self, event: AstrMessageEvent, msg: str) -> str:
        """获取验证错误提示消息"""
        admins = self.context.get_config().get("admins_id", [])
        if event.get_sender_id() in admins:
            return f"⚠️ [管理提示] {msg}\n请在面板检查 [插件激活] 配置。"
        else:
            return f"❌ 插件服务已暂停：{msg}\n请联系管理员处理。"

    @filter.command("save_quark")
    async def save_quark(self, event: AstrMessageEvent, link: str):
        """转存夸克网盘链接并分享"""
        is_active, msg = await self._check_rental(event)
        if not is_active:
            yield event.plain_result(self._get_rental_err_msg(event, msg))
            return
        
        yield event.plain_result(f"正在处理: {link} ...")
        async for res in self.transfer_manager.process_save(event, "quark", link, "用户指定链接"):
             yield res

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def search_resource(self, event: AstrMessageEvent):
        """处理搜索请求，支持搜、百度、夸克、UC 开头的各种指令"""
        message = event.message_str.strip()
        
        search_patterns = [
            (r'^搜(.+)$', None),
            (r'^百度(.+)$', "baidu"),
            (r'^夸克(.+)$', "quark"),
            (r'^UC(.+)$', "uc"),
        ]
        
        keyword = None
        priority_type = None
        
        for pattern, p_type in search_patterns:
            match = re.match(pattern, message, re.IGNORECASE)
            if match:
                keyword = match.group(1).strip()
                priority_type = p_type
                break
        
        if not keyword:
            return

        # 发现任务了，再进行服务验证
        is_active, msg = await self._check_rental(event)
        if not is_active:
            yield event.plain_result(self._get_rental_err_msg(event, msg))
            return

        # 委托给 SearchManager 执行带优先级的搜索
        async for res in self.search_manager.perform_search(event, keyword, priority_type):
            yield res

    async def _check_perm(self, event: AstrMessageEvent) -> bool:
        # 权限等级定义 (数字越小权限越高)
        LEVELS = {
            "超管": 0,
            "群主": 1,
            "管理员": 2,
            "普通成员": 3,
            "member": 3,
            "admin": 2,
            "owner": 1,
            "unknown": 4
        }
        
        # 获取配置要求的最低等级
        cfg = self.config.get("blacklist_settings", {})
        min_level_str = cfg.get("min_permission_level", "管理员")
        required_level = LEVELS.get(min_level_str, 2)
        
        # 1. 检查是否是超管 (AstrBot 管理员)
        admins = self.context.get_config().get("admins_id", [])
        sender_id = event.get_sender_id()
        if sender_id in admins:
            return True # 超管无视一切
            
        # 2. 获取当前用户等级
        user_role = "member"
        try:
            # 尝试从不同属性获取角色
            if hasattr(event, "role"):
                user_role = event.role
            elif hasattr(event, "event") and hasattr(event.event, "message_event"):
                sender = getattr(event.event.message_event, "sender", {})
                user_role = getattr(sender, "role", "member")
        except:
            pass
            
        user_level = LEVELS.get(user_role.lower(), 3)
        
        # 等级越小权限越大
        return user_level <= required_level

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_selection(self, event: AstrMessageEvent):
        """监听数字输入或翻页指令"""
        message = event.message_str.strip()
        user_id = event.get_sender_id()
        
        # 检查是否在该用户的活跃会话中
        cache = self.search_manager.search_cache.get(user_id)
        if not cache:
            return

        # 确认是翻页、序号或黑名单指令
        is_paging = message in ["下一页", "下一頁", "下页", "下頁", "next", "下", "上一页", "上一頁", "上页", "上頁", "prev", "上"]
        is_selection = re.match(r'^(?:第)?(\d+)(?:个|個)?$', message)
        is_blacklist = message.startswith("黑") or message in ["还原", "查询拉黑"]

        if not (is_paging or is_selection or is_blacklist):
            return

        # 确实在操作任务，进行状态验证
        is_active, msg = await self._check_rental(event)
        if not is_active:
            yield event.plain_result(self._get_rental_err_msg(event, msg))
            return

        search_cfg = self.config.get("search_config", {})
        expiry_min = int(search_cfg.get("cache_expiry", 8))
        if time.time() - cache.get("timestamp", 0) > expiry_min * 60:
            del self.search_manager.search_cache[user_id]
            yield event.plain_result(f"❌ 搜索结果已过期 (有效期 {expiry_min} 分钟)，请重新搜索。")
            return

        if message in ["下一页", "下一頁", "下页", "下頁", "next", "下"]:
            curr = cache.get("current_page", 1)
            yield event.plain_result(self.search_manager._render_search_page(user_id, curr + 1))
            return
        elif message in ["上一页", "上一頁", "上页", "上頁", "prev", "上"]:
            curr = cache.get("current_page", 1)
            yield event.plain_result(self.search_manager._render_search_page(user_id, curr - 1))
            return

        # 正则匹配序号：支持数字 或 “第X个”
        select_match = re.match(r'^(?:第)?(\d+)(?:个|個)?$', message)
        if select_match:
            idx = int(select_match.group(1)) - 1
            results = cache.get("all_flat_results", []) # 对应 SearchManager 新的存储结构
            if 0 <= idx < len(results):
                item = results[idx]
                link = item.get("url", "")
                note = item.get("note", "资源")
                ptype = item.get("_mapped_type", "other")
                
                yield event.plain_result("⏳ 正在转存请稍等...")
                async for res in self.transfer_manager.process_save(event, ptype, link, note):
                    yield res
            return

        # 处理黑名单
        if message.startswith("黑"):
            if not await self._check_perm(event):
                yield event.plain_result("❌ 权限不足：仅限群主或管理员拉黑资源。")
                return
                
            idx_str = message[1:].strip()
            if idx_str.isdigit():
                idx = int(idx_str) - 1
                results = cache.get("all_flat_results", [])
                if 0 <= idx < len(results):
                    url = results[idx].get("url", "")
                    title = results[idx].get("note", "未知资源")
                    if self.search_manager.add_to_blacklist(url, title):
                        yield event.plain_result(f"✅ 已拉黑：{title}\n后续搜索将不再显示该资源。")
                        # 保存配置到 AstrBot 框架
                        self.config.save_config()
                    else:
                        yield event.plain_result(f"ℹ️ 该资源已在黑名单中。")
                else:
                    yield event.plain_result(f"❌ 序号 {idx+1} 不在列表范围内。")
            return
        elif message == "还原":
            if not await self._check_perm(event):
                yield event.plain_result("❌ 权限不足：仅限群主或管理员操作。")
                return
                
            last_url = self.search_manager.restore_last_blacklist()
            if last_url:
                yield event.plain_result(f"✅ 已还原上一个拉黑的资源链接：\n{last_url}")
                self.config.save_config()
            else:
                yield event.plain_result(f"ℹ️ 当前黑名单为空。")
            return
        elif message == "查询拉黑":
            yield event.plain_result(self.search_manager.get_blacklist_msg())
            return

    async def terminate(self):
        """销毁方法"""
        pass

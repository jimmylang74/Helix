"""
Common Tools Plugin - 获取当前日期时间和默认地点（城市）。
"""

import json
import urllib.request
from datetime import datetime, timezone, timedelta

from pypinyin import pinyin, Style

from modules.agents.tool_base import BaseTool
from modules.core.user_question import user_question_broker
from modules.llm.llm_events import get_request_context, emit as _emit_llm_event
from modules.utils.logger import log_tool_call


# ──────────────────────────────────────────────────────────
# 辅助函数: 中文城市名 → 拼音名
# ──────────────────────────────────────────────────────────

# 常见城市特殊拼音映射（标准拼音 + 常见英文别名）
_CITY_PINYIN_MAP: dict[str, str] = {
    "北京": "Beijing",
    "上海": "Shanghai",
    "天津": "Tianjin",
    "重庆": "Chongqing",
    "南京": "Nanjing",
    "杭州": "Hangzhou",
    "广州": "Guangzhou",
    "深圳": "Shenzhen",
    "成都": "Chengdu",
    "武汉": "Wuhan",
    "西安": "Xi'an",
    "苏州": "Suzhou",
    "郑州": "Zhengzhou",
    "长沙": "Changsha",
    "东莞": "Dongguan",
    "沈阳": "Shenyang",
    "青岛": "Qingdao",
    "合肥": "Hefei",
    "佛山": "Foshan",
    "宁波": "Ningbo",
    "昆明": "Kunming",
    "大连": "Dalian",
    "厦门": "Xiamen",
    "哈尔滨": "Harbin",
    "济南": "Jinan",
    "福州": "Fuzhou",
    "无锡": "Wuxi",
    "长春": "Changchun",
    "温州": "Wenzhou",
    "石家庄": "Shijiazhuang",
    "常州": "Changzhou",
    "泉州": "Quanzhou",
    "南宁": "Nanning",
    "贵阳": "Guiyang",
    "南昌": "Nanchang",
    "太原": "Taiyuan",
    "烟台": "Yantai",
    "嘉兴": "Jiaxing",
    "南通": "Nantong",
    "金华": "Jinhua",
    "珠海": "Zhuhai",
    "惠州": "Huizhou",
    "徐州": "Xuzhou",
    "海口": "Haikou",
    "乌鲁木齐": "Urumqi",
    "绍兴": "Shaoxing",
    "中山": "Zhongshan",
    "台州": "Taizhou",
    "兰州": "Lanzhou",
    "潍坊": "Weifang",
    "保定": "Baoding",
    "镇江": "Zhenjiang",
    "桂林": "Guilin",
    "唐山": "Tangshan",
    "三亚": "Sanya",
    "湖州": "Huzhou",
    "呼和浩特": "Hohhot",
    "廊坊": "Langfang",
    "洛阳": "Luoyang",
    "威海": "Weihai",
    "盐城": "Yancheng",
    "柳州": "Liuzhou",
    "拉萨": "Lhasa",
    "绵阳": "Mianyang",
    "湛江": "Zhanjiang",
    "鞍山": "Anshan",
    "赣州": "Ganzhou",
    "大庆": "Daqing",
    "秦皇岛": "Qinhuangdao",
    "株洲": "Zhuzhou",
    "莆田": "Putian",
    "连云港": "Lianyungang",
    "衡阳": "Hengyang",
    "遵义": "Zunyi",
    "江门": "Jiangmen",
    "汕头": "Shantou",
}


def chinese_city_to_pinyin(city_cn: str) -> str:
    """
    将中国大陆中文城市名转换为拼音英文名。

    优先使用内置映射表（包含标准英文拼写，如 Xi'an、Ürümqi），
    未命中时 fallback 到 pypinyin 自动生成。

    Args:
        city_cn: 中文城市名，如 "南京"

    Returns:
        拼音英文名，如 "Nanjing"
    """
    if city_cn in _CITY_PINYIN_MAP:
        return _CITY_PINYIN_MAP[city_cn]

    py_list = pinyin(city_cn, style=Style.NORMAL)
    raw = "".join(s[0] for s in py_list)
    return raw[0].upper() + raw[1:] if raw else ""


# ──────────────────────────────────────────────────────────
# Tool: 获取当前日期时间
# ──────────────────────────────────────────────────────────

class GetCurrentDateTimeTool(BaseTool):
    """获取当前日期时间。"""

    name = "get_current_datetime"
    description = "获取当前日期和时间，支持指定时区。返回格式化的日期时间字符串。"
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "timezone_offset": {
                "type": "integer",
                "description": "UTC 时区偏移量（小时），如 8 表示 UTC+8（北京时间），默认为 8",
                "default": 8
            }
        },
        "required": []
    }

    def execute(self, timezone_offset: int = 8, **kwargs) -> str:
        log_tool_call(f"get_current_datetime(timezone_offset={timezone_offset})")
        try:
            tz = timezone(timedelta(hours=timezone_offset))
            now = datetime.now(tz)

            weekday_map = {
                0: "星期一", 1: "星期二", 2: "星期三",
                3: "星期四", 4: "星期五", 5: "星期六", 6: "星期日"
            }

            sign = "+" if timezone_offset >= 0 else "-"
            abs_offset = abs(timezone_offset)

            result = (
                f"日期时间: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"星期: {weekday_map[now.weekday()]}\n"
                f"时区: UTC{sign}{abs_offset}\n"
                f"时间戳: {int(now.timestamp())}"
            )
            return result
        except Exception as e:
            return f"获取日期时间失败: {e}"


# ──────────────────────────────────────────────────────────
# Tool: 获取当前城市
# ──────────────────────────────────────────────────────────

class GetCurrentLocationTool(BaseTool):
    """获取当前默认城市。"""

    name = "get_current_location"
    description = "获取当前默认城市，返回中文名和英文名。"
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {},
        "required": []
    }

    # ── IP 地理位置获取（fallback） ──

    @staticmethod
    def _fetch_city_by_ip() -> dict[str, str]:
        """通过 IP 地理位置 API 获取城市信息。"""
        url = "http://ip-api.com/json/?lang=zh-CN"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Helix-Agent/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        if data.get("status") != "success":
            raise RuntimeError(f"IP API 返回异常: {data}")

        return {
            "city_cn": data.get("city", ""),
            "region": data.get("regionName", ""),
            "country": data.get("country", ""),
        }

    # ── 配置读取 ──

    @staticmethod
    def _load_from_config() -> str | None:
        """从 Helix.json 读取 default_location.city 配置值。"""
        try:
            from modules.config.config_manager import ConfigManager
            config = ConfigManager()
            return config.get("default_location.city")
        except Exception:
            return None

    # ── execute ──

    def execute(self, **kwargs) -> str:
        log_tool_call("get_current_location()")

        # 1. 优先读取配置
        city_cn = self._load_from_config()

        # 2. 配置为空 → IP 获取
        if not city_cn:
            try:
                info = self._fetch_city_by_ip()
                city_cn = info.get("city_cn", "")
            except Exception as e:
                return f"获取城市失败: {e}"

        if not city_cn:
            return "无法确定当前城市"

        city_en = chinese_city_to_pinyin(city_cn)
        return f"当前城市: {city_cn} ({city_en})"


# ──────────────────────────────────────────────────────────
# Tool: 向用户提问（阻塞等待用户回答）
# ──────────────────────────────────────────────────────────

class AskUserTool(BaseTool):
    """向用户提问，阻塞等待用户回答后返回回答内容。"""

    name = "ask_user"
    description = "当信息不足、存在歧义、需要用户确认时调用该工具向用户提问，禁止自行猜测"
    intents = ["*"]
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "需要向用户提问的问题；可在问题中列出选项供用户选择，"
                    "例如：A. xxx，B. xxx"
                ),
            }
        },
        "required": ["question"],
    }

    def execute(self, question: str = "", **kwargs) -> str:
        request_id = get_request_context()
        if not request_id:
            return "错误: ask_user 需要活跃的请求上下文（request_id），当前无法提问"
        if user_question_broker.is_waiting(request_id):
            return "错误: 已有一个等待用户回答的问题，请等待其回答完成，不要重复提问"
        log_tool_call(f"ask_user(question='{question[:200]}')")
        _emit_llm_event(request_id, {"type": "ask_user", "question": question})
        return user_question_broker.ask(request_id, question)

"""
weather_tool.py - 天气查询示例外部插件

演示如何编写一个简单的外部插件：
继承 BaseTool → 定义元数据 → 实现 execute() → 完成。
放入 plugins/user/ 后重启 Helix 即可自动注册。

返回值遵循插件约定：JSON 格式字符串，且必须包含 success 字段
（true 表示成功，false 表示失败）。
"""

import json
import urllib.request
import urllib.parse

from HelixCore.tools.base import BaseTool
from modules.utils.logger import log_tool_call


class WeatherTool(BaseTool):
    """查询指定城市的天气信息。"""

    name = "weather"
    description = (
        "查询指定城市的天气信息，返回当前实况以及今天和未来两天的三天预报，"
        "包括最高/最低温度、天气状况、湿度、风力等。支持中文城市名。"
    )
    intents = ["generic"]
    parameters = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称，如 '北京'、'Tokyo'、'New York'"
            }
        },
        "required": ["city"]
    }

    def execute(self, city: str = "", **kwargs) -> str:
        log_tool_call(f"weather(city='{city}')")
        try:
            url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Helix-Agent/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            current = data["current_condition"][0]

            # wttr.in 提供 3 天预报（今天 + 未来 2 天）
            forecast = []
            for day in data.get("weather", [])[:3]:
                hourly = day.get("hourly") or []
                # 取接近正午的时刻点（索引 4 对应 12:00）作为当日天气描述
                daytime = hourly[4] if len(hourly) > 4 else (hourly[0] if hourly else {})
                weather_desc = ""
                if daytime.get("weatherDesc"):
                    weather_desc = daytime["weatherDesc"][0]["value"]
                forecast.append({
                    "日期": day.get("date", ""),
                    "最高温度": day.get("maxtempC", ""),
                    "最低温度": day.get("mintempC", ""),
                    "平均温度": day.get("avgtempC", ""),
                    "天气": weather_desc,
                    "湿度": daytime.get("humidity", ""),
                    "风速": daytime.get("windspeedKmph", ""),
                    "风向": daytime.get("winddir16Point", ""),
                })

            return json.dumps({
                "success": True,
                "城市": city,
                "当前": {
                    "温度": current["temp_C"],
                    "体感温度": current["FeelsLikeC"],
                    "天气": current["weatherDesc"][0]["value"],
                    "湿度": current["humidity"],
                    "风速": current["windspeedKmph"],
                    "风向": current["winddir16Point"],
                    "观测时间": current["observation_time"],
                },
                "预报": forecast,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "success": False,
                "城市": city,
                "error": str(e),
            }, ensure_ascii=False)

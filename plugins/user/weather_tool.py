"""
weather_tool.py - 天气查询示例外部插件

演示如何编写一个简单的外部插件：
继承 BaseTool → 定义元数据 → 实现 execute() → 完成。
放入 plugins/user/ 后重启 Helix 即可自动注册。
"""

import json
import urllib.request
import urllib.parse

from modules.agents.tool_base import BaseTool
from modules.utils.logger import log_tool_call


class WeatherTool(BaseTool):
    """查询指定城市的天气信息。"""

    name = "weather"
    description = "查询指定城市的当前天气信息，包括温度、天气状况、风力等。支持中文城市名。"
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
            result = (
                f"城市: {city}\n"
                f"温度: {current['temp_C']}°C (体感 {current['FeelsLikeC']}°C)\n"
                f"天气: {current['weatherDesc'][0]['value']}\n"
                f"湿度: {current['humidity']}%\n"
                f"风速: {current['windspeedKmph']} km/h ({current['winddir16Point']})"
            )
            return result
        except Exception as e:
            return f"天气查询失败 ({city}): {e}"

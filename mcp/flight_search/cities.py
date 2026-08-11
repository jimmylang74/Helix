"""常用中国城市名 → 三字码（IATA）映射，用于携程/飞猪的航班搜索。"""

CITY_CODES: dict[str, str] = {
    "北京": "BJS", "上海": "SHA", "广州": "CAN", "深圳": "SZX",
    "成都": "CTU", "杭州": "HGH", "重庆": "CKG", "西安": "XIY",
    "武汉": "WUH", "南京": "NKG", "长沙": "CSX", "青岛": "TAO",
    "大连": "DLC", "昆明": "KMG", "三亚": "SYX", "厦门": "XMN",
    "天津": "TSN", "郑州": "CGO", "沈阳": "SHE", "哈尔滨": "HRB",
    "济南": "TNA", "福州": "FOC", "南宁": "NNG", "贵阳": "KWE",
    "兰州": "LHW", "乌鲁木齐": "URC", "拉萨": "LXA", "海口": "HAK",
    "桂林": "KWL", "珠海": "ZUH", "宁波": "NGB", "合肥": "HFE",
    "石家庄": "SJW", "太原": "TYN", "呼和浩特": "HET", "银川": "INC",
    "西宁": "XNN", "长春": "CGQ", "南昌": "KHN", "无锡": "WUX",
    "温州": "WNZ", "烟台": "YNT", "泉州": "JJN", "徐州": "XUZ",
    "南通": "NTG", "常州": "CZX", "扬州": "YTY", "汕头": "SWA",
    "湛江": "ZHA", "香港": "HKG", "澳门": "MFM", "台北": "TPE",
    "高雄": "KHH",
}


def to_iata(city: str) -> str:
    """城市名或三字码 → 三字码。

    入参可直接是 IATA 三字码（如 "SHA"），或中文城市名（如 "上海"）。
    无法识别时抛出 ValueError。
    """
    name = (city or "").strip()
    if not name:
        raise ValueError("城市不能为空")
    if len(name) == 3 and name.isascii() and name.isalpha():
        return name.upper()
    code = CITY_CODES.get(name)
    if not code:
        raise ValueError(f"未收录城市: {name}（可改用三字码，如 SHA/BJS）")
    return code


def to_city_name(city: str) -> str:
    """三字码或城市名 → 中文城市名。

    携程移动版城市弹层与飞猪结果 URL 都需要中文城市名：
    - 输入为已收录三字码（如 "SHA"）→ 返回 "上海"
    - 输入为中文城市名（如 "上海"）→ 原样返回
    - 其他（未知三字码）→ 原样返回
    """
    name = (city or "").strip()
    if not name:
        return name
    if len(name) == 3 and name.isascii() and name.isalpha():
        upper = name.upper()
        for cn, code in CITY_CODES.items():
            if code == upper:
                return cn
    return name

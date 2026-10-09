"""weatheremail — 天气邮件推送程序.

获取北京市实时天气与次日预报，记录温度历史，在出现大幅升降温或恶劣
天气时通过 SMTP 发送 HTML 预警邮件。

纯标准库实现，无第三方依赖。
"""

__version__ = "1.0.1"
__appname__ = "weatheremail"
__summary__ = "天气邮件推送程序"

# 北京市中心坐标
DEFAULT_LAT = "39.90"
DEFAULT_LON = "116.41"
DEFAULT_LOCATION_NAME = "北京市"

"""
Cron 定时任务通道包。

本包只提供文档与命名空间，不做任何子模块的预先导入（避免
``__init__ ↔ scheduler`` 导入环）。消费方请使用显式路径：

- ``modules.channels.cron.store``      任务定义(cron.json) + 结果(cron.db)
- ``modules.channels.cron.scheduler``  CronScheduler 调度线程与 get_scheduler 单例
- ``modules.channels.cron.channel``    CronChannel（ChannelAdapter 实现）
"""

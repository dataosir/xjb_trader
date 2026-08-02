"""阶段返回值：四个阶段共用的一套哨兵字符串。

集中定义的原因是这三个值同时被 phase1~4 和 runner 判断。此前它们在
phase1/phase2/phase3 各自重写了一遍（phase2 甚至硬编码了字面量 "ABORT"），
一旦有人只改一处就会出现"某个阶段的 OK 不等于另一个阶段的 OK"。

    OK      本阶段通过，继续下一阶段
    REJECT  明确不合格，出具拒绝报告（属于正常业务结果）
    ABORT   流程中断（用户放弃、数据缺失、异常），不出具拒绝报告
"""
from __future__ import annotations

OK = "OK"
REJECT = "REJECT"
ABORT = "ABORT"

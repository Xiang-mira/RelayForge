"""Versioned bounded repair corpus; each template has independent trusted tests."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RepairTemplate:
    title: str
    before: str
    after: str


TEMPLATES = {
    "fix_addition": RepairTemplate("修复加法函数", "return a - b", "return a + b"),
    "fix_sum_empty": RepairTemplate(
        "空序列求和", "return sum(values) if values else None", "return sum(values)"
    ),
    "fix_factorial_zero": RepairTemplate(
        "阶乘零边界", "if n == 0:\n        return 0", "if n == 0:\n        return 1"
    ),
    "fix_clamp_bounds": RepairTemplate(
        "数值区间截断", "return min(low, max(value, high))", "return max(low, min(value, high))"
    ),
    "fix_unique_order": RepairTemplate(
        "保留顺序去重", "return sorted(set(values))", "return list(dict.fromkeys(values))"
    ),
    "fix_binary_search": RepairTemplate(
        "二分搜索闭区间", "while low < high:", "while low <= high:"
    ),
    "fix_median_even": RepairTemplate(
        "偶数样本中位数",
        "return values[middle - 1] + values[middle]",
        "return (values[middle - 1] + values[middle]) / 2",
    ),
    "fix_slug_spacing": RepairTemplate("空白规范化", "split(' ')", "split()"),
    "fix_leap_century": RepairTemplate(
        "闰年世纪规则",
        "return year % 4 == 0\n",
        "return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n",
    ),
    "fix_chunk_remainder": RepairTemplate(
        "分块尾部保留", "range(0, len(values) - size + 1, size)", "range(0, len(values), size)"
    ),
}

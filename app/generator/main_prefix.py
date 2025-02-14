"""
prefix_manager.py
シフト生成前の事前チェックを統括するマネージャーモジュール。
各種Prefixクラスを使用して、全ての制約チェックを実行する。
"""

from typing import List, Optional
from PyQt6.QtWidgets import QMessageBox
from datetime import datetime
import calendar
import math
from .logger import logger
from ..from_dict import StaffData, ShiftData, RuleData
from .mapping import (
    SHIFT_TYPE_FIXMAPPING,
    KANJI_TO_NUMBER
)
from .basic_prefix import BasicPrefix
from .pattern_prefix import PatternPrefix
from .sequence_prefix import SequencePrefix
from ..firebase_client import write_notification

class PrefixManager:
    def __init__(self, year: int, month: int, rule_data: RuleData):
        """
        Parameters:
            year (int): 対象年
            month (int): 対象月
            rule_data (RuleData): ルールデータ
        """
        self.year = year
        self.month = month
        self.rule_data = rule_data
        # 月の日数を計算して保持
        self.month_days = calendar.monthrange(year, month)[1]
        # カレンダー情報を計算して保持
        cal = calendar.monthcalendar(year, month)
        self.sunday_count = sum(1 for week in cal if week[calendar.SUNDAY] != 0)
        self.weekday_count = self.month_days - self.sunday_count
        self.SHIFT_TYPE_FIXMAPPING = SHIFT_TYPE_FIXMAPPING
        self.KANJI_TO_NUMBER = KANJI_TO_NUMBER


        # 各Prefixクラスのインスタンス化
        self.basic = BasicPrefix(year, month, rule_data)
        self.pattern = PatternPrefix(year, month, rule_data)
        self.sequence = SequencePrefix(year, month, rule_data)

    def check_constraints(
        self,
        staff_data_list: List[StaffData],
        shift_data: Optional[ShiftData],
    ) -> bool:
        """
        全ての制約チェックを実行する。
        エラーがある場合はFirestoreに通知を保存し、Falseを返す。
        
        Parameters:
            staff_data_list (List[StaffData]): スタッフデータのリスト
            shift_data (Optional[ShiftData]): シフトデータ
        
        Returns:
            bool: 全てのチェックが通ればTrue、エラーがあればFalse
        """
        if shift_data is None:
            msg = "シフトデータが不足しています。"
            logger.error(msg)
            write_notification(msg)
            return False

        # 基本チェック
        if not self.basic.check_constraints(staff_data_list, shift_data):
            return False

        # パターンチェック
        if not self.pattern.check_constraints(staff_data_list, shift_data):
            return False

        # 連続性チェック
        if not self.sequence.check_constraints(staff_data_list, shift_data):
            return False

        return True
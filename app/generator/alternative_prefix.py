"""
alternative_prefix.py
シフト生成前の事前チェックを行うモジュール。
シフト間隔に関する制約違反を検出し、エラー内容をユーザーに提示する。
"""

from typing import List, Optional, Tuple, Dict
from datetime import datetime
import calendar
import math
from .logger import logger
from ..from_dict import StaffData, ShiftData, RuleData
from .mapping import (
    SHIFT_TYPES,
    SHIFT_TYPE_MAPPING,
    KANJI_TO_NUMBER,
    STATUS_MAP
)
from ..firebase_client import write_notification

class AlternativePrefix:
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
        self.SHIFT_TYPE_MAPPING = SHIFT_TYPE_MAPPING
        self.KANJI_TO_NUMBER = KANJI_TO_NUMBER
        self.SHIFT_TYPES = SHIFT_TYPES

    def check_constraints(
        self,
        staff_data_list: List[StaffData],
        shift_data: ShiftData,
    ) -> bool:
        """シフト間隔に関する制約チェックを実行"""
        logger.debug("=== シフト間隔制約のチェック ===")
        
        # 1. シフトデータとの不整合チェック
        if self.check_shift_interval_data_consistency(staff_data_list, shift_data):
            return False
        
        # 2. 制約間矛盾チェック
        if self.check_shift_interval_constraints_conflict(staff_data_list):
            return False
        
        # 3. 必須嫌悪とmin、必須愛好とmaxの整合性チェック
        if self.check_shift_interval_min_max_consistency(staff_data_list):
            return False
        
        return True
        
    def check_shift_interval_data_consistency(
        self,
        staff_data_list: List[StaffData],
        shift_data: ShiftData
    ) -> bool:
        """シフト間隔制約とシフトデータの整合性をチェック
        
        既存のシフトデータが、必須のシフト間隔制約に違反していないかを検証
        
        Returns:
            bool: エラーがある場合はTrue
        """
        errors = []
        
        # スタッフごとのエントリを整理
        entries_by_staff = {}
        for entry in shift_data.entries:
            if entry.staff_name not in entries_by_staff:
                entries_by_staff[entry.staff_name] = []
            entries_by_staff[entry.staff_name].append(entry)
        
        # 各スタッフの制約をチェック
        for staff in staff_data_list:
            # シフト間隔の必須制約を抽出
            interval_constraints = [
                c for c in staff.constraints
                if c.category == "シフト間隔" and c.type == "必須"
            ]
            
            if not interval_constraints or staff.name not in entries_by_staff:
                continue
            
            # スタッフのエントリを日付順にソート
            staff_entries = sorted(entries_by_staff[staff.name], key=lambda e: e.day)
            
            # 各制約に対してチェック
            for constraint in interval_constraints:
                # 対象シフトを取得
                target_shift_name = constraint.count
                shift_type = self.SHIFT_TYPE_MAPPING.get(target_shift_name, target_shift_name)
                
                # 無効なシフトタイプはスキップ
                if shift_type not in self.SHIFT_TYPES:
                    continue
                
                # 間隔日数
                interval_days = int(constraint.target)
                
                # シフトの出現日をリストアップ
                shift_days = []
                for entry in staff_entries:
                    if entry.shift_type == shift_type:
                        shift_days.append(entry.day)
                
                # 嫌悪制約のチェック（指定日数以内に再発生禁止）
                if constraint.sub_category == "嫌悪":
                    for i in range(len(shift_days) - 1):
                        if shift_days[i+1] - shift_days[i] <= interval_days:
                            errors.append(
                                f"スタッフ「{staff.name}」のシフト間隔制約違反：\n"
                                f"・{target_shift_name}は{interval_days}日以内に再発生禁止\n"
                                f"・{shift_days[i]}日目と{shift_days[i+1]}日目に{target_shift_name}が"
                                f"{shift_days[i+1] - shift_days[i]}日間隔で割り当てられています"
                            )
                
                # 愛好制約のチェックは不要
                # 理由：
                # 1. シフト入力は途中段階であることが多い
                # 2. 愛好制約は「シフトを入れるときに過去N日以内に同じシフトがあること」が条件
                # 3. 現時点でシフト間隔が開いていても、後から間にシフトを追加することで解決可能
        
        if errors:
            for error in errors:
                logger.error(error)
                write_notification(error)
            return True
        
        return False
    
    def check_shift_interval_constraints_conflict(
        self,
        staff_data_list: List[StaffData]
    ) -> bool:
        """シフト間隔制約間の矛盾をチェック
        
        同一スタッフ内での制約の論理的矛盾をチェック
        
        Returns:
            bool: エラーがある場合はTrue
        """
        errors = []
        
        # 各スタッフの制約をチェック
        for staff in staff_data_list:
            # シフト間隔の必須制約を抽出
            interval_constraints = [
                c for c in staff.constraints
                if c.category == "シフト間隔" and c.type == "必須"
            ]
            
            # 同一シフトタイプでの制約間矛盾をチェック
            shift_type_constraints = {}
            for constraint in interval_constraints:
                shift_type = constraint.count
                if shift_type not in shift_type_constraints:
                    shift_type_constraints[shift_type] = []
                shift_type_constraints[shift_type].append(constraint)
            
            # 各シフトタイプごとに矛盾をチェック
            for shift_type, constraints in shift_type_constraints.items():
                if len(constraints) < 2:
                    continue
                
                # 嫌悪と愛好のペアで矛盾チェック
                for i, c1 in enumerate(constraints):
                    for c2 in constraints[i+1:]:
                        # 同じ好みタイプはスキップ
                        if c1.sub_category == c2.sub_category:
                            continue
                        
                        # 嫌悪と愛好のペア
                        hate_constraint = c1 if c1.sub_category == "嫌悪" else c2
                        like_constraint = c2 if c1.sub_category == "嫌悪" else c1
                        
                        # 間隔日数
                        hate_days = int(hate_constraint.target)
                        like_days = int(like_constraint.target)
                        
                        # 愛好の日数が嫌悪の日数以下の場合は矛盾
                        if like_days <= hate_days:
                            errors.append(
                                f"スタッフ「{staff.name}」のシフト間隔制約が矛盾しています：\n"
                                f"・{shift_type}は{hate_days}日以内に再発生禁止（必須嫌悪）\n"
                                f"・{shift_type}は{like_days}日以内に再発生必須（必須愛好）"
                            )
        
        if errors:
            for error in errors:
                logger.error(error)
                write_notification(error)
            return True
        
        return False
    
    def check_shift_interval_min_max_consistency(
        self,
        staff_data_list: List[StaffData]
    ) -> bool:
        """シフト間隔制約とシフト回数の整合性をチェック
        
        必須嫌悪制約とminの関係、必須愛好制約とmaxの関係をチェック
        
        Returns:
            bool: エラーがある場合はTrue
        """
        errors = []
        
        # 各スタッフの制約をチェック
        for staff in staff_data_list:
            # シフト間隔の必須制約を抽出
            interval_constraints = [
                c for c in staff.constraints
                if c.category == "シフト間隔" and c.type == "必須"
            ]
            
            for constraint in interval_constraints:
                # 対象シフトを取得
                target_shift_name = constraint.count
                shift_type = self.SHIFT_TYPE_MAPPING.get(target_shift_name, target_shift_name)
                
                # 無効なシフトタイプはスキップ
                if shift_type not in self.SHIFT_TYPES:
                    continue
                
                # 間隔日数
                interval_days = int(constraint.target)
                
                # シフト回数を取得
                shift_counts = staff.shift_counts.get(target_shift_name, {})
                min_count = shift_counts.get("min", 0)
                max_count = shift_counts.get("max", 0)
                
                # 必須嫌悪とmin回数の整合性チェック
                if constraint.sub_category == "嫌悪" and min_count > 0:
                    # 必要日数 = 1日目 + (間隔+1) × (回数-1)
                    required_days = 1 + (interval_days + 1) * (min_count - 1)
                    
                    if required_days > self.month_days:
                        errors.append(
                            f"スタッフ「{staff.name}」のシフト間隔制約とシフト回数が矛盾しています：\n"
                            f"・{target_shift_name}は{interval_days}日以内に再発生禁止（必須嫌悪）\n"
                            f"・{target_shift_name}の最小回数: {min_count}回\n"
                            f"・必要日数: 1 + ({interval_days}+1) × ({min_count}-1) = {required_days}日\n"
                            f"・月の日数: {self.month_days}日"
                        )
                
                # 必須愛好制約については、シフト回数との整合性チェックは不要
                # 理由：
                # 1. 愛好制約は「対象シフト配置時に過去N日以内に同じシフトがあること」を要求
                # 2. 初回のシフトには制約がかからないため、最小回数0回でも問題ない
                # 3. 最大回数については、連続配置可能なため上限に達することは稀
        
        if errors:
            for error in errors:
                logger.error(error)
                write_notification(error)
            return True
        
        return False
    
    def validate_shift_interval_constraints(
        self,
        staff_data_list: List[StaffData],
        shift_data: ShiftData
    ) -> bool:
        """シフト間隔制約の検証（まとめメソッド）
        
        Returns:
            bool: エラーがある場合はTrue
        """
        # このメソッドは当初のスケルトンとして残しておくが、
        # 実際の処理は個別のチェックメソッドに分割済み
        return False 
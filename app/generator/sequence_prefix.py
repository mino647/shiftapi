"""
sequence_prefix.py
シフト生成前の事前チェックを行うモジュール。
連続勤務に関する制約違反を検出し、エラー内容をユーザーに提示する。
"""

from typing import List, Optional, Tuple
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
from ..firebase_client import write_notification

Range = Tuple[int, int]
Ranges = List[Range]

class SequencePrefix:
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


    def check_constraints(
        self,
        staff_data_list: List[StaffData],
        shift_data: ShiftData,
    ) -> bool:
        """連続性に関する制約チェックを実行"""
        if self.validate_consecutive_holiday_constraints(staff_data_list, shift_data):
            return False
        
        if self._check_consecutive_work_limit(staff_data_list, shift_data):
            return False
        
        if self.check_holiday_constraints_conflict(staff_data_list, self.rule_data):
            return False
        
        if self.validate_shift_pattern(shift_data, staff_data_list, self.rule_data):
            return False
        
        if self.check_night_shift_holiday_conflict(staff_data_list, self.rule_data):
            return False
        
        if self.check_consecutive_shift_constraints(staff_data_list, shift_data, self.rule_data):
            return False

        if self.check_preference_night_shift_constraints(staff_data_list, self.rule_data):
            return False
        
        if self.check_consecutive_work_conflict(staff_data_list, self.rule_data):
            return False
        if self.validate_consecutive_work(shift_data, staff_data_list, self.rule_data):
            return False
        

        


        return True

    def _check_consecutive_work_limit(
        self,
        staff_data_list: List[StaffData],
        shift_data: ShiftData
    ) -> bool:
        """連続勤務制限のチェック
        
        各スタッフのシフトデータを分析し、連続勤務制限を超える可能性がある場合にエラーを出す。
        """
        for staff in staff_data_list:
            work_days = []
            remaining_holidays = staff.holiday_override or self.rule_data.holiday_count
            
            # シフトデータを配列に変換
            for day in range(1, self.month_days + 1):
                entry = next(
                    (e for e in shift_data.entries 
                     if e.staff_name == staff.name and e.day == day),
                    None
                )
                
                if not entry:
                    work_days.append(0)  # 空欄
                elif entry.shift_type in ["公", "休"]:
                    work_days.append(-1)  # 休み
                    remaining_holidays -= 1
                else:
                    work_days.append(1)  # 確定勤務
            
            # 連続勤務チェックの改善
            work_limit = self.rule_data.consecutive_work_limit
            i = 0
            while i < len(work_days):
                # 空欄または勤務を見つけた場合
                if work_days[i] >= 0:  # 0:空欄 or 1:勤務
                    # 区間内の勤務と空欄をカウント
                    work_count_in_section = 0  # 区間内の総勤務数
                    blank_count = 0  # 区間内の空欄数
                    start_day = i + 1  # 区間の開始日
                    
                    # 実際の連続勤務カウント用の変数
                    max_consecutive_days = 0  # 実際の最大連続勤務日数
                    current_consecutive = 0  # 現在の連続勤務日数
                    
                    # 休み(-1)が出現するまで区間として処理
                    section_start = i
                    while i < len(work_days) and work_days[i] >= 0:
                        if work_days[i] == 0:  # 空欄
                            blank_count += 1
                            # 空欄が出現したら連続勤務カウントをリセット
                            current_consecutive = 0
                        else:  # 確定勤務
                            work_count_in_section += 1
                            # 連続勤務カウントを増やす
                            current_consecutive += 1
                            # 最大連続勤務日数を更新
                            max_consecutive_days = max(max_consecutive_days, current_consecutive)
                        i += 1
                    
                    total_span = blank_count + work_count_in_section
                    
                    # 実際の連続勤務日数が制限を超えているかチェック
                    if max_consecutive_days > work_limit:
                        msg = (
                            f"スタッフ「{staff.name}」に{max_consecutive_days}日の連続勤務があります。\n"
                            f"（{start_day}日目から）"
                        )
                        logger.error(msg)
                        write_notification(msg)
                        return True
                    
                    # 空欄を含む区間で制限違反の可能性をチェック
                    if total_span > work_limit:
                        needed_holidays = total_span // (work_limit + 1)
                        if needed_holidays > remaining_holidays:
                            msg = (
                                f"スタッフ「{staff.name}」の{start_day}日目からの区間で連続勤務制限違反の可能性があります：\n"
                                f"・区間長: {total_span}日（空欄{blank_count}日＋確定勤務{work_count_in_section}日）\n"
                                f"・必要な休み日数: {needed_holidays}日\n"
                                f"・残り休日数: {remaining_holidays}日"
                            )
                            logger.error(msg)
                            write_notification(msg)
                            return True
                else:
                    i += 1
        return False

    def validate_consecutive_holiday_constraints(
        self, 
        staff_data_list: List[StaffData],
        shift_data: Optional[ShiftData] = None
    ) -> bool:
        """連続休暇制約のバリデーションチェック"""
        # グローバル制約のチェック
        for constraint in self.rule_data.preference_constraints:
            if constraint.category == "連続休暇" and constraint.times == "全員":
                if constraint.type == "必須":
                    # グローバルルール適用対象のスタッフのみをチェック
                    target_staff_list = [
                        staff for staff in staff_data_list 
                        if not staff.is_global_rule  # グローバルルール除外でないスタッフのみ
                    ]
                    
                    if constraint.sub_category == "回避":
                        count_value = constraint.count if constraint.count is not None else "単休"
                        base_days = self.KANJI_TO_NUMBER.get(count_value, 1)
                        if base_days == 1:  # 単休の場合はエラー
                            msg = "全員に対する単休の必須回避制約は設定できません。"
                            logger.error(msg)
                            write_notification(msg)
                            return True
                        elif constraint.target == "以下":  # 「以下」を使用する場合もエラー
                            msg = f"全員に対する{base_days}連休の必須回避制約では「以下」は設定できません。"
                            logger.error(msg)
                            write_notification(msg)
                            return True
                
                    elif constraint.sub_category == "推奨":
                        count_value = constraint.count if constraint.count is not None else "単休"
                        base_days = self.KANJI_TO_NUMBER.get(count_value, 1)
                        
                        if base_days == 1:  # 単休
                            if constraint.target != "丁度":
                                msg = "全員に対する単休の必須推奨制約は「丁度」のみ設定可能です。"
                                logger.error(msg)
                                write_notification(msg)
                                return True
                        else:  # 二連休以上
                            if constraint.target != "以下":
                                msg = f"全員に対する{base_days}連休の必須推奨制約は「以下」のみ設定可能です。"
                                logger.error(msg)
                                write_notification(msg)
                                return True

        # ローカル制約のチェック
        for staff in staff_data_list:
            for constraint in staff.constraints:
                if constraint.category == "連続休暇":
                    if constraint.type == "必須":
                        count_value = constraint.count if constraint.count is not None else "単休"
                        base_days = self.KANJI_TO_NUMBER.get(count_value, 1)
                        
                        if constraint.sub_category == "嫌悪":
                            if base_days == 1:  # 単休の場合
                                if constraint.target in ["以上", "以下"]:  # 単休の以上・以下はエラー
                                    msg = f"スタッフ「{staff.name}」の単休の必須嫌悪制約では「以上」「以下」は設定できません。"
                                    logger.error(msg)
                                    write_notification(msg)
                                    return True
                            
                            # シフトパターンのチェック
                            if shift_data:
                                consecutive_count = 0
                                for day in range(1, self.month_days + 1):
                                    entry = next(
                                        (e for e in shift_data.entries 
                                         if e.staff_name == staff.name and e.day == day),
                                        None
                                    )
                                    
                                    if entry and entry.shift_type == "公":
                                        consecutive_count += 1
                                    else:
                                        if consecutive_count > 0:
                                            if constraint.target == "以上" and consecutive_count >= base_days:
                                                msg = (
                                                    f"スタッフ「{staff.name}」に{base_days}連休以上の嫌悪制約がありますが、\n"
                                                    f"{consecutive_count}連休が設定されています。"
                                                )
                                                logger.error(msg)
                                                write_notification(msg)
                                                return True
                                            elif constraint.target == "丁度" and consecutive_count == base_days:
                                                msg = (
                                                    f"スタッフ「{staff.name}」に{base_days}連休の嫌悪制約がありますが、\n"
                                                    f"{consecutive_count}連休が設定されています。"
                                                )
                                                logger.error(msg)
                                                write_notification(msg)
                                                return True
                                            elif constraint.target == "以下" and consecutive_count <= base_days:
                                                msg = (
                                                    f"スタッフ「{staff.name}」に{base_days}連休以下の嫌悪制約がありますが、\n"
                                                    f"{consecutive_count}連休が設定されています。"
                                                )
                                                logger.error(msg)
                                                write_notification(msg)
                                                return True
                                        consecutive_count = 0
                                
                                # 月末まで連続している場合の処理
                                if consecutive_count > 0:
                                    if constraint.target == "以上" and consecutive_count >= base_days:
                                        msg = (
                                            f"スタッフ「{staff.name}」に{base_days}連休以上の嫌悪制約がありますが、\n"
                                            f"月末に{consecutive_count}連休が設定されています。"
                                        )
                                        logger.error(msg)
                                        write_notification(msg)
                                        return True
                                    elif constraint.target == "丁度" and consecutive_count == base_days:
                                        msg = (
                                            f"スタッフ「{staff.name}」に{base_days}連休の嫌悪制約がありますが、\n"
                                            f"月末に{consecutive_count}連休が設定されています。"
                                        )
                                        logger.error(msg)
                                        write_notification(msg)
                                        return True
                        
                        elif constraint.sub_category == "愛好":
                            if base_days == 1:  # 単休
                                if constraint.target != "丁度":
                                    msg = f"スタッフ「{staff.name}」の単休の必須愛好制約は「丁度」のみ設定可能です。"
                                    logger.error(msg)
                                    write_notification(msg)
                                    return True

        return False

    def calculate_holiday_range(self, constraint_type, sub_category, target, count):
        """連休制約の有効範囲を計算
        
        Args:
            constraint_type (str): "必須" or "選好"
            sub_category (str): "愛好"/"推奨" or "嫌悪"/"回避"
            target (str): "以上" or "丁度" or "以下"
            count (Union[str, int]): 基準となる連休日数
        
        Returns:
            tuple: (最小連休数, 最大連休数)
        """
        MAX_HOLIDAY_CONSECUTIVE = 7
        
        # countを整数に変換
        if isinstance(count, str):
            count = self.KANJI_TO_NUMBER.get(count, 1)
        else:
            count = int(count)

        if constraint_type == "必須":
            if sub_category in ["愛好", "推奨"]:
                if target == "以上":
                    return count, MAX_HOLIDAY_CONSECUTIVE
                elif target == "丁度":
                    return count, count
                elif target == "以下":
                    return 1, count
            elif sub_category in ["嫌悪", "回避"]:
                if target == "以上":
                    return 1, count - 1
                elif target == "丁度":
                    return 2 if count == 1 else 1, MAX_HOLIDAY_CONSECUTIVE
                elif target == "以下":
                    return count + 1, MAX_HOLIDAY_CONSECUTIVE
        else:  # 選好制約
            if sub_category in ["推奨", "愛好"]:
                if target == "以上":
                    return count, MAX_HOLIDAY_CONSECUTIVE
                elif target == "丁度":
                    return count, count
                elif target == "以下":
                    return 1, count
            elif sub_category in ["回避", "嫌悪"]:
                if target == "以上":
                    return 1, count - 1
                elif target == "丁度":
                    return 2 if count == 1 else 1, MAX_HOLIDAY_CONSECUTIVE
                elif target == "以下":
                    return count + 1, MAX_HOLIDAY_CONSECUTIVE
        
        return None  # 無効な組み合わせの場合

    def has_overlap(self, range1, range2):
        """2つの範囲に重なりがあるかチェック
        
        Args:
            range1 (tuple): (min1, max1)
            range2 (tuple): (min2, max2)
        
        Returns:
            bool: 重なりがある場合True
        """
        min1, max1 = range1
        min2, max2 = range2
        return max(min1, min2) <= min(max1, max2)  # notを削除
    
    def check_holiday_constraints_conflict(self, staff_data_list, rule_data) -> bool:
        """連休制約の競合をチェック
        
        Args:
            staff_data_list (List[StaffData]): スタッフデータのリスト
            rule_data (RuleData): ルールデータ
        
        Returns:
            bool: エラーがある場合はTrue
        """
        errors = []
        
        # 1. グローバル制約とローカル制約の競合チェック
        for staff in staff_data_list:
            # グローバルルール除外スタッフはスキップ
            if staff.is_global_rule:
                continue
                
            for staff_constraint in staff.constraints:
                if staff_constraint.category == "連続休暇":
                    for global_constraint in rule_data.preference_constraints:
                        if global_constraint.category == "連続休暇":
                            # 必須制約同士の場合のみチェック
                            if staff_constraint.type == "必須" and global_constraint.type == "必須":
                                staff_range = self.calculate_holiday_range(
                                    staff_constraint.type,
                                    staff_constraint.sub_category,
                                    staff_constraint.target,
                                    staff_constraint.count
                                )
                                global_range = self.calculate_holiday_range(
                                    global_constraint.type,
                                    global_constraint.sub_category,
                                    global_constraint.target,
                                    global_constraint.count
                                )
                                
                                if staff_range is None or global_range is None:
                                    continue
                                
                                # 範囲の重なりをチェック
                                if self.has_overlap(staff_range, global_range):
                                    # 丁度の制約がある場合の追加チェック
                                    if staff_constraint.target == "丁度" or global_constraint.target == "丁度":
                                        # 一方が嫌悪/回避で、もう一方が愛好/推奨の場合
                                        if (staff_constraint.sub_category in ["嫌悪", "回避"] and 
                                            global_constraint.sub_category in ["愛好", "推奨"]) or \
                                           (global_constraint.sub_category in ["嫌悪", "回避"] and 
                                            staff_constraint.sub_category in ["愛好", "推奨"]):
                                            if staff_constraint.count == global_constraint.count:
                                                errors.append(
                                                    f"スタッフ「{staff.name}」の{staff_constraint.count}"
                                                    f"{staff_constraint.target}の{staff_constraint.sub_category}制約が、"
                                                    f"全体ルールの{global_constraint.count}"
                                                    f"{global_constraint.target}の{global_constraint.sub_category}制約と競合しています。"
                                                )
                                else:  # 範囲が重ならない場合
                                    errors.append(
                                        f"スタッフ「{staff.name}」の{staff_constraint.count}"
                                        f"{staff_constraint.target}の{staff_constraint.sub_category}制約が、"
                                        f"全体ルールの{global_constraint.count}"
                                        f"{global_constraint.target}の{global_constraint.sub_category}制約と競合しています。"
                                    )

        # 2. スタッフ内での制約の競合チェック
        for staff in staff_data_list:
            holiday_constraints = [c for c in staff.constraints if c.category == "連続休暇"]
            for i, c1 in enumerate(holiday_constraints):
                for c2 in holiday_constraints[i+1:]:
                    # 必須制約同士の場合のみチェック
                    if c1.type == "必須" and c2.type == "必須":
                        range1 = self.calculate_holiday_range(
                            c1.type,
                            c1.sub_category,
                            c1.target,
                            c1.count
                        )
                        range2 = self.calculate_holiday_range(
                            c2.type,
                            c2.sub_category,
                            c2.target,
                            c2.count
                        )
                        
                        if range1 is None or range2 is None:
                            continue
                        
                        # 範囲の重なりをチェック
                        if self.has_overlap(range1, range2):
                            # 丁度の制約がある場合の追加チェック
                            if c1.target == "丁度" or c2.target == "丁度":
                                # 一方が嫌悪/回避で、もう一方が愛好/推奨の場合
                                if (c1.sub_category in ["嫌悪", "回避"] and c2.sub_category in ["愛好", "推奨"]) or \
                                   (c2.sub_category in ["嫌悪", "回避"] and c1.sub_category in ["愛好", "推奨"]):
                                    if c1.count == c2.count:  # 同じ連休数を指定している場合
                                        errors.append(
                                            f"スタッフ「{staff.name}」の制約同士が競合しています：\n"
                                            f"・{c1.count}{c1.target}の{c1.sub_category}制約\n"
                                            f"・{c2.count}{c2.target}の{c2.sub_category}制約"
                                        )
                        else:  # 範囲が重ならない場合
                            errors.append(
                                f"スタッフ「{staff.name}」の制約同士が競合しています：\n"
                                f"・{c1.count}{c1.target}の{c1.sub_category}制約\n"
                                f"・{c2.count}{c2.target}の{c2.sub_category}制約"
                            )

        if errors:
            for error in errors:
                logger.error(error)
                write_notification(error)
            return True
            
        return False

    def validate_shift_pattern(self, shift_data, staff_data_list, rule_data) -> bool:
        """シフトパターンと制約の整合性をチェック"""
        errors = []
        
        for staff in staff_data_list:
            # スタッフの連休制約を取得（必須のみ）
            holiday_constraints = [
                c for c in staff.constraints 
                if c.category == "連続休暇" and c.type == "必須"
            ]
            
            # グローバル制約も追加（ただしグローバルルール除外スタッフには適用しない）
            if not staff.is_global_rule:  # この条件を追加
                global_constraints = [
                    c for c in rule_data.preference_constraints
                    if c.category == "連続休暇" and c.type == "必須"
                ]
            else:
                global_constraints = []  # グローバルルール除外スタッフには空リストを設定
            
            # 連休パターンの検出（既存のまま）
            consecutive_holidays = []
            current_sequence = []
            prev_type = None
            
            for day in range(1, self.month_days + 1):
                entry = next(
                    (e for e in shift_data.entries 
                     if e.staff_name == staff.name and e.day == day),
                    None
                )
                current_type = entry.shift_type if entry else "_"
                
                if current_type == "公":
                    current_sequence.append(day)
                else:
                    if current_sequence:
                        if prev_type not in ["_", None] and current_type not in ["_", None]:
                            consecutive_holidays.append(("確定", current_sequence.copy()))
                        else:
                            consecutive_holidays.append(("未確定", current_sequence.copy()))
                        current_sequence = []
                prev_type = current_type
            
            if current_sequence:
                if prev_type not in ["_", None]:
                    consecutive_holidays.append(("確定", current_sequence.copy()))
                else:
                    consecutive_holidays.append(("未確定", current_sequence.copy()))
            
            # 制約チェック
            for constraint in holiday_constraints + global_constraints:
                # countを数値に変換
                count = self.KANJI_TO_NUMBER.get(constraint.count, 1) if isinstance(constraint.count, str) else constraint.count
                
                for status, sequence in consecutive_holidays:
                    length = len(sequence)
                    
                    if constraint.sub_category in ["愛好", "推奨"]:
                        if constraint.target == "以上":
                            if status == "確定" and length < count:
                                errors.append(
                                    f"スタッフ「{staff.name}」の{sequence[0]}日目からの確定した{length}連休が"
                                    f"{count}連休以上の{constraint.sub_category}制約に違反しています。"
                                )
                        elif constraint.target in ["以下", "丁度"]:
                            if length > count:
                                errors.append(
                                    f"スタッフ「{staff.name}」の{sequence[0]}日目からの{length}連休が"
                                    f"{count}連休{constraint.target}の{constraint.sub_category}制約に違反しています。"
                                )

                    else:  # 嫌悪/回避
                        if constraint.target == "以上":
                            if length >= count:
                                errors.append(
                                    f"スタッフ「{staff.name}」の{sequence[0]}日目からの{length}連休が"
                                    f"{count}連休以上の{constraint.sub_category}制約に違反しています。"
                                )
                        elif constraint.target == "以下":
                            if status == "確定" and length < count:
                                errors.append(
                                    f"スタッフ「{staff.name}」の{sequence[0]}日目からの確定した{length}連休が"
                                    f"{count}連休以下の{constraint.sub_category}制約に違反しています。"
                                )
                        elif constraint.target == "丁度":
                            if status == "確定" and length == count:
                                errors.append(
                                    f"スタッフ「{staff.name}」の{sequence[0]}日目からの確定した{length}連休が"
                                    f"{count}連休丁度の{constraint.sub_category}制約に違反しています。"
                                )

        if errors:
            for error in errors:
                logger.error(error)
                write_notification(error)
            return True
            
        return False

    def check_night_shift_holiday_conflict(
        self,
        staff_data_list: List[StaffData],
        rule_data: RuleData
    ) -> bool:
        """夜勤回数と休日数の実現可能性をチェック"""
        
        for staff in staff_data_list:
            # 夜勤の最小回数を取得
            night_shift_min = staff.shift_counts.get("夜勤", {}).get("min", 0)
            if night_shift_min == 0:
                continue
            
            # 休日数を取得（override優先）
            holiday_count = staff.holiday_override or rule_data.holiday_count
            
            # 連休の最小値を取得
            min_consecutive_days = 1
            constraint_reason = "制約なし"
            
            # スタッフ個別の制約とグローバル制約（除外スタッフには適用しない）を結合
            all_constraints = staff.constraints
            if not staff.is_global_rule:  # グローバルルール除外でないスタッフのみにグローバル制約を追加
                all_constraints = all_constraints + rule_data.preference_constraints
            
            for constraint in all_constraints:
                if constraint.type == "必須":
                    if constraint.category == "連続休暇":
                        # 単休丁度嫌悪
                        if (constraint.sub_category == "嫌悪" and 
                            constraint.count == "単休" and 
                            constraint.target == "丁度"):
                            min_consecutive_days = 2
                            constraint_reason = "単休禁止制約"
                        
                        # X連休以下嫌悪
                        elif (constraint.sub_category == "嫌悪" and 
                              constraint.target == "以下"):
                            count = (self.KANJI_TO_NUMBER.get(constraint.count, 1) 
                                   if isinstance(constraint.count, str) 
                                   else (constraint.count if constraint.count is not None else 1))
                            if count + 1 > min_consecutive_days:
                                min_consecutive_days = count + 1
                                constraint_reason = f"{count}連休以下嫌悪制約"
                        
                        # X連休丁度愛好
                        elif (constraint.sub_category == "愛好" and 
                              constraint.target == "丁度"):
                            count = (self.KANJI_TO_NUMBER.get(constraint.count, 1) 
                                   if isinstance(constraint.count, str) 
                                   else (constraint.count if constraint.count is not None else 1))
                            if count > min_consecutive_days:
                                min_consecutive_days = count
                                constraint_reason = f"{count}連休丁度愛好制約"
                        
                        # X連休以上愛好
                        elif (constraint.sub_category == "愛好" and 
                              constraint.target == "以上"):
                            count = (self.KANJI_TO_NUMBER.get(constraint.count, 1) 
                                   if isinstance(constraint.count, str) 
                                   else (constraint.count if constraint.count is not None else 1))
                            if count > min_consecutive_days:
                                min_consecutive_days = count
                                constraint_reason = f"{count}連休以上愛好制約"
            
            # 必要な休日数を計算
            required_holidays = (night_shift_min - 1) * min_consecutive_days
            
            # 実現可能性チェック
            if required_holidays > holiday_count:
                msg = (
                    f"スタッフ「{staff.name}」の制約が実現不可能です：\n"
                    f"・夜勤回数: {night_shift_min}回（月末を除き各夜勤後に休暇必要）\n"
                    f"・最小連休日数: {min_consecutive_days}日（{constraint_reason}）\n"
                    f"・計算式: ({night_shift_min}回 - 1) × {min_consecutive_days}日 = {required_holidays}日\n"
                    f"・設定された休日数: {holiday_count}日\n\n"
                    f"休日数を{required_holidays}日以上に増やすか、夜勤回数または連休制約を緩和してください。"
                )
                logger.error(msg)
                write_notification(msg)
                return True
            
        return False
    
    def check_consecutive_shift_constraints(self, staff_data_list: List[StaffData], shift_data: ShiftData, rule_data: RuleData) -> bool:
        """連続シフト制約のチェック
        
        Returns:
            bool: エラーがある場合True
        """
        if not shift_data.entries:
            return False
        
        # エントリを日付でソート
        sorted_entries = sorted(shift_data.entries, key=lambda x: (x.staff_name, x.day))
        
        # スタッフごとのエントリを作成
        entries_by_staff = {}
        for entry in sorted_entries:
            if entry.staff_name not in entries_by_staff:
                entries_by_staff[entry.staff_name] = []
            entries_by_staff[entry.staff_name].append(entry)
        
        # グローバルの連続シフト制約を取得
        global_constraints = [
            c for c in rule_data.preference_constraints
            if c.category == "連続シフト" and c.type == "必須"
        ]
        
        for staff_name, entries in entries_by_staff.items():
            staff = next(s for s in staff_data_list if s.name == staff_name)
            if staff.is_global_rule:  # グローバルルール適用対象のみチェック
                continue
            
            for constraint in global_constraints:
                target_shift = str(constraint.count)
                consecutive_count = (
                    self.KANJI_TO_NUMBER.get(str(constraint.final), 1)
                    if isinstance(constraint.final, str)
                    else constraint.final if constraint.final is not None else 1
                )
                
                # 夜勤の場合
                if target_shift == "夜勤":
                    # 区間の開始位置を探す
                    i = 0
                    while i < len(entries):
                        if entries[i].shift_type == "×":
                            # 区間の終了位置を探す
                            j = i + 1
                            night_count = 1  # ×で開始なので1からスタート
                            while j < len(entries):
                                if entries[j].day != entries[j-1].day + 1:
                                    break  # 日付が連続していない場合は区間終了
                                
                                current_shift = str(entries[j].shift_type)
                                if current_shift not in ["／", "×", "公"]:
                                    break  # リセット対象のシフトが出現したら区間終了
                                if current_shift == "／":
                                    night_count += 1
                                j += 1
                            
                            # 連続回数チェック
                            if constraint.target == "以上" and night_count >= consecutive_count:
                                msg = (
                                    f"スタッフ「{staff_name}」の{entries[i].day}日からの夜勤が"
                                    f"{consecutive_count}回以上連続しています（{night_count}回）"
                                )
                                write_notification(msg)
                                return True
                            i = j
                        else:
                            i += 1
                
                # 夜勤以外の場合
                else:
                    shift_type = self.SHIFT_TYPE_FIXMAPPING.get(str(target_shift), str(target_shift))
                    consecutive = 0
                    start_day = None
                    
                    for i, entry in enumerate(entries):
                        current_shift = self.SHIFT_TYPE_FIXMAPPING.get(
                            str(entry.shift_type), str(entry.shift_type)
                        )
                        
                        # 連続日でない場合はリセット
                        if i > 0 and entry.day != entries[i-1].day + 1:
                            consecutive = 0
                            continue
                        
                        if current_shift == shift_type:
                            if consecutive == 0:
                                start_day = entry.day
                            consecutive += 1
                            
                            # 連続回数チェック
                            if constraint.target == "以上" and consecutive >= consecutive_count:
                                msg = (
                                    f"スタッフ「{staff_name}」の{start_day}日からの{target_shift}が"
                                    f"{consecutive_count}回以上連続しています（{consecutive}回）"
                                )
                                write_notification(msg)
                                return True
                        else:
                            consecutive = 0
        
        return False
    
    def check_preference_night_shift_constraints(self, staff_data_list: List[StaffData], rule_data: RuleData) -> bool:
        """選好連続シフト夜勤制約のチェック
        
        Returns:
            bool: エラーがある場合True
        """
        # グローバルの連続シフト制約を取得
        global_constraints = [
            c for c in rule_data.preference_constraints
            if c.category == "連続シフト" and c.type == "選好"
        ]
        
        # 夜勤の連続シフト制約があるかチェック
        night_shift_constraints = [
            c for c in global_constraints
            if str(c.count) == "夜勤"
        ]
        
        if night_shift_constraints:
            msg = (
                "選好の連続シフト夜勤制約が設定されています。\n"
                "この制約は現在サポートされていません。\n"
                "必須の連続シフト制約を使用してください。"
            )
            logger.error(msg)
            write_notification(msg)
            return True
            
        return False
    
    def calculate_ranges(self, constraint, work_limit: int) -> Optional[Ranges]:
        """制約から許容範囲を計算
        Args:
            constraint: 制約オブジェクト
            work_limit: 連続勤務上限日数
        Returns:
            Optional[Ranges]: 許容範囲のリスト。丁度の嫌悪は複数範囲、通常は1つの範囲を返す
        """
        count = self.KANJI_TO_NUMBER.get(str(constraint.count), 1) if isinstance(constraint.count, str) else constraint.count
        
        if constraint.sub_category in ["愛好", "推奨"]:
            if constraint.target == "以上":
                return [(count, work_limit)]  # work_limitで制限
            elif constraint.target == "以下":
                return [(1, min(count, work_limit))]  # work_limitで制限
            elif constraint.target == "丁度":
                if count > work_limit:  # work_limitを超える丁度は不可
                    return None
                return [(count, count)]
        else:  # 嫌悪/回避
            if constraint.target == "以上":
                return [(1, min(count - 1, work_limit))]  # work_limitで制限
            elif constraint.target == "以下":
                return [(min(count + 1, work_limit), work_limit)]  # work_limitで制限
            elif constraint.target == "丁度":
                if count == 1:
                    return [(2, work_limit)]
                return [(1, count - 1), (min(count + 1, work_limit), work_limit)]  # work_limitで制限
        
        return None

    def has_overlap_ranges(self, ranges1: Ranges, ranges2: Ranges) -> bool:
        """複数の範囲間で重なりがあるかチェック"""
        for r1 in ranges1:
            for r2 in ranges2:
                if max(r1[0], r2[0]) <= min(r1[1], r2[1]):
                    return True
        return False

    def check_constraint_conflict(
        self, 
        c1, 
        c2, 
        staff_name: str,
        work_limit: int
    ) -> bool:
        """2つの制約間の矛盾をチェック
        Returns:
            bool: 矛盾がある場合True
        """
        ranges1 = self.calculate_ranges(c1, work_limit)
        ranges2 = self.calculate_ranges(c2, work_limit)
        
        if ranges1 is None or ranges2 is None:
            return False
        
        if not self.has_overlap_ranges(ranges1, ranges2):
            # カテゴリが異なる場合（連続勤務 vs 日勤帯連勤）
            if c1.category != c2.category:
                msg = (
                    f"スタッフ「{staff_name}」の{c1.category}と{c2.category}の制約が競合しています：\n"
                    f"・{c1.category}: {c1.count}{c1.target}の{c1.sub_category}制約\n"
                    f"・{c2.category}: {c2.count}{c2.target}の{c2.sub_category}制約"
                )
            # グローバル制約との競合の場合
            elif hasattr(c2, 'is_global') and c2.is_global:
                msg = (
                    f"スタッフ「{staff_name}」の{c1.category}制約が全体ルールと競合しています：\n"
                    f"・個別ルール: {c1.count}{c1.target}の{c1.sub_category}制約\n"
                    f"・全体ルール: {c2.count}{c2.target}の{c2.sub_category}制約"
                )
            # 同じカテゴリ内での競合（現在のメッセージ）
            else:
                msg = (
                    f"スタッフ「{staff_name}」の{c1.category}制約同士が競合しています：\n"
                    f"・{c1.count}{c1.target}の{c1.sub_category}制約\n"
                    f"・{c2.count}{c2.target}の{c2.sub_category}制約"
                )
            
            logger.error(msg)
            write_notification(msg)
            return True
        
        return False

    def check_consecutive_work_conflict(self, staff_data_list: List[StaffData], rule_data: RuleData) -> bool:
        """連続勤務制約の矛盾をチェック"""
        logger.debug("=== 連続勤務制約の矛盾チェック ===")
        work_limit = rule_data.consecutive_work_limit

                # 追加: 単体の制約でwork_limitを超えるものをチェック
        for staff in staff_data_list:
            if staff.is_global_rule:
                continue
                
            all_constraints = (
                [c for c in staff.constraints if c.type == "必須" and c.category in ["連続勤務", "日勤帯連勤"]] +
                [c for c in rule_data.preference_constraints if c.type == "必須" and c.category in ["連続勤務", "日勤帯連勤"]]
            )
            
            for constraint in all_constraints:
                count = self.KANJI_TO_NUMBER.get(str(constraint.count), 1) if isinstance(constraint.count, str) else (constraint.count if constraint.count is not None else 1)
                if constraint.sub_category in ["愛好", "推奨"] and count > work_limit:
                    msg = (
                        f"スタッフ「{staff.name}」の{constraint.category}制約が連続勤務上限を超えています：\n"
                        f"・{count}{constraint.target}の{constraint.sub_category}制約\n"
                        f"・連続勤務上限: {work_limit}日"
                    )
                    logger.error(msg)
                    write_notification(msg)
                    return True


        # グローバル制約の取得
        global_consecutive = [
            c for c in rule_data.preference_constraints
            if c.category == "連続勤務" and c.type == "必須"
        ]
        global_dayshift = [
            c for c in rule_data.preference_constraints
            if c.category == "日勤帯連勤" and c.type == "必須"
        ]

        for staff in staff_data_list:
            if staff.is_global_rule:
                continue

            # ローカル制約の取得
            local_consecutive = [
                c for c in staff.constraints
                if c.category == "連続勤務" and c.type == "必須"
            ]
            local_dayshift = [
                c for c in staff.constraints
                if c.category == "日勤帯連勤" and c.type == "必須"
            ]

            # 1. ローカル制約同士のチェック
            # 1.1 連続勤務制約同士
            for i, c1 in enumerate(local_consecutive):
                for c2 in local_consecutive[i+1:]:
                    if self.check_constraint_conflict(c1, c2, staff.name, work_limit):
                        return True

            # 1.2 日勤帯連勤制約同士
            for i, c1 in enumerate(local_dayshift):
                for c2 in local_dayshift[i+1:]:
                    if self.check_constraint_conflict(c1, c2, staff.name, work_limit):
                        return True

            # 1.3 連続勤務 vs 日勤帯連勤
            for c1 in local_consecutive:
                for c2 in local_dayshift:
                    if self.check_constraint_conflict(c1, c2, staff.name, work_limit):
                        return True

            # 2. ローカル vs グローバルのチェック
            # 2.1 連続勤務
            for local_c in local_consecutive:
                for global_c in global_consecutive:
                    if self.check_constraint_conflict(local_c, global_c, staff.name, work_limit):
                        return True

            # 2.2 日勤帯連勤
            for local_c in local_dayshift:
                for global_c in global_dayshift:
                    if self.check_constraint_conflict(local_c, global_c, staff.name, work_limit):
                        return True

            # 2.3 ローカル連続勤務 vs グローバル日勤帯連勤
            for local_c in local_consecutive:
                for global_c in global_dayshift:
                    if self.check_constraint_conflict(local_c, global_c, staff.name, work_limit):
                        return True

            # 2.4 ローカル日勤帯連勤 vs グローバル連続勤務
            for local_c in local_dayshift:
                for global_c in global_consecutive:
                    if self.check_constraint_conflict(local_c, global_c, staff.name, work_limit):
                        return True

        return False

    def validate_consecutive_work(self, shift_data, staff_data_list, rule_data) -> bool:
        """連続勤務パターンと制約の整合性をチェック"""
        errors = []
        
        for staff in staff_data_list:
            # スタッフの制約を取得（必須のみ）
            consecutive_constraints = [
                c for c in staff.constraints 
                if c.category == "連続勤務" and c.type == "必須"
            ]
            dayshift_constraints = [
                c for c in staff.constraints 
                if c.category == "日勤帯連勤" and c.type == "必須"
            ]
            
            # グローバル制約も追加（ただしグローバルルール除外スタッフには適用しない）
            if not staff.is_global_rule:
                consecutive_constraints.extend([
                    c for c in rule_data.preference_constraints
                    if c.category == "連続勤務" and c.type == "必須"
                ])
                dayshift_constraints.extend([
                    c for c in rule_data.preference_constraints
                    if c.category == "日勤帯連勤" and c.type == "必須"
                ])
            
            # 連続勤務パターンの検出
            consecutive_work = []
            dayshift_work = []
            current_consecutive = []
            current_dayshift = []
            prev_type = None
            
            for day in range(1, self.month_days + 1):
                entry = next(
                    (e for e in shift_data.entries 
                     if e.staff_name == staff.name and e.day == day),
                    None
                )
                current_type = entry.shift_type if entry else "_"
                
                # 連続勤務の場合（▲日▼／×☆）
                if current_type in ["▲", "日", "▼", "／", "×", "☆"]:
                    current_consecutive.append((day, current_type))
                else:  # 公休の場合
                    if current_consecutive:
                        consecutive_work.append(current_consecutive.copy())
                        # 公休で囲まれた勤務も確定した連続勤務として扱う
                        if prev_type == "公" and len(current_consecutive) > 0:
                            consecutive_work.append(current_consecutive.copy())
                    current_consecutive = []
                
                # 日勤帯連勤の場合（▲日▼☆）
                if current_type in ["▲", "日", "▼", "☆"]:
                    current_dayshift.append((day, current_type))
                else:  # 公休の場合
                    if current_dayshift:
                        dayshift_work.append(current_dayshift.copy())
                        # 公休で囲まれた勤務も確定した連続勤務として扱う
                        if prev_type == "公" and len(current_dayshift) > 0:
                            dayshift_work.append(current_dayshift.copy())
                    current_dayshift = []
                
                prev_type = current_type
            
            # 最後のシーケンスも追加
            if current_consecutive:
                consecutive_work.append(current_consecutive.copy())
                # 最後が公休で終わる場合も確定した連続勤務として扱う
                if prev_type == "公":
                    consecutive_work.append(current_consecutive.copy())
            if current_dayshift:
                dayshift_work.append(current_dayshift.copy())
                if prev_type == "公":
                    dayshift_work.append(current_dayshift.copy())
            
            # 制約チェック
            for constraint in consecutive_constraints:
                count = self.KANJI_TO_NUMBER.get(str(constraint.count), 1) if isinstance(constraint.count, str) else (constraint.count if constraint.count is not None else 1)
                
                for sequence in consecutive_work:
                    length = len(sequence)
                    shift_types = [s[1] for s in sequence]
                    start_day = sequence[0][0]
                    
                    if constraint.sub_category in ["愛好", "推奨"]:
                        if constraint.target == "以下" and length > count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}連続勤務が"
                                f"{count}連勤以下の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
                        elif constraint.target == "以上" and length < count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}連続勤務が"
                                f"{count}連勤以上の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
                        elif constraint.target == "丁度" and length != count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}連続勤務が"
                                f"{count}連勤丁度の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
                    else:  # 嫌悪/回避
                        if constraint.target == "以上" and length >= count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}連続勤務が"
                                f"{count}連勤以上の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
                        elif constraint.target == "以下" and length < count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}連続勤務が"
                                f"{count}連勤以下の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
                        elif constraint.target == "丁度" and length == count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}連続勤務が"
                                f"{count}連勤丁度の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
            
            # 日勤帯連勤の制約チェック
            for constraint in dayshift_constraints:
                count = self.KANJI_TO_NUMBER.get(str(constraint.count), 1) if isinstance(constraint.count, str) else (constraint.count if constraint.count is not None else 1)
                
                for sequence in dayshift_work:
                    length = len(sequence)
                    shift_types = [s[1] for s in sequence]
                    start_day = sequence[0][0]
                    
                    if constraint.sub_category in ["愛好", "推奨"]:
                        if constraint.target == "以下" and length > count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}日勤帯連続勤務が"
                                f"{count}連勤以下の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
                        elif constraint.target == "以上" and length < count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}日勤帯連続勤務が"
                                f"{count}連勤以上の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
                        elif constraint.target == "丁度" and length != count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}日勤帯連続勤務が"
                                f"{count}連勤丁度の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
                    else:  # 嫌悪/回避
                        if constraint.target == "以上" and length >= count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}日勤帯連続勤務が"
                                f"{count}連勤以上の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
                        elif constraint.target == "以下" and length < count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}日勤帯連続勤務が"
                                f"{count}連勤以下の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )
                        elif constraint.target == "丁度" and length == count:
                            errors.append(
                                f"スタッフ「{staff.name}」の{start_day}日目からの{length}日勤帯連続勤務が"
                                f"{count}連勤丁度の{constraint.sub_category}制約に違反しています。"
                                f"（シフト: {' '.join(shift_types)}）"
                            )

        if errors:
            for error in errors:
                logger.error(error)
                write_notification(error)
            return True
            
        return False

    
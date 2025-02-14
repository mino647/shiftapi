"""
basic_prefix.py
シフト生成前の事前チェックを行うモジュール。
基本的な制約違反を検出し、エラー内容をユーザーに提示する。
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
from ..firebase_client import write_notification  # 追加

class BasicPrefix:
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
        """基本的な制約チェックを実行"""
        if self._check_empty_staff_list(staff_data_list):
            return False
            
        if self._check_shift_count_conflicts(staff_data_list, shift_data):
            return False
        
        if self.check_total_shifts(staff_data_list, shift_data):
            return False

        if self._check_shift_type_requirements(staff_data_list, shift_data):
            return False
        
        if self._check_shift_constraints(staff_data_list, shift_data):
            return False
        
        if self._check_staff_constraints(staff_data_list, shift_data):
            return False

        return True

    def _check_empty_staff_list(self, staff_data_list: List[StaffData]) -> bool:
        """スタッフリストが空かどうかをチェック"""
        if len(staff_data_list) == 0:
            msg = "スタッフデータが空です。シフトを生成できません。"
            logger.error(msg)
            write_notification(msg)
            return True
        return False

    def _check_shift_count_conflicts(self, staff_data_list: List[StaffData], shift_data: ShiftData) -> bool:
        """
        勤務回数の矛盾をチェック：
        1. 最小回数 > 最大回数
        2. 勤務区分のminの合計 > 出勤日数
        3. 勤務区分のmaxの合計 < 出勤日数
        ※ 夜勤は1回につき2コマ使用するため、min/maxを2倍してカウント
        ※ ☆シフトは出勤日数から除外
        Returns:
            bool: エラーがある場合True、なければFalse
        """
        for staff_data in staff_data_list:
            # ☆シフトの数を計算
            star_shift_count = len([
                entry for entry in shift_data.entries
                if entry.staff_name == staff_data.name and entry.shift_type == "☆"
            ])

            # 月の日数から休日数と☆シフトを引いて出勤日数を計算
            holiday_count = staff_data.holiday_override or self.rule_data.holiday_count
            working_days = self.month_days - holiday_count - star_shift_count

            # 各勤務区分のmin/maxの合計を計算（夜勤は2倍）
            total_min = sum(
                limits.get('min', 0) * (2 if shift_type == "夜勤" else 1)
                for shift_type, limits in staff_data.shift_counts.items()
            )
            total_max = sum(
                limits.get('max', 9999) * (2 if shift_type == "夜勤" else 1)
                for shift_type, limits in staff_data.shift_counts.items()
            )

            # min > maxのチェック
            for shift_type, limits in staff_data.shift_counts.items():
                min_val = limits.get('min', 0)
                max_val = limits.get('max', 9999)
                if min_val > max_val:
                    msg = (
                        f"スタッフ「{staff_data.name}」のシフトタイプ「{shift_type}」において、\n"
                        f"最小回数({min_val})が最大回数({max_val})を超えています。"
                    )
                    logger.error(f"勤務回数の矛盾: {msg}")
                    write_notification(msg)
                    return True

            # minの合計が出勤日数を超過
            if total_min > working_days:
                msg = (
                    f"スタッフ「{staff_data.name}」の勤務回数設定に矛盾があります：\n"
                    f"・出勤日数：{working_days}日（☆シフト{star_shift_count}日を除く）\n"
                    f"・最小回数の合計：{total_min}コマ（夜勤は2コマとしてカウント）\n\n"
                    f"最小回数の合計が出勤可能日数を超えています。\n"
                    f"休日数か各勤務の最小回数を見直してください。"
                )
                logger.error(f"勤務回数の矛盾: {msg}")
                write_notification(msg)
                return True

            # maxの合計が出勤日数に満たない
            if total_max < working_days:
                msg = (
                    f"スタッフ「{staff_data.name}」の勤務回数設定に矛盾があります：\n"
                    f"・出勤日数：{working_days}日（☆シフト{star_shift_count}日を除く）\n"
                    f"・最大回数の合計：{total_max}コマ（夜勤は2コマとしてカウント）\n\n"
                    f"最大回数の合計が出勤必要日数に足りません。\n"
                    f"休日数か各勤務の最大回数を見直してください。"
                )
                logger.error(f"勤務回数の矛盾: {msg}")
                write_notification(msg)
                return True

        return False
    def check_total_shifts(
        self,
        staff_data_list: List[StaffData],
        shift_data: ShiftData
    ) -> bool:
        """
        総稼働コマ数が必要コマ数の範囲内であることをチェック
        
        必要コマ数は以下のように計算:
        - weekday_staffとsunday_staffが整数の場合: 固定値として扱う
        - 小数点がある場合（例: 2.5）: 切り下げを最小値、切り上げを最大値として扱う
        
        Returns:
            bool: エラーがある場合True、なければFalse
        """
        total_shifts = self.calculate_total_shifts(staff_data_list, shift_data)
        
        # 必要最小・最大コマ数を計算
        base_required = self.calculate_required_shifts(shift_data)
        
        # weekday_staffとsunday_staffの小数点チェック
        has_range = (
            (isinstance(self.rule_data.weekday_staff, float) and self.rule_data.weekday_staff % 1 == 0.5) or
            (isinstance(self.rule_data.sunday_staff, float) and self.rule_data.sunday_staff % 1 == 0.5)
        )
        
        if has_range:
            # 範囲指定の場合（小数点がある場合）
            min_required = base_required
            # 0.5刻みの場合、平日と日曜の数に応じて最大値を計算
            max_required = base_required
            if isinstance(self.rule_data.weekday_staff, float) and self.rule_data.weekday_staff % 1 == 0.5:
                max_required += self.weekday_count
            if isinstance(self.rule_data.sunday_staff, float) and self.rule_data.sunday_staff % 1 == 0.5:
                max_required += self.sunday_count
            
            if total_shifts < min_required:
                error_msg = (
                    f"エラー: 総稼働コマ数({total_shifts})が"
                    f"必要最小コマ数({min_required})より少ないため、"
                    f"シフトを組むことができません。"
                )
                write_notification(error_msg)
                return True
            
            if total_shifts > max_required:
                warning_msg = (
                    f"総稼働コマ数({total_shifts})が必要最大コマ数({max_required})を"
                    f"{total_shifts - max_required}コマ超過しています。\n\n"
                    f"シフトを組むためには以下のいずれかの調整が必要です：\n"
                    f"・休日数の増加\n"
                    f"・必要人数の見直し"
                )
                write_notification(warning_msg)
                return True
            
        else:
            # 固定値の場合（現状の実装通り）
            if total_shifts < base_required:
                error_msg = (
                    f"エラー: 総稼働コマ数({total_shifts})が"
                    f"必要コマ数({base_required})より少ないため、"
                    f"シフトを組むことができません。"
                )
                write_notification(error_msg)
                return True
            
            if total_shifts > base_required:
                surplus = total_shifts - base_required
                warning_msg = (
                    f"総稼働コマ数({total_shifts})が必要コマ数({base_required})を"
                    f"{surplus}コマ超過しています。\n\n"
                    f"シフトを組むためには以下のいずれかの調整が必要です：\n"
                    f"・休日数の増加\n"
                    f"・必要人数の見直し"
                )
                write_notification(warning_msg)
                return True
        
        return False

    def calculate_required_shifts(self, shift_data: ShiftData) -> int:
        """月の必要コマ数を計算する"""
        # 基本の1日あたりの必要人数を取得
        early_staff = self.rule_data.early_staff
        late_staff = self.rule_data.late_staff
        night_staff = self.rule_data.night_staff  # 夜勤と夜勤明けは同じ
        weekday_staff = self.rule_data.weekday_staff
        sunday_staff = self.rule_data.sunday_staff
        
        # 通常日の計算
        base_staff = early_staff + late_staff + (night_staff * 2)  # 夜勤と夜勤明け
        
        # 平日と日曜の必要コマ数を計算（最小値を使用）
        if isinstance(weekday_staff, float) and weekday_staff % 1 == 0.5:
            weekday_total = (base_staff + int(weekday_staff - 0.5)) * self.weekday_count
        else:
            weekday_total = (base_staff + int(weekday_staff)) * self.weekday_count
        
        if isinstance(sunday_staff, float) and sunday_staff % 1 == 0.5:
            sunday_total = (base_staff + int(sunday_staff - 0.5)) * self.sunday_count
        else:
            sunday_total = (base_staff + int(sunday_staff)) * self.sunday_count
        
        return weekday_total + sunday_total

    def calculate_total_shifts(self, staff_data_list: List[StaffData], shift_data: ShiftData) -> int:
        """総稼働コマ数を計算する"""
        # 基本の総稼働コマ数を計算
        total = 0
        for staff in staff_data_list:
            # holiday_overrideがある場合はそれを使用、なければrule_dataの休日数
            holiday_count = (staff.holiday_override 
                           if staff.holiday_override is not None 
                           else self.rule_data.holiday_count)
            total += self.month_days - holiday_count
        
        # ☆の数をカウントして引く
        star_count = sum(1 for entry in shift_data.entries if entry.shift_type == "☆")
        total -= star_count
        
        return total
    
    def _check_shift_type_requirements(
        self,
        staff_data_list: List[StaffData],
        shift_data: ShiftData
    ) -> bool:
        """
        各勤務区分の必要数とスタッフの総シフト可能回数を比較
        
        以下の2つのケースをチェック：
        1. 総max回数 < 必要数 の場合（シフトが足りない）
        2. 総min回数 > 必要数 の場合（必要以上のシフトを組まざるを得ない）
        
        Returns:
            bool: エラーがある場合True、なければFalse
        """
        # 基本の1日あたりの必要人数を取得
        required_shifts = {
            "早番": self.rule_data.early_staff * self.month_days,
            "遅番": self.rule_data.late_staff * self.month_days,
            "夜勤": self.rule_data.night_staff * self.month_days,  # 夜勤と夜勤明けは同じ
            "日勤": 0  # 日勤は平日と日曜で計算
        }
        
        # 日勤の必要数を計算
        if isinstance(self.rule_data.weekday_staff, float) and self.rule_data.weekday_staff % 1 == 0.5:
            required_shifts["日勤"] = int(self.rule_data.weekday_staff - 0.5) * self.weekday_count
        else:
            required_shifts["日勤"] = int(self.rule_data.weekday_staff) * self.weekday_count
        
        if isinstance(self.rule_data.sunday_staff, float) and self.rule_data.sunday_staff % 1 == 0.5:
            required_shifts["日勤"] += int(self.rule_data.sunday_staff - 0.5) * self.sunday_count
        else:
            required_shifts["日勤"] += int(self.rule_data.sunday_staff) * self.sunday_count
        
        # スタッフの総シフト可能回数を計算（最小値と最大値）
        total_available = {
            "早番": {
                "min": sum(staff.shift_counts.get("早番", {}).get("min", 0) for staff in staff_data_list),
                "max": sum(staff.shift_counts.get("早番", {}).get("max", 0) for staff in staff_data_list)
            },
            "遅番": {
                "min": sum(staff.shift_counts.get("遅番", {}).get("min", 0) for staff in staff_data_list),
                "max": sum(staff.shift_counts.get("遅番", {}).get("max", 0) for staff in staff_data_list)
            },
            "夜勤": {
                "min": sum(staff.shift_counts.get("夜勤", {}).get("min", 0) for staff in staff_data_list),
                "max": sum(staff.shift_counts.get("夜勤", {}).get("max", 0) for staff in staff_data_list)
            },
            "日勤": {
                "min": sum(staff.shift_counts.get("日勤", {}).get("min", 0) for staff in staff_data_list),
                "max": sum(staff.shift_counts.get("日勤", {}).get("max", 0) for staff in staff_data_list)
            }
        }

        has_error = False
        error_messages = []

        for shift_type in ["早番", "日勤", "遅番", "夜勤"]:
            # 最大値が必要数より少ない場合（従来のチェック）
            if total_available[shift_type]["max"] < required_shifts[shift_type]:
                shortage = required_shifts[shift_type] - total_available[shift_type]["max"]
                error_messages.append(
                    f"・{shift_type}: 必要数{required_shifts[shift_type]}コマに対し"
                    f"最大{total_available[shift_type]['max']}コマしか割り当てできません"
                    f"（{shortage}コマ不足）"
                )
                has_error = True
            
            # 最小値が必要数より多い場合（新規チェック）
            elif total_available[shift_type]["min"] > required_shifts[shift_type]:
                excess = total_available[shift_type]["min"] - required_shifts[shift_type]
                error_messages.append(
                    f"・{shift_type}: 必要数{required_shifts[shift_type]}コマに対し"
                    f"最小{total_available[shift_type]['min']}コマを割り当てることはできません"
                    f"（{excess}コマ超過）"
                )
                has_error = True

        if has_error:
            error_msg = (
                f"以下の勤務区分で必要数と勤務回数の制約が矛盾しています：\n\n"
                f"{chr(10).join(error_messages)}\n\n"
                f"シフトを組むためには以下のいずれかの調整が必要です：\n"
                f"・各スタッフフの勤務可能回数（最小/最大）の見直し\n"
                f"・必要人数の見直し"
            )
            write_notification(error_msg)
            return True

        return False

    def _check_shift_constraints(
        self,
        staff_data_list: List[StaffData],
        shift_data: ShiftData
    ) -> bool:
        """
        各日のシフト制約をチェック
        
        以下の制約違反を検出：
        1. 各勤務の確定数が必要数を超過（over）
        2. 夜勤の入り・明けの不一致
        3. 日勤の確定数が許容範囲外
        4. 未確定枠が残り必要数より少ない（解なし）
        """
        for day in range(1, self.month_days + 1):
            # その日の曜日を計算（0=月曜, 6=日曜）
            weekday = calendar.weekday(self.year, self.month, day)
            
            # 日曜日かどうかで必要人数を設定
            required = {
                "早番": self.rule_data.early_staff,
                "遅番": self.rule_data.late_staff,
                "夜勤": self.rule_data.night_staff,
                "日勤": {
                    "min": math.floor(self.rule_data.sunday_staff if weekday == 6 else self.rule_data.weekday_staff),
                    "max": math.ceil(self.rule_data.sunday_staff if weekday == 6 else self.rule_data.weekday_staff)
                }
            }

            # その日の確定シフトを集計
            confirmed_shifts = {
                "早番": 0,
                "日勤": 0,
                "遅番": 0,
                "夜勤": {"入り": 0, "明け": 0}
            }
            confirmed_holidays = 0
            
            for entry in shift_data.entries:
                if entry.day == day:
                    if entry.shift_type == "▲":
                        confirmed_shifts["早番"] += 1
                    elif entry.shift_type == "日":
                        confirmed_shifts["日勤"] += 1
                    elif entry.shift_type in ["▼", "▽"]:
                        confirmed_shifts["遅番"] += 1
                    elif entry.shift_type == "／":
                        confirmed_shifts["夜勤"]["入り"] += 1
                    elif entry.shift_type == "×":
                        confirmed_shifts["夜勤"]["明け"] += 1
                    elif entry.shift_type in ["公", "休"]:
                        confirmed_holidays += 1

            # 1. 固定人数の勤務（早番・遅番・夜勤）の超過チェック
            for shift_type in ["早番", "遅番"]:
                if confirmed_shifts[shift_type] > required[shift_type]:
                    msg = f"{day}日目: {shift_type}が{required[shift_type]}人必要ですが、{confirmed_shifts[shift_type]}人確定しています"
                    logger.error(msg)
                    write_notification(msg)
                    return True

            # 2. 夜勤の入り・明けの超過チェック
            if confirmed_shifts["夜勤"]["入り"] > required["夜勤"]:
                msg = f"{day}日目: 夜勤が{required['夜勤']}人必要ですが、夜勤の入り（／）が{confirmed_shifts['夜勤']['入り']}人確定しています"
                logger.error(msg)
                write_notification(msg)
                return True

            if confirmed_shifts["夜勤"]["明け"] > required["夜勤"]:
                msg = f"{day}日目: 夜勤が{required['夜勤']}人必要ですが、夜勤明け（×）が{confirmed_shifts['夜勤']['明け']}人確定しています"
                logger.error(msg)
                write_notification(msg)
                return True

            # 3. 日勤の範囲チェック（確定数が最大値を超えている場合のみエラー）
            if confirmed_shifts["日勤"] > required["日勤"]["max"]:
                msg = f"{day}日目: 日勤は最大{required['日勤']['max']}人までですが、{confirmed_shifts['日勤']}人確定しています"
                logger.error(msg)
                write_notification(msg)
                return True

            # 4. 残り必要数と未確定枠の比較
            needed = {
                "早番": required["早番"] - confirmed_shifts["早番"],
                "日勤": (
                    required["日勤"]["min"] - confirmed_shifts["日勤"]
                    if confirmed_shifts["日勤"] < required["日勤"]["min"]
                    else 0
                ),
                "遅番": required["遅番"] - confirmed_shifts["遅番"],
                "夜勤入り": required["夜勤"] - confirmed_shifts["夜勤"]["入り"],
                "夜勤明け": required["夜勤"] - confirmed_shifts["夜勤"]["明け"]
            }

            # 残り必要数の合計（夜勤入りと夜勤明けは別々にカウント）
            total_needed = (
                needed["早番"] +
                needed["日勤"] +
                needed["遅番"] +
                needed["夜勤入り"] +
                needed["夜勤明け"]
            )

            # 未確定の枠数 = 全スタッフ - (確定済みの全勤務 + 休暇)
            remaining_slots = len(staff_data_list) - (
                confirmed_shifts["早番"] +
                confirmed_shifts["日勤"] +
                confirmed_shifts["遅番"] +
                confirmed_shifts["夜勤"]["入り"] +
                confirmed_shifts["夜勤"]["明け"] +
                confirmed_holidays
            )

            if total_needed > remaining_slots:
                msg = (
                    f"{day}日目: 残り{remaining_slots}枠に対し、"
                    f"早番あと{needed['早番']}人、"
                    f"日勤あと{needed['日勤']}人、"
                    f"遅番あと{needed['遅番']}人、"
                    f"夜勤入り(／)あと{needed['夜勤入り']}人、"
                    f"夜勤明け(×)あと{needed['夜勤明け']}人"
                    f"が必要で、合計{total_needed}人分必要です"
                )
                logger.error(msg)
                write_notification(msg)
                return True

        return False
    
    def _check_staff_constraints(
        self,
        staff_data_list: List[StaffData],
        shift_data: ShiftData
    ) -> bool:
        """
        スタッフごとの制約をチェック
        
        1. 各勤務区分の上限（max）超過
        2. 休日数の上限超過
        3. 各勤務区分の下限（min）実現可能性
        4. 休日数の下限実現可能性
        """
        for staff in staff_data_list:
            # 現在の確定状況を集計
            confirmed_shifts = {
                "早番": 0,
                "日勤": 0,
                "遅番": 0,
                "夜勤": 0,
                "休み": 0  # 公休、休み、☆を含む
            }
            
            # 残りの空欄数をカウント
            remaining_slots = self.month_days
            
            for entry in shift_data.entries:
                if entry.staff_name == staff.name:
                    remaining_slots -= 1
                    if entry.shift_type == "▲":
                        confirmed_shifts["早番"] += 1
                    elif entry.shift_type == "日":
                        confirmed_shifts["日勤"] += 1
                    elif entry.shift_type in ["▼", "▽"]:
                        confirmed_shifts["遅番"] += 1
                    elif entry.shift_type == "／":
                        confirmed_shifts["夜勤"] += 1
                    elif entry.shift_type in ["公", "休",]:
                        confirmed_shifts["休み"] += 1

            # 1. 勤務区分の上限チェック
            for shift_type, counts in staff.shift_counts.items():
                max_count = counts.get('max', 0)
                current_count = confirmed_shifts[shift_type]
                if current_count > max_count:
                    msg = (
                        f"スタッフ「{staff.name}」の{shift_type}が"
                        f"上限{max_count}回を超えて{current_count}回になっています"
                    )
                    logger.error(msg)
                    write_notification(msg)
                    return True

            # 2. 休日数の上限チェック
            holiday_limit = staff.holiday_override or self.rule_data.holiday_count
            if confirmed_shifts["休み"] > holiday_limit:
                msg = (
                    f"スタッフ「{staff.name}」の休日数が"
                    f"上限{holiday_limit}日を超えて{confirmed_shifts['休み']}日になっています"
                )
                logger.error(msg)
                write_notification(msg)
                return True

            # 残りコマ数の計算を先に行う
            total_confirmed = sum(confirmed_shifts.values())
            remaining_slots = self.month_days - total_confirmed

            # 3. 休日数の実現可能性チェック
            if confirmed_shifts["休み"] + remaining_slots < holiday_limit:
                msg = (
                    f"スタッフ「{staff.name}」の休日数：\n"
                    f"・必要日数：{holiday_limit}日\n"
                    f"・現在の確定日数：{confirmed_shifts['休み']}日\n"
                    f"・残りの空欄：{remaining_slots}コマ\n\n"
                    f"残り{holiday_limit - confirmed_shifts['休み']}日分の休日を確保することができません"
                )
                logger.error(msg)
                write_notification(msg)
                return True

            # 4. 勤務区分のminチェック
            available_slots = remaining_slots - (holiday_limit - confirmed_shifts["休み"])
            for shift_type, counts in staff.shift_counts.items():
                min_count = counts.get('min', 0)
                if min_count > 0:
                    # 夜勤は／の数だけをカウント
                    current_count = (
                        len([e for e in shift_data.entries 
                             if e.staff_name == staff.name 
                             and e.shift_type == "／"]) if shift_type == "夜勤"
                        else confirmed_shifts[shift_type]
                    )
                    # 夜勤は残り回数×2、他は通常通り
                    needed_slots = 2 * (min_count - current_count) if shift_type == "夜勤" else (min_count - current_count)
                    
                    if available_slots < needed_slots:
                        msg = (
                            f"スタッフ「{staff.name}」の{shift_type}：\n"
                            f"・必要回数：最低{min_count}回\n"
                            f"・現在の確定回数：{current_count}回\n"
                            f"・残りの空欄：{remaining_slots}コマ\n"
                            f"  → ただし、休日をあと{holiday_limit - confirmed_shifts['休み']}日確保する必要があるため\n"
                            f"  → 実際に勤務に使えるのは{available_slots}コマ"
                            + (f"（夜勤1回につき2コマ必要）" if shift_type == "夜勤" else "") + "\n\n"
                            f"したがって残り{min_count - current_count}回分の{shift_type}を割り当てることができません"
                        )
                        logger.error(msg)
                        write_notification(msg)
                        return True

        return False

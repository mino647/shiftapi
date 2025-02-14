"""
pattern_prefix.py
シフト生成前の事前チェックを行うモジュール。
曜日パターンに関する制約違反を検出し、エラー内容をユーザーに提示する。
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
from ..firebase_client import write_notification

class PatternPrefix:
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
        """パターンに関する制約チェックを実行"""
        if self._check_weekday_constraints(staff_data_list, shift_data):
            return False
        
        if self._check_pairing_constraints(staff_data_list):
            return False
        
        if self._check_staff_exists(staff_data_list, shift_data):
            return False
        
        if self._check_shift_pattern_constraints(staff_data_list, shift_data, self.rule_data):
            return False
        
        if self._check_night_shift_pattern(shift_data, staff_data_list):
            return False
        
        if self._check_pair_overlap_constraints(staff_data_list, shift_data):
            return False
        
        if self._check_separate_constraints(staff_data_list, shift_data):
            return False
        if self._check_shift_pattern_feasibility(staff_data_list, self.rule_data, shift_data):
            return False
        if self._check_shift_pattern_conflicts(staff_data_list, self.rule_data):
            return False
        if self._check_global_shift_pattern_mandatory(self.rule_data):
            return False
        return True

    def _check_weekday_constraints(self, staff_data_list: List[StaffData], shift_data: ShiftData) -> bool:
        """曜日制約をチェック"""
        # 既存シフトの収集（曜日ごと）
        existing_shifts = {
            weekday: {
                'shifts': {shift_type: [] for shift_type in SHIFT_TYPE_FIXMAPPING.values()},
                'days': {}  # 日付ごとの実際のシフト状況を保持
            }
            for weekday in range(7)
        }
        
        # 既存シフトを収集
        for entry in shift_data.entries:
            weekday = datetime(self.year, self.month, entry.day).weekday()
            shift_type = SHIFT_TYPE_FIXMAPPING.get(entry.shift_type, entry.shift_type)
            existing_shifts[weekday]['shifts'][shift_type].append(entry.staff_name)
            
            # 日付ごとのシフト状況も記録
            if entry.day not in existing_shifts[weekday]['days']:
                existing_shifts[weekday]['days'][entry.day] = []
            existing_shifts[weekday]['days'][entry.day].append(entry.staff_name)

        # 日付ごとのチェック
        for weekday in range(7):
            for day, staff_list in existing_shifts[weekday]['days'].items():
                # シフトタイプごとのスタッフを集計
                staff_by_type = {}
                
                # 1. シフトエントリからの集計
                for entry in shift_data.entries:
                    if entry.day == day:
                        entry_type = SHIFT_TYPE_FIXMAPPING.get(entry.shift_type, entry.shift_type)
                        if entry_type not in staff_by_type:
                            staff_by_type[entry_type] = {"staff": set(), "reasons": {}}
                        staff_by_type[entry_type]["staff"].add(entry.staff_name)
                        staff_by_type[entry_type]["reasons"][entry.staff_name] = "entry"

                # 2. 曜日制約からの集計（全ての週 + 第N週）
                for staff in staff_data_list:
                    for constraint in staff.constraints:
                        if (constraint.category != "曜日希望" or 
                            constraint.type != "必須" or 
                            not constraint.target or 
                            not constraint.target.endswith("曜日") or 
                            not constraint.times):
                            continue

                        constraint_weekday = "月火水木金土日".index(constraint.target.replace("曜日", ""))
                        if constraint_weekday != weekday:
                            continue

                        shift_type = SHIFT_TYPE_FIXMAPPING.get(constraint.times, constraint.times)
                        
                        # 全ての週の場合
                        if constraint.count == "全て":
                            if shift_type not in staff_by_type:
                                staff_by_type[shift_type] = {"staff": set(), "reasons": {}}
                            staff_by_type[shift_type]["staff"].add(staff.name)
                            staff_by_type[shift_type]["reasons"][staff.name] = ("constraint", constraint)
                        
                        # 第N週の場合、その日が該当週かチェック
                        elif constraint.count:
                            try:
                                nth = ["第一", "第二", "第三", "第四", "第五"].index(constraint.count)
                                weekday_occurrences = [
                                    d for d in range(1, self.month_days + 1)
                                    if datetime(self.year, self.month, d).weekday() == weekday
                                ]
                                if nth < len(weekday_occurrences) and day == weekday_occurrences[nth]:
                                    if shift_type not in staff_by_type:
                                        staff_by_type[shift_type] = {"staff": set(), "reasons": {}}
                                    staff_by_type[shift_type]["staff"].add(staff.name)
                                    staff_by_type[shift_type]["reasons"][staff.name] = ("constraint", constraint)
                            except ValueError:
                                continue

                # 3. シフトタイプごとの必要人数チェック
                for shift_type, data in staff_by_type.items():
                    staff_set = data["staff"]
                    reasons = data["reasons"]
                    required_staff = 0
                    if shift_type == "早番":
                        required_staff = self.rule_data.early_staff
                    elif shift_type == "遅番":
                        required_staff = self.rule_data.late_staff
                    elif shift_type == "夜勤":
                        required_staff = self.rule_data.night_staff
                    elif shift_type in ["休み", "公休"]:
                        # 出勤必要人数を計算（日勤は曜日によって参照値が異なり、小数点切り捨て）
                        day_shift_staff = math.floor(
                            self.rule_data.sunday_staff if weekday == 6
                            else self.rule_data.weekday_staff
                        )
                        total_required = (
                            self.rule_data.early_staff +  # 早番
                            day_shift_staff +  # 日勤（切り捨て）
                            self.rule_data.late_staff +  # 遅番
                            self.rule_data.night_staff * 2  # 夜勤入り + 夜勤明け
                        )
                        required_staff = len(staff_data_list) - total_required
                    else:  # 日勤
                        required_staff = math.floor(
                            self.rule_data.sunday_staff if weekday == 6
                            else self.rule_data.weekday_staff
                        )

                    if len(staff_set) > required_staff:
                        # エラーメッセージ用に分類して収集
                        entry_staff = []
                        constraint_staff = []
                        
                        for staff_name in sorted(staff_set):
                            reason = reasons.get(staff_name)
                            if reason == "entry":
                                entry_staff.append(staff_name)
                            elif reason[0] == "constraint":
                                constraint = reason[1]
                                weekday_str = "月火水木金土日"[weekday] + "曜日"
                                if constraint.count == "全て":
                                    constraint_staff.append(f"{staff_name}（全て{weekday_str}）")
                                else:
                                    constraint_staff.append(f"{staff_name}（{constraint.count}{weekday_str}）")

                        msg = (
                            f"{day}日の{shift_type}が必要人数を超過しています\n"
                            f"希望　{', '.join(entry_staff)}\n"
                            f"制約　{', '.join(constraint_staff)}\n"
                            f"合計　{len(staff_set)}人（必要人数{required_staff}人）"
                        )
                        logger.error(msg)
                        write_notification(msg)
                        return True

                    # 曜日制約と希望シフトの矛盾チェック（ここに追加）
                    for staff_name in staff_set:
                        if reasons.get(staff_name) == "entry":  # 希望シフトの場合
                            staff = next((s for s in staff_data_list if s.name == staff_name), None)
                            if staff:
                                for constraint in staff.constraints:
                                    if (constraint.category == "曜日希望" and 
                                        constraint.type == "必須" and 
                                        constraint.target and 
                                        constraint.target.endswith("曜日") and 
                                        constraint.times):
                                        
                                        constraint_weekday = "月火水木金土日".index(constraint.target.replace("曜日", ""))
                                        if constraint_weekday == weekday:
                                            required_type = SHIFT_TYPE_FIXMAPPING.get(constraint.times, constraint.times)
                                            
                                            # 全ての週の制約チェック
                                            if constraint.count == "全て":
                                                if required_type != shift_type:
                                                    weekday_str = "月火水木金土日"[weekday] + "曜日"
                                                    msg = (
                                                        f"スタッフ「{staff_name}」の曜日制約と希望シフトが矛盾しています：\n"
                                                        f"・制約：全ての{weekday_str}は{required_type}\n"
                                                        f"・希望：{day}日（{weekday_str}）に{shift_type}"
                                                    )
                                                    logger.error(msg)
                                                    write_notification(msg)
                                                    return True
                                            
                                            # 第N週の制約チェック
                                            elif constraint.count:
                                                try:
                                                    nth = ["第一", "第二", "第三", "第四", "第五"].index(constraint.count)
                                                    weekday_occurrences = [
                                                        d for d in range(1, self.month_days + 1)
                                                        if datetime(self.year, self.month, d).weekday() == weekday
                                                    ]
                                                    if nth < len(weekday_occurrences) and day == weekday_occurrences[nth]:
                                                        if required_type != shift_type:
                                                            weekday_str = "月火水木金土日"[weekday] + "曜日"
                                                            msg = (
                                                                f"スタッフ「{staff_name}」の曜日制約と希望シフトが矛盾しています：\n"
                                                                f"・制約：{constraint.count}{weekday_str}は{required_type}\n"
                                                                f"・希望：{day}日（{constraint.count}{weekday_str}）に{shift_type}"
                                                            )
                                                            logger.error(msg)
                                                            write_notification(msg)
                                                            return True
                                                except ValueError:
                                                    continue

        return False
    
    def _check_pairing_constraints(self, staff_data_list: List[StaffData]) -> bool:
        """ペアリング制約の実現可能性をチェック"""
        for staff in staff_data_list:
            for constraint in staff.constraints:
                if constraint.category != "ペアリング":
                    continue

                # シフトタイプの検証
                if not constraint.count or not constraint.target:
                    logger.error(f"ペアリング制約でシフト区分が未指定です: {staff.name}")
                    continue

                # 対象スタッフの存在確認
                target_staff = next(
                    (s for s in staff_data_list if s.name == constraint.sub_category),
                    None
                )
                if target_staff is None:
                    logger.error(f"ペアリング対象のスタッフが見つかりません: {constraint.sub_category}")
                    continue

                # 主体と客体の最大シフト回数を取得
                source_type = "夜勤" if constraint.count in ["夜勤明け", "明け"] else constraint.count
                target_type = "夜勤" if constraint.target in ["夜勤明け", "明け"] else constraint.target
                
                source_max = staff.shift_counts.get(source_type, {}).get('max', 0)
                target_max = target_staff.shift_counts.get(target_type, {}).get('max', 0)

                # 必要回数の判定
                if constraint.times == "全て":
                    if source_max == 0 or target_max == 0:
                        error_msg = (
                            f"ペアリング制約を満たすことができません：\n"
                            f"・{staff.name}の{constraint.count}の最大回数: {source_max}回\n"
                            f"・{constraint.sub_category}の{constraint.target}の最大回数: {target_max}回\n"
                            f"・必要回数: 全て"
                        )
                        QMessageBox.critical(None, "エラー", error_msg)
                        return True
                else:
                    # 回数指定がある場合
                    if constraint.times:
                        timecombo = self.KANJI_TO_NUMBER.get(
                            constraint.times.replace("まで", ""),
                            0
                        )
                        if timecombo <= 0:
                            logger.error(f"無効な回数指定です: {constraint.times}")
                            continue
                    else:
                        logger.error(f"回数が指定されていません")
                        continue

                    if source_max < timecombo or target_max < timecombo:
                        error_msg = (
                            f"ペアリング制約を満たすことができません：\n"
                            f"・{staff.name}の{constraint.count}の最大回数: {source_max}回\n"
                            f"・{constraint.sub_category}の{constraint.target}の最大回数: {target_max}回\n"
                            f"・必要回数: {timecombo}回"
                        )
                        QMessageBox.critical(None, "エラー", error_msg)
                        return True

        return False

    def _check_staff_exists(self, staff_data_list: List[StaffData], shift_data: ShiftData) -> bool:
        """制約に含まれるスタッフの実在チェック"""
        for staff in staff_data_list:
            for constraint in staff.constraints:
                if constraint.category not in ["ペアリング", "セパレート"]:
                    continue
                
                if not any(s.name == constraint.sub_category for s in staff_data_list):
                    error_msg = (
                        f"スタッフ「{staff.name}」の{constraint.category}制約で指定された\n"
                        f"スタッフ「{constraint.sub_category}」が存在しません。"
                    )
                    QMessageBox.critical(None, "エラー", error_msg)
                    return True
                
        return False
    
    def _check_night_shift_pattern(self, shift_data: ShiftData, staff_data_list: List[StaffData]) -> bool:
        """
        夜勤パターン（／→×→公）が崩れていないかチェック
        また、夜勤max=0のスタッフに夜勤明け(×)が入っていないかチェック
        Returns:
            bool: エラーがある場合True、なければFalse
        """
        if not shift_data.entries:
            return False

        # 夜勤max=0のスタッフをチェック
        for staff in staff_data_list:
            night_shift_max = staff.shift_counts.get("夜勤", {}).get("max", 0)
            if night_shift_max == 0:
                # このスタッフの夜勤明け(×)をチェック
                invalid_entries = [
                    entry for entry in shift_data.entries
                    if entry.staff_name == staff.name and entry.shift_type == "×"
                ]
                if invalid_entries:
                    days = [entry.day for entry in invalid_entries]
                    msg = (
                        f"スタッフ「{staff.name}」は夜勤の最大回数が0回に設定されていますが、\n"
                        f"{days}日に夜勤明け(×)が入っています。"
                    )
                    QMessageBox.critical(None, "夜勤パターンエラー", msg)
                    return True

        # 以下、既存の夜勤パターンチェック
        entries_by_staff = {}
        for entry in shift_data.entries:
            if entry.staff_name not in entries_by_staff:
                entries_by_staff[entry.staff_name] = {}
            entries_by_staff[entry.staff_name][entry.day] = entry.shift_type

        for staff, days in entries_by_staff.items():
            # 月初の処理: 1日目が×なら2日目は空欄か公休
            if 1 in days and days[1] == '×':
                if 2 in days and days[2] != '公':
                    msg = f"スタッフ「{staff}」の月初パターンが不正です：\n1日目が夜勤明け(×)の場合、2日目は公休である必要があります。"
                    QMessageBox.critical(None, "夜勤パターンエラー", msg)
                    return True

            # 通常の夜勤パターンチェック
            for day in sorted(days.keys()):
                if days[day] == '／':
                    # 夜勤の翌日が入力済みで、×以外ならエラー
                    if day + 1 in days and days[day + 1] != '×':
                        msg = f"スタッフ「{staff}」の{day}日目の夜勤パターンが不正です：\n夜勤(／)の翌日は夜勤明け(×)である必要があります。"
                        QMessageBox.critical(None, "夜勤パターンエラー", msg)
                        return True
                    
                    # 夜勤の2日後が入力済みで、公休以外ならエラー
                    if day + 2 in days and days[day + 2] != '公':
                        msg = f"スタッフ「{staff}」の{day}日目の夜勤パターンが不正です：\n夜勤明け(×)の翌日は公休である必要があります。"
                        QMessageBox.critical(None, "夜勤パターンエラー", msg)
                        return True

                elif days[day] == '×':
                    # 夜勤明けの前日が入力済みで、／以外ならエラー
                    if day - 1 in days and days[day - 1] != '／':
                        msg = f"スタッフ「{staff}」の{day}日目の夜勤パターンが不正です：\n夜勤明け(×)の前日は夜勤(／)である必要があります。"
                        QMessageBox.critical(None, "夜勤パターンエラー", msg)
                        return True

        return False

    def _check_shift_pattern_constraints(self, staff_data_list: List[StaffData], shift_data: ShiftData, rule_data: RuleData) -> bool:
        """
        シフトパターン制約のチェック
        Returns:
            bool: エラーがある場合True
        """
        # 1. グローバル制約間の矛盾チェック
        global_patterns = [
            (c.count, c.target, c.sub_category) 
            for c in rule_data.preference_constraints 
            if c.category == "シフトパターン" and c.type == "必須"
        ]
        
        for from_shift1, to_shift1, sub_cat1 in global_patterns:
            for from_shift2, to_shift2, sub_cat2 in global_patterns:
                if from_shift1 == from_shift2 and to_shift1 == to_shift2:
                    if sub_cat1 != sub_cat2:  # 同じパターンで異なる制約（推奨と回避）
                        msg = (
                            f"グローバル制約で矛盾が検出されました：\n"
                            f"・{from_shift1}→{to_shift1}のパターンが\n"
                            f"・{sub_cat1}と{sub_cat2}の両方で指定されています"
                        )
                        QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                        write_notification(msg)
                        return True

        # 2. スタッフ内の制約矛盾チェック
        for staff in staff_data_list:
            staff_patterns = [
                (c.count, c.target, c.sub_category)
                for c in staff.constraints
                if c.category == "シフトパターン" and c.type == "必須"
            ]
            
            for from_shift1, to_shift1, sub_cat1 in staff_patterns:
                for from_shift2, to_shift2, sub_cat2 in staff_patterns:
                    if from_shift1 == from_shift2 and to_shift1 == to_shift2:
                        if sub_cat1 != sub_cat2:  # 同じパターンで異なる制約（愛好と嫌悪）
                            msg = (
                                f"スタッフ「{staff.name}」の制約で矛盾が検出されました：\n"
                                f"・{from_shift1}→{to_shift1}のパターンが\n"
                                f"・{sub_cat1}と{sub_cat2}の両方で指定されています"
                            )
                            QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                            write_notification(msg)
                            return True

        # 3. グローバルとスタッフの制約矛盾チェック
        for staff in staff_data_list:
            if not staff.is_global_rule:  # グローバルルール除外フラグがFalseの場合のみチェック
                staff_patterns = [
                    (c.count, c.target, c.sub_category)
                    for c in staff.constraints
                    if c.category == "シフトパターン" and c.type == "必須"
                ]
                
                for g_from, g_to, g_sub in global_patterns:
                    for s_from, s_to, s_sub in staff_patterns:
                        if g_from == s_from and g_to == s_to:
                            if (g_sub in ["回避"] and s_sub in ["愛好"]) or \
                               (g_sub in ["推奨"] and s_sub in ["嫌悪"]):
                                msg = (
                                    f"スタッフ「{staff.name}」の制約がグローバル制約と矛盾しています：\n"
                                    f"・グローバル制約：{g_from}→{g_to}を{g_sub}\n"
                                    f"・個人の制約：{s_from}→{s_to}を{s_sub}"
                                )
                                QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                                write_notification(msg)
                                return True

        # 4. 既存シフトとの矛盾チェック
        if shift_data.entries:
            # エントリを日付でソート
            sorted_entries = sorted(shift_data.entries, key=lambda x: x.day)
            
            # スタッフごとのエントリを作成
            entries_by_staff = {}
            for entry in sorted_entries:
                if entry.staff_name not in entries_by_staff:
                    entries_by_staff[entry.staff_name] = []
                entries_by_staff[entry.staff_name].append(entry)

            # 各スタッフのエントリをチェック
            for staff_name, entries in entries_by_staff.items():
                staff = next(s for s in staff_data_list if s.name == staff_name)
                
                # 連続する2日分のエントリをチェック
                for i in range(len(entries) - 1):
                    if entries[i+1].day != entries[i].day + 1:
                        continue  # 連続していない日付はスキップ
                    
                    # シフトタイプを標準化
                    from_shift = self.SHIFT_TYPE_FIXMAPPING.get(entries[i].shift_type, entries[i].shift_type)
                    to_shift = self.SHIFT_TYPE_FIXMAPPING.get(entries[i+1].shift_type, entries[i+1].shift_type)
                    
                    # グローバル制約チェック
                    if not staff.is_global_rule:
                        for g_from, g_to, g_sub in global_patterns:
                            # 制約のシフトタイプも標準化
                            g_from_std = self.SHIFT_TYPE_FIXMAPPING.get(str(g_from), str(g_from))
                            g_to_std = self.SHIFT_TYPE_FIXMAPPING.get(str(g_to), str(g_to))
                            
                            if g_from_std == from_shift and g_to_std == to_shift and g_sub == "回避":
                                msg = (
                                    f"スタッフ「{staff_name}」の{entries[i].day}日→{entries[i+1].day}日のシフトが\n"
                                    f"グローバル制約（{g_from}→{g_to}を回避）に違反しています"
                                )
                                QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                                write_notification(msg)
                                return True
                    
                    # 個人制約チェック
                    staff_patterns = [
                        (c.count, c.target, c.sub_category)
                        for c in staff.constraints
                        if c.category == "シフトパターン" and c.type == "必須"
                    ]
                    
                    for s_from, s_to, s_sub in staff_patterns:
                        # 制約のシフトタイプも標準化
                        s_from_std = self.SHIFT_TYPE_FIXMAPPING.get(str(s_from), str(s_from))
                        s_to_std = self.SHIFT_TYPE_FIXMAPPING.get(s_to, s_to)
                        
                        if s_from_std == from_shift and s_to_std == to_shift and s_sub == "嫌悪":
                            msg = (
                                f"スタッフ「{staff_name}」の{entries[i].day}日→{entries[i+1].day}日のシフトが\n"
                                f"個人制約（{s_from}→{s_to}を嫌悪）に違反しています"
                            )
                            QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                            write_notification(msg)
                            return True

        return False

    def _check_pair_overlap_constraints(self, staff_data_list: List[StaffData], shift_data: ShiftData) -> bool:
        """ペア重複制約のチェック
        
        Returns:
            bool: エラーがある場合True
        """
        logger.debug("=== ペア重複制約のチェック ===")

        # グローバルルール対象外のスタッフのみを抽出
        target_staff = [
            staff for staff in staff_data_list 
            if not staff.is_global_rule
        ]
        
        if len(target_staff) < 2:
            return False

        # ペア重複制約を取得
        pair_constraints = [
            constraint for constraint in self.rule_data.preference_constraints
            if constraint.category == "ペア重複" and constraint.type == "必須"
        ]
        
        if not pair_constraints:
            return False

        # 既存シフトをスタッフごとに整理
        staff_shifts = {}
        for entry in shift_data.entries:
            if entry.staff_name not in staff_shifts:
                staff_shifts[entry.staff_name] = []
            staff_shifts[entry.staff_name].append(entry)

        # 各制約をチェック
        for constraint in pair_constraints:
            shift_type = self.SHIFT_TYPE_FIXMAPPING.get(str(constraint.count), str(constraint.count))
            target_count = self.KANJI_TO_NUMBER.get(str(constraint.final), 0)

            # 各スタッフペアの組み合わせをチェック
            for i in range(len(target_staff)):
                for j in range(i + 1, len(target_staff)):
                    staff1 = target_staff[i]
                    staff2 = target_staff[j]
                    
                    # ペアの発生回数をカウント
                    pair_count = 0
                    pair_days = []
                    for day in range(1, self.month_days + 1):
                        staff1_has_shift = any(
                            e.day == day and shift_type == self.SHIFT_TYPE_FIXMAPPING.get(e.shift_type, e.shift_type)
                            for e in staff_shifts.get(staff1.name, [])
                        )
                        staff2_has_shift = any(
                            e.day == day and shift_type == self.SHIFT_TYPE_FIXMAPPING.get(e.shift_type, e.shift_type)
                            for e in staff_shifts.get(staff2.name, [])
                        )
                        if staff1_has_shift and staff2_has_shift:
                            pair_count += 1
                            pair_days.append(day)

                    if constraint.target == "以上":
                        # 指定回数以上のペアが存在する場合はエラー
                        if pair_count >= target_count:
                            msg = (
                                f"スタッフ「{staff1.name}」と「{staff2.name}」のペア重複制約が実現不可能です：\n"
                                f"・{shift_type}シフトのペアが{pair_count}回発生しており\n"
                                f"・制約（{target_count}回以上を禁止）に違反します"
                            )
                            QMessageBox.critical(None, "ペア重複制約エラー", msg)
                            write_notification(msg)
                            return True

                    else:  # "丁度"の場合
                        if pair_count == target_count:
                            # シフト上限チェック
                            staff1_max = staff1.shift_counts.get(shift_type, {}).get('max', 0)
                            staff2_max = staff2.shift_counts.get(shift_type, {}).get('max', 0)
                            staff1_current = sum(
                                1 for e in staff_shifts.get(staff1.name, [])
                                if shift_type == self.SHIFT_TYPE_FIXMAPPING.get(e.shift_type, e.shift_type)
                            )
                            staff2_current = sum(
                                1 for e in staff_shifts.get(staff2.name, [])
                                if shift_type == self.SHIFT_TYPE_FIXMAPPING.get(e.shift_type, e.shift_type)
                            )

                            # シフト確定チェック
                            staff1_complete = len(staff_shifts.get(staff1.name, [])) == self.month_days
                            staff2_complete = len(staff_shifts.get(staff2.name, [])) == self.month_days

                            if (staff1_current >= staff1_max or staff2_current >= staff2_max or 
                                staff1_complete or staff2_complete):
                                msg = (
                                    f"スタッフ「{staff1.name}」と「{staff2.name}」のペア重複制約が実現不可能です：\n"
                                    f"・{shift_type}シフトのペアが{pair_count}回発生しており\n"
                                    f"・制約（{target_count}回丁度を禁止）に違反します"
                                )
                                QMessageBox.critical(None, "ペア重複制約エラー", msg)
                                write_notification(msg)
                                return True

        return False

    def _check_separate_constraints(
        self,
        staff_data_list: List[StaffData],
        shift_data: ShiftData,
    ) -> bool:
        """セパレート制約のチェック"""
        logger.debug("=== セパレート制約のチェック ===")

        for staff in staff_data_list:
            for constraint in staff.constraints:
                if constraint.category != "セパレート" or constraint.type != "必須":
                    continue

                # 対象スタッフの存在確認
                target_staff = next(
                    (s for s in staff_data_list if s.name == constraint.sub_category),
                    None
                )
                if target_staff is None:
                    continue

                # シフトタイプの正規化
                source_type = self.SHIFT_TYPE_FIXMAPPING.get(str(constraint.count), str(constraint.count))
                target_type = self.SHIFT_TYPE_FIXMAPPING.get(str(constraint.target), str(constraint.target))

                # 1. 既存シフトとの矛盾チェック
                overlap_days = []
                for day in range(1, self.month_days + 1):
                    source_entry = next(
                        (e for e in shift_data.entries 
                         if e.staff_name == staff.name and e.day == day 
                         and self.SHIFT_TYPE_FIXMAPPING.get(e.shift_type, e.shift_type) == source_type),
                        None
                    )
                    target_entry = next(
                        (e for e in shift_data.entries 
                         if e.staff_name == target_staff.name and e.day == day 
                         and self.SHIFT_TYPE_FIXMAPPING.get(e.shift_type, e.shift_type) == target_type),
                        None
                    )
                    if source_entry and target_entry:
                        overlap_days.append(day)

                if overlap_days:
                    if constraint.times == "全て":
                        msg = (
                            f"スタッフ「{staff.name}」の{source_type}と"
                            f"スタッフ「{target_staff.name}」の{target_type}のセパレート制約（全て）に違反しています。\n"
                            f"重複日: {', '.join(map(str, overlap_days))}日"
                        )
                        logger.error(msg)
                        write_notification(msg)
                        return True
                    else:
                        max_overlaps = self.KANJI_TO_NUMBER.get(
                            str(constraint.times).replace("まで", ""),
                            0
                        )
                        if len(overlap_days) > max_overlaps:
                            msg = (
                                f"スタッフ「{staff.name}」の{source_type}と"
                                f"スタッフ「{target_staff.name}」の{target_type}のセパレート制約（{max_overlaps}回まで）に違反しています。\n"
                                f"現在{len(overlap_days)}回重複（{', '.join(map(str, overlap_days))}日）"
                            )
                            logger.error(msg)
                            write_notification(msg)
                            return True

                # 2. 実現可能性チェック
                # 出勤可能数を計算
                source_available_days = self.month_days - (staff.holiday_override or self.rule_data.holiday_count)
                target_available_days = self.month_days - (target_staff.holiday_override or self.rule_data.holiday_count)

                # 主体の対象シフトmax値と他シフトmaxの合計を計算
                source_max = staff.shift_counts.get(source_type, {}).get("max", 0)
                source_other_max = sum(
                    count.get("max", 0) for shift_type, count in staff.shift_counts.items()
                    if shift_type != source_type
                )

                # 客体の対象シフト以外のmaxの合計を計算（夜勤は2日分）
                target_other_max = sum(
                    (count.get("max", 0) * (2 if shift_type == "夜勤" else 1))
                    for shift_type, count in target_staff.shift_counts.items()
                    if shift_type != target_type
                )

                # 休日数を取得
                target_holidays = target_staff.holiday_override or self.rule_data.holiday_count

                # 重複許容回数を取得
                allowed_overlaps = 0 if constraint.times == "全て" else self.KANJI_TO_NUMBER.get(
                    str(constraint.times).replace("まで", ""),
                    0
                )

                # 月初の夜勤明け可能性チェック
                first_day_available = False
                
                # 1. 客体の1日目に夜勤が入っているかチェック
                target_first_day = next(
                    (e for e in shift_data.entries 
                     if e.staff_name == target_staff.name 
                     and e.day == 1 
                     and e.shift_type == "×"),
                    None
                )
                if target_first_day:
                    first_day_available = True
                
                # 2. 1日目が空欄で、他スタッフの夜勤が上限に達していないかチェック
                else:
                    first_day_entries = [
                        e for e in shift_data.entries 
                        if e.day == 1 and e.shift_type == "×"
                    ]
                    night_shift_limit = self.rule_data.night_staff
                    if len(first_day_entries) < night_shift_limit:
                        first_day_available = True

                # 出勤可能数を上限とした値を計算
                source_shift_max = min(source_max - source_other_max, source_available_days)
                target_shift_max = min(target_other_max, target_available_days) + target_holidays

                # 実現可能性チェック
                if source_shift_max > (
                    target_shift_max + 
                    allowed_overlaps + 
                    (1 if first_day_available else 0)
                ):
                    msg = (
                        f"スタッフ「{staff.name}」の{source_type}とスタッフ「{target_staff.name}」の{target_type}の"
                        f"セパレート制約（{constraint.times}）は実現不可能です。\n"
                        f"・主体の{source_type}可能回数：{source_shift_max}回\n"
                        f"・客体の他シフトと休日の合計：{target_shift_max}回\n"
                        f"・重複許容回数と月初の明け：{allowed_overlaps + (1 if first_day_available else 0)}回"
                    )
                    logger.error(msg)
                    write_notification(msg)
                    return True

        return False

    def _check_shift_pattern_feasibility(self, staff_data_list: List[StaffData], rule_data: RuleData, shift_data: ShiftData) -> bool:
        """シフトパターン制約の実現可能性をチェック
        個人の愛好パターンについて、シフト回数の整合性をチェックする
        
        Returns:
            bool: エラーがある場合True
        """
        # 日曜日の数を計算
        sunday_count = sum(
            1 for day in range(1, self.month_days + 1)
            if datetime(self.year, self.month, day).weekday() == 6
        )
        weekday_count = self.month_days - sunday_count

        # シフトタイプごとの必要総人数を計算（小数点以下切り捨て）
        required_staff = {
            "早番": int(self.month_days * rule_data.early_staff),
            "遅番": int(self.month_days * rule_data.late_staff),
            "夜勤": int(self.month_days * rule_data.night_staff),
            "日勤": int(sunday_count * rule_data.sunday_staff + weekday_count * rule_data.weekday_staff)
        }

        # スタッフ個別の制約実現可能性チェック
        for staff in staff_data_list:
            staff_patterns = [
                (c.count, c.target, c.sub_category)
                for c in staff.constraints
                if c.category == "シフトパターン" and c.type == "必須"
            ]
            
            for from_shift, to_shift, sub_cat in staff_patterns:
                if sub_cat == "愛好":
                    # シフト回数の制限を取得
                    from_min = staff.shift_counts.get(str(from_shift), {}).get('min', 0)
                    to_max = staff.shift_counts.get(str(to_shift), {}).get('max', 0)

                    # 月末のfrom_shift可能性チェック
                    last_day_from_available = False
                    last_day_from_entries = [
                        e for e in shift_data.entries 
                        if e.day == self.month_days and e.shift_type == from_shift
                    ]
                    if any(e.staff_name == staff.name for e in last_day_from_entries):
                        last_day_from_available = True
                    else:
                        # 本人の月末の他シフト希望をチェック
                        has_other_shift = any(
                            e.staff_name == staff.name and e.day == self.month_days
                            for e in shift_data.entries
                        )
                        if not has_other_shift:
                            # シフトタイプに応じた必要人数の取得
                            staff_count_map = {
                                "早番": rule_data.early_staff,
                                "遅番": rule_data.late_staff,
                                "夜勤": rule_data.night_staff,
                                "日勤": rule_data.weekday_staff
                            }
                            required_staff_count = staff_count_map[str(from_shift)]
                            # 既に必要人数に達していないかチェック
                            if len(last_day_from_entries) < required_staff_count:
                                last_day_from_available = True

                    # from_shiftが月末に入れる場合は-1を考慮
                    effective_from_min = from_min - (1 if last_day_from_available else 0)
                    
                    # 実現可能性チェック
                    if effective_from_min > to_max:
                        msg = (
                            f"スタッフ「{staff.name}」の制約「{from_shift}→{to_shift}を{sub_cat}」が\n"
                            f"シフト回数制限と矛盾します：\n"
                            f"・{from_shift}の最小回数（{from_min}回）が\n"
                            f"・{to_shift}の最大回数（{to_max}回）を超過しています"
                        )
                        QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                        write_notification(msg)
                        return True

        return False

    def _check_shift_pattern_conflicts(self, staff_data_list: List[StaffData], rule_data: RuleData) -> bool:
        """シフトパターン制約間の矛盾をチェック
        
        Args:
            staff_data_list: スタッフデータのリスト
            rule_data: ルールデータ
            
        Returns:
            bool: エラーがある場合True
        """
        logger.debug("=== シフトパターン制約間の矛盾チェック ===")

        try:
            # 1. グローバル制約の矛盾チェック
            global_patterns = [
                (c.count, c.target, c.sub_category) 
                for c in rule_data.preference_constraints 
                if c.category == "シフトパターン" and c.type == "必須"
            ]
            
            for i, (from1, to1, sub1) in enumerate(global_patterns):
                for from2, to2, sub2 in global_patterns[i+1:]:
                    # 同一パターンでの矛盾（推奨/愛好 vs 回避/嫌悪）
                    if from1 == from2 and to1 == to2:
                        if ((sub1 in ["推奨", "愛好"] and sub2 in ["回避", "嫌悪"]) or
                            (sub1 in ["回避", "嫌悪"] and sub2 in ["推奨", "愛好"])):
                            msg = (
                                f"グローバル制約のシフトパターンが矛盾しています：\n"
                                f"・{from1}→{to1}を{sub1}\n"
                                f"・{from2}→{to2}を{sub2}"
                            )
                            QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                            write_notification(msg)
                            return True
                    
                    # 同じ起点から異なる終点への矛盾
                    if from1 == from2 and to1 != to2 and sub1 == sub2 and sub1 in ["推奨", "愛好"]:
                        msg = (
                            f"グローバル制約のシフトパターンが矛盾しています：\n"
                            f"・{from1}→{to1}を{sub1}\n"
                            f"・{from2}→{to2}を{sub2}\n"
                            f"（同じシフトから異なるシフトへの{sub1}は設定できません）"
                        )
                        QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                        write_notification(msg)
                        return True

            # 2. スタッフ個別の制約矛盾チェック
            for staff in staff_data_list:
                staff_patterns = [
                    (c.count, c.target, c.sub_category)
                    for c in staff.constraints
                    if c.category == "シフトパターン" and c.type == "必須"
                ]
                
                # スタッフ内の制約矛盾チェック
                for i, (from1, to1, sub1) in enumerate(staff_patterns):
                    for from2, to2, sub2 in staff_patterns[i+1:]:
                        # 同一パターンでの矛盾
                        if from1 == from2 and to1 == to2:
                            if ((sub1 in ["推奨", "愛好"] and sub2 in ["回避", "嫌悪"]) or
                                (sub1 in ["回避", "嫌悪"] and sub2 in ["推奨", "愛好"])):
                                msg = (
                                    f"スタッフ「{staff.name}」のシフトパターン制約が矛盾しています：\n"
                                    f"・{from1}→{to1}を{sub1}\n"
                                    f"・{from2}→{to2}を{sub2}"
                                )
                                QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                                write_notification(msg)
                                return True
                        
                        # 同じ起点から異なる終点への矛盾
                        if from1 == from2 and to1 != to2 and sub1 == sub2 and sub1 in ["推奨", "愛好"]:
                            msg = (
                                f"スタッフ「{staff.name}」のシフトパターン制約が矛盾しています：\n"
                                f"・{from1}→{to1}を{sub1}\n"
                                f"・{from2}→{to2}を{sub2}\n"
                                f"（同じシフトから異なるシフトへの{sub1}は設定できません）"
                            )
                            QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                            write_notification(msg)
                            return True

                # 3. グローバルとスタッフの制約矛盾チェック
                if not staff.is_global_rule:
                    for g_from, g_to, g_sub in global_patterns:
                        for s_from, s_to, s_sub in staff_patterns:
                            # 同一パターンでの矛盾
                            if g_from == s_from and g_to == s_to:
                                if ((g_sub in ["回避"] and s_sub in ["愛好"]) or
                                    (g_sub in ["推奨"] and s_sub in ["嫌悪"])):
                                    msg = (
                                        f"スタッフ「{staff.name}」の制約がグローバル制約と矛盾しています：\n"
                                        f"・グローバル制約：{g_from}→{g_to}を{g_sub}\n"
                                        f"・個人の制約：{s_from}→{s_to}を{s_sub}"
                                    )
                                    QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                                    write_notification(msg)
                                    return True
                            
                            # 同じ起点から異なる終点への矛盾
                            if g_from == s_from and g_to != s_to:
                                if g_sub in ["推奨", "愛好"] and s_sub in ["推奨", "愛好"]:  # 推奨/愛好の場合のみチェック
                                    msg = (
                                        f"スタッフ「{staff.name}」の制約がグローバル制約と矛盾しています：\n"
                                        f"・グローバル制約：{g_from}→{g_to}を{g_sub}\n"
                                        f"・個人の制約：{s_from}→{s_to}を{s_sub}\n"
                                        f"（同じシフトから異なるシフトへの同種の制約は設定できません）"
                                    )
                                    QMessageBox.critical(None, "シフトパターン制約エラー", msg)
                                    write_notification(msg)
                                    return True

            return False

        except Exception as e:
            logger.error(f"シフトパターン制約の矛盾チェックでエラー: {e}")
            write_notification("シフトパターン制約の矛盾チェックでエラーが発生しました。")
            return True

    def _check_global_shift_pattern_mandatory(self, rule_data: RuleData) -> bool:
        """グローバルのシフトパターン制約で必須推奨が選択されていないかチェック
        
        Returns:
            bool: エラーがある場合True
        """
        logger.debug("=== グローバルシフトパターン制約の必須推奨チェック ===")
        
        # グローバルのシフトパターン制約を抽出（必須推奨のみ）
        global_pattern_constraints = [
            constraint for constraint in rule_data.preference_constraints
            if (constraint.category == "シフトパターン" and 
                constraint.type == "必須" and 
                constraint.sub_category == "推奨")
        ]
        
        if global_pattern_constraints:
            invalid_patterns = []
            for constraint in global_pattern_constraints:
                pattern = f"{constraint.count}→{constraint.target}"
                invalid_patterns.append(pattern)
            
            msg = (
                f"グローバル制約でシフトパターンに必須推奨が設定されています：\n"
                f"・パターン：{', '.join(invalid_patterns)}\n"
                f"グローバル制約では必須推奨以外（選好推奨、必須回避、選好回避）を選択してください。"
            )
            logger.error(msg)
            write_notification(msg)
            return True
        
        return False


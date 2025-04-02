"""
基本的な制約を実装するライブラリ
"""

import logging
from typing import Dict, List
from ortools.sat.python import cp_model
from .logger import logger
from ..from_dict import StaffData, ShiftData, RuleData
from datetime import datetime
import math
from .mapping import (
    SHIFT_TYPES,
    SHIFT_TYPE_MAPPING,
    KANJI_TO_NUMBER,
    STATUS_MAP,
    Constraint
)

class BasicLibrary:
    """基本的な制約（必要人数、1日1シフトなど）を扱うライブラリ"""
    def __init__(
        self,
        model: cp_model.CpModel,
        shifts: Dict,
        staff_data_list: List[StaffData],
        rule_data: RuleData,
        shift_data: ShiftData,
        days_in_month: int,
        year: int,
        month: int,
        staff_list: List[str],
        reliability_map: Dict[str, int],
        constraint_weights: Dict,
    ):
        self.model = model
        self.shifts = shifts
        self.staff_data_list = staff_data_list
        self.rule_data = rule_data
        self.shift_data = shift_data
        self.days_in_month = days_in_month
        self.year = year
        self.month = month
        self.staff_list = staff_list
        self.reliability_map = reliability_map
        self.constraint_weights = constraint_weights
        self.SHIFT_TYPES = SHIFT_TYPES
        self.SHIFT_TYPE_MAPPING = SHIFT_TYPE_MAPPING
        self.KANJI_TO_NUMBER = KANJI_TO_NUMBER
        self.STATUS_MAP = STATUS_MAP
        self.objective_terms = []

    def add_one_shift_per_day(self):
        """制約1: 一日一シフト"""
        logger.debug("=== 制約1: 一日一シフトの設定 ===")
        for staff in self.staff_list:
            for day in range(self.days_in_month):
                c = self.model.Add(
                    sum(self.shifts[(staff, day, st)] for st in self.SHIFT_TYPES.keys()) == 1
                )
                c.WithName(f"【一日一シフト】{staff}：{day+1}日")

    def add_required_staff(self):
        """制約2: 必要人数 (早番・遅番・夜勤・日勤)"""
        logger.debug("=== 制約2: 必要人数の設定 ===")
        for day in range(self.days_in_month):
            # 早番
            c_early = self.model.Add(
                sum(self.shifts[(st, day, '▲')] for st in self.staff_list)
                == self.rule_data.early_staff
            )
            c_early.WithName(f"【早番の必要人数】{day+1}日")

            # 遅番
            c_late = self.model.Add(
                sum(self.shifts[(st, day, '▼')] for st in self.staff_list)
                == self.rule_data.late_staff
            )
            c_late.WithName(f"【遅番の必要人数】{day+1}日")

            # 夜勤(／)と夜勤明け(×)
            c_night_in = self.model.Add(
                sum(self.shifts[(st, day, '／')] for st in self.staff_list)
                == self.rule_data.night_staff
            )
            c_night_in.WithName(f"【夜勤(／)の必要人数】{day+1}日")

            c_night_out = self.model.Add(
                sum(self.shifts[(st, day, '×')] for st in self.staff_list)
                == self.rule_data.night_staff
            )
            c_night_out.WithName(f"【夜勤明け(×)の必要人数】{day+1}日")

            # 日勤 (平日 or 日曜)
            weekday = datetime(self.year, self.month, day+1).weekday()
            staff_count_day = sum(self.shifts[(st, day, '日')] for st in self.staff_list)
            if weekday == 6:
                val = self.rule_data.sunday_staff
                if abs(val % 1 - 0.5) < 0.01:
                    mi = math.floor(val)
                    ma = math.ceil(val)
                    c1 = self.model.Add(staff_count_day >= mi)
                    c2 = self.model.Add(staff_count_day <= ma)
                    c1.WithName(f"【日曜日の日勤_min】{day+1}日")
                    c2.WithName(f"【日曜日の日勤_max】{day+1}日")
                else:
                    c0 = self.model.Add(staff_count_day == int(val))
                    c0.WithName(f"【日曜日の日勤_int】{day+1}日")
            else:
                val = self.rule_data.weekday_staff
                if abs(val % 1 - 0.5) < 0.01:
                    mi = math.floor(val)
                    ma = math.ceil(val)
                    c1 = self.model.Add(staff_count_day >= mi)
                    c2 = self.model.Add(staff_count_day <= ma)
                    c1.WithName(f"【平日日勤_min】{day+1}日")
                    c2.WithName(f"【平日日勤_max】{day+1}日")
                else:
                    c0 = self.model.Add(staff_count_day == int(val))
                    c0.WithName(f"【平日日勤_int】{day+1}日")

    def add_monthly_holiday_limit(self):
        """制約3: 月の公休日数 (overrideも含む)"""
        logger.debug("=== 制約3: 月の公休日数の設定 ===")
        for stf in self.staff_data_list:
            if stf.holiday_override is None:
                c = self.model.Add(
                    sum(self.shifts[(stf.name, d, '公')] for d in range(self.days_in_month))
                    == self.rule_data.holiday_count
                )
                c.WithName(f"【公休日数】{stf.name}：{self.rule_data.holiday_count}日")
            else:
                c2 = self.model.Add(
                    sum(self.shifts[(stf.name, d, '公')] for d in range(self.days_in_month))
                    == stf.holiday_override
                )
                c2.WithName(f"【公休日数】{stf.name}：override {stf.holiday_override}日")

    def add_hope_shift(self):
        """制約6: 希望シフトの必須制約 (必要であれば実装)"""
        logger.debug("=== 制約6: 希望シフトの必須制約の設定 ===")

        if self.shift_data and self.shift_data.entries:
            for entry in self.shift_data.entries:
                c_shift_required=self.model.Add(self.shifts[(entry.staff_name, entry.day-1, entry.shift_type)] == 1)
                c_shift_required.WithName(f"【希望シフト】{entry.staff_name}, {entry.day}日, {entry.shift_type}")

    def add_work_count_limit(self):
        """スタッフの勤務回数制限 (必須制約)"""
        logger.debug("=== 勤務回数制限の設定 ===")
        for stf in self.staff_data_list:
            for shift_type, limits in stf.shift_counts.items():
                normalized_type = self.SHIFT_TYPE_MAPPING.get(shift_type, shift_type)
                if normalized_type in self.SHIFT_TYPES:
                    total_shifts = sum(
                        self.shifts[(stf.name, d, normalized_type)]
                        for d in range(self.days_in_month)
                    )
                    c_min = self.model.Add(total_shifts >= limits['min'])
                    c_min.WithName(
                        f"【勤務回数制限_min】{stf.name}:{shift_type} {limits['min']}回以上"
                    )
                    c_max = self.model.Add(total_shifts <= limits['max'])
                    c_max.WithName(
                        f"【勤務回数制限_max】{stf.name}:{shift_type} {limits['max']}回以下"
                    )

    def add_reliability_constraint(self):
        """シフト適性の必須制約と選好制約を設定する"""
        logger.debug("=== シフト適性の制約設定開始 ===")

        if (self.rule_data.weekday_reliability is None
            and self.rule_data.sunday_reliability is None):
            return

        for day in range(self.days_in_month):
            weekday = datetime(self.year, self.month, day+1).weekday()
            is_sunday = weekday == 6
            
            # 1. 必須制約の処理
            target_reliability = (
                self.rule_data.sunday_reliability if is_sunday
                else self.rule_data.weekday_reliability
            )
            
            # その日の適性値合計を計算
            daily_sum = self.model.NewIntVar(0, 1000, f'daily_reliability_{day}')
            staff_contributions = []
            for staff in self.staff_list:
                # 1日1シフトなので、どれか1つのシフトのみが1になる
                staff_rel = (
                    self.shifts[(staff, day, '▲')] +
                    self.shifts[(staff, day, '日')] +
                    self.shifts[(staff, day, '▼')]
                ) * self.reliability_map[staff]
                staff_contributions.append(staff_rel)

            self.model.Add(daily_sum == sum(staff_contributions))
            
            # 必須制約の追加
            if target_reliability is not None:
                c_rel = self.model.Add(daily_sum >= target_reliability)
                c_rel.WithName(f"【シフト適性_必須】{day+1}日目(目標:{target_reliability})")

            # 2. 選好制約の処理
            for constraint in self.rule_data.preference_constraints:
                if constraint.category == "シフト適性":
                    # sub_categoryのチェック
                    if ((constraint.sub_category == "日曜" and not is_sunday) or
                        (constraint.sub_category == "通常" and is_sunday) or
                        (constraint.sub_category not in ["日曜", "通常"])):
                        continue

                    if constraint.target is None:
                        logger.warning(f"シフト適性の目標値が未設定です: {constraint}")
                        continue

                    target_value = int(constraint.target)
                    
                    if constraint.type == "必須":
                        # 必須制約として処理
                        c = self.model.Add(daily_sum >= target_value)
                        c.WithName(f"【シフト適性_必須(preference)】{day+1}日目(目標:{target_value})")
                    
                    elif constraint.type == "選好":
                        # 選好制約として処理
                        weight = constraint.weight
                        penalty = self.model.NewBoolVar(
                            f'penalty_target_day_{day}_{constraint.sub_category}'
                        )
                        
                        # 目標値未満の場合にペナルティ
                        self.model.Add(daily_sum < target_value).OnlyEnforceIf(penalty)
                        self.model.Add(daily_sum >= target_value).OnlyEnforceIf(penalty.Not())
                        
                        # 目的関数に負の重みを追加
                        self.objective_terms.append(penalty * (-weight))

        logger.debug("=== シフト適性の制約設定完了 ===")

    def add_star_shift_constraint(self):
        """制約: ☆シフトは希望シフトとして指定された場合のみ使用可能"""
        logger.debug("=== ☆シフト制約の設定 ===")

        for staff in self.staff_list:
            for day in range(self.days_in_month):
                # ☆シフトが指定されていない場合は禁止
                has_star = False
                if self.shift_data and self.shift_data.entries:
                    for entry in self.shift_data.entries:
                        if (entry.staff_name == staff and 
                            entry.day-1 == day and 
                            entry.shift_type == '☆'):
                            has_star = True
                            # ☆シフトを必須に設定
                            c1 = self.model.Add(self.shifts[(staff, day, '☆')] == 1)
                            c1.WithName(f"【☆シフト必須】{staff}:{day+1}日")
                            # 他のシフトを禁止
                            for other_type in self.SHIFT_TYPES.keys():
                                if other_type != '☆':
                                    c2 = self.model.Add(self.shifts[(staff, day, other_type)] == 0)
                                    c2.WithName(f"【☆シフト時の他シフト禁止】{staff}:{day+1}日:{other_type}")
                            break
                
                if not has_star:
                    # ☆シフトを禁止
                    c3 = self.model.Add(self.shifts[(staff, day, '☆')] == 0)
                    c3.WithName(f"【☆シフト禁止】{staff}:{day+1}日")

        logger.debug("☆シフト制約の設定完了")

    def add_under_shift_constraint(self):
        """制約: アンダースコアシフトの使用を禁止する"""
        logger.debug("=== アンダースコアシフト制約の設定 ===")

        for staff in self.staff_list:
            for day in range(self.days_in_month):
                # アンダースコアシフトを禁止
                self.model.Add(
                    self.shifts[(staff, day, '_')] == 0
                ).WithName(f"【_禁止】{staff}:{day+1}日")

        logger.debug("アンダースコアシフト禁止制約の設定完了")

    def add_preference_objective(self):
        """選好制約(オブジェクト)の処理を追加し、self.objective_termsに項を追加"""
        logger.debug("=== 選好制約 (目的関数) の処理 ===")
        for stf in self.staff_data_list:
            for constraint in stf.constraints:
                if constraint.category == "勤務希望":
                    normalized_type = self.SHIFT_TYPE_MAPPING.get(constraint.target, constraint.target)
                    if normalized_type in self.SHIFT_TYPES:
                        # 必須制約の処理を追加
                        if constraint.type == "必須":
                            target_count = sum(
                                self.shifts[(stf.name, d, normalized_type)]
                                for d in range(self.days_in_month)
                            )
                            if constraint.sub_category == "愛好":
                                max_shifts = stf.shift_counts[constraint.target]['max']
                                c_suki1 = self.model.Add(target_count == max_shifts)
                                c_suki1.WithName(
                                    f"【選好】{stf.name}：{normalized_type} {constraint.sub_category} {max_shifts}回"
                                )
                            elif constraint.sub_category == "嫌悪":
                                min_shifts = stf.shift_counts[constraint.target]['min']
                                c_suki2 = self.model.Add(target_count == min_shifts)
                                c_suki2.WithName(
                                    f"【選好】{stf.name}：{normalized_type} {constraint.sub_category} {min_shifts}回"
                                )
                        
                        # 既存の選好制約の処理
                        elif constraint.type == "選好":
                            shift_count_var = self.model.NewIntVar(
                                0, self.days_in_month, f'shift_count_{stf.name}_{normalized_type}'
                            )
                            self.model.Add(
                                shift_count_var == sum(
                                    self.shifts[(stf.name, d, normalized_type)]
                                    for d in range(self.days_in_month)
                                )
                            )
                            weight = self.constraint_weights["選好"]["勤務希望"]
                            multiplier = 1 if constraint.sub_category == "愛好" else -1
                            self.objective_terms.append(shift_count_var * weight * multiplier)

    def add_underscore_penalty_to_objective(self):
        """アンダースコアシフトに対してペナルティを目的関数に追加"""
        logger.debug("=== アンダースコアシフトペナルティの設定 ===")

        if '_' in self.SHIFT_TYPES:
            for staff in self.staff_list:
                for day in range(self.days_in_month):
                    # 目的関数にペナルティ項を追加
                    penalty_term = self.shifts[(staff, day, '_')] * -10000
                    self.objective_terms.append(penalty_term)
    def calculate_reliability(self, day):
        """日ごとの適性値合計を計算する共通処理"""
        weekday = datetime(self.year, self.month, day+1).weekday()
        is_sunday = weekday == 6
        
        # その日の適性値合計を計算
        daily_sum = self.model.NewIntVar(0, 1000, f'daily_reliability_{day}')
        staff_contributions = []
        for staff in self.staff_list:
            staff_rel = (
                self.shifts[(staff, day, '▲')] +
                self.shifts[(staff, day, '日')] +
                self.shifts[(staff, day, '▼')]
            ) * self.reliability_map[staff]
            staff_contributions.append(staff_rel)

        self.model.Add(daily_sum == sum(staff_contributions))
        return daily_sum, is_sunday

    def add_global_standard_reliability(self):
        """weekday_reliabilityとsunday_reliabilityの処理"""
        if (self.rule_data.weekday_reliability is None
            and self.rule_data.sunday_reliability is None):
            logger.debug("必須の目標値が未設定のためスキップ")
            return

        for day in range(self.days_in_month):
            daily_sum, is_sunday = self.calculate_reliability(day)
            
            target_reliability = (
                self.rule_data.sunday_reliability if is_sunday
                else self.rule_data.weekday_reliability
            )
            
            if target_reliability is not None:
                c_rel = self.model.Add(daily_sum >= target_reliability)
                c_rel.WithName(f"【シフト適性_必須】{day+1}日目(目標:{target_reliability})")
                logger.debug(f"必須制約を追加: {day+1}日目, 目標値{target_reliability}")

    def add_global_custom_reliability(self):
        """シフト適性の選好制約と必須制約（preference_constraints）を設定"""
        logger.debug("=== シフト適性のカスタム制約設定開始 ===")

        for day in range(self.days_in_month):
            daily_sum, is_sunday = self.calculate_reliability(day)
            
            for constraint in self.rule_data.preference_constraints:
                if constraint.category == "シフト適性":
                    # sub_categoryのチェック
                    if ((constraint.sub_category == "日曜" and not is_sunday) or
                        (constraint.sub_category == "通常" and is_sunday) or
                        (constraint.sub_category not in ["日曜", "通常"])):
                        continue

                    if constraint.target is None:
                        logger.warning(f"シフト適性の目標値が未設定です: {constraint}")
                        continue

                    target_value = int(constraint.target)
                    
                    if constraint.type == "必須":
                        # 必須制約として処理
                        c = self.model.Add(daily_sum >= target_value)
                        c.WithName(f"【シフト適性_必須(preference)】{day+1}日目(目標:{target_value})")
                        logger.debug(f"必須制約(preference)を追加: {day+1}日目, 目標値{target_value}")
                    
                    elif constraint.type == "選好":
                        # 選好制約として処理
                        weight = constraint.weight
                        penalty = self.model.NewBoolVar(
                            f'penalty_target_day_{day}_{constraint.sub_category}'
                        )
                        
                        # 目標値未満の場合にペナルティ
                        self.model.Add(daily_sum < target_value).OnlyEnforceIf(penalty)
                        self.model.Add(daily_sum >= target_value).OnlyEnforceIf(penalty.Not())
                        
                        # 目的関数に負の重みを追加
                        self.objective_terms.append(penalty * (-weight))
                        
                        logger.debug(
                            f"選好制約を追加: {day+1}日目({constraint.sub_category}), "
                            f"目標値{target_value}, 重み{-weight}"
                        )

        logger.debug("=== シフト適性のカスタム制約設定完了 ===")

    def add_numbered_shift_preference(self):
        """数字付きシフトの選好制約を設定"""
        logger.debug("=== 選好制約: 数字付きシフトの処理 ===")
        
        for entry in self.shift_data.preference_entries:
            shift_var = self.shifts[(entry.staff_name, entry.day - 1, entry.shift_type)]
            weight = getattr(entry, 'weight', 1)
            self.objective_terms.append(shift_var * weight)


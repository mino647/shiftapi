"""
連続性に関する制約を実装するライブラリ
"""

import logging
from typing import Dict, List, Any
from ortools.sat.python import cp_model
from .logger import logger
from ..main import StaffData, ShiftData, RuleData
from .mapping import (
    SHIFT_TYPES,
    SHIFT_TYPE_MAPPING,
    KANJI_TO_NUMBER,
    STATUS_MAP
)
import dataclasses

class SequenceLibrary:
    """連続性に関する制約（連続勤務制限、夜勤パターンなど）を扱うライブラリ"""
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

    def add_consecutive_work_limit(self):
        """制約4: 連続勤務制限"""
        logger.debug("=== 制約4: 連続勤務制限の設定 ===")
        max_limit = self.rule_data.consecutive_work_limit
        for stf in self.staff_data_list:
            for start_day in range(self.days_in_month - max_limit + 1):
                work_days_exprs = []
                for i in range(min(max_limit + 1, self.days_in_month - start_day)):
                    cur_day = start_day + i
                    day_count = sum(
                        self.shifts[(stf.name, cur_day, s)]
                        for s in self.SHIFT_TYPES
                        if s != '公'
                    )
                    if cur_day == self.days_in_month - 1:
                        day_count += self.shifts[(stf.name, cur_day, '／')]
                    work_days_exprs.append(day_count)
                c = self.model.Add(sum(work_days_exprs) <= max_limit)
                c.WithName(
                    f"【連続勤務制限】{stf.name}:{start_day+1}日目～{start_day+max_limit}日目"
                )

    def add_global_holiday_pattern_constraint(self):
        """RuleDataの連続休暇制約をグローバルルール適用対象のスタッフに適用
        
        グローバルルール適用対象：
        - is_global_ruleがFalseのスタッフ
        """
        logger.debug("=== グローバル制約: 連続休暇パターンの設定 ===")
        
        for constraint in self.rule_data.preference_constraints:
            if constraint.category == "連続休暇" and constraint.times == "全員":
                # RuleDataの制約をStaffData形式に変換
                converted_constraint = dataclasses.replace(
                    constraint,
                    sub_category="愛好" if constraint.sub_category == "推奨" else "嫌悪"
                )
                
                # グローバルルール適用対象のスタッフにのみ制約を適用
                for staff in self.staff_data_list:
                    if not staff.is_global_rule:  # グローバルルール除外でないスタッフのみに適用
                        self.add_holiday_pattern_constraint(staff, converted_constraint)

    def add_holiday_pattern_constraint(self, target_staff=None, target_constraint=None):
        """制約: 連続休暇パターン
        必須制約：
            - 愛好/推奨の場合、指定された連休パターンを強制
            - 嫌悪/回避の場合、指定された連休パターンを禁止
        選好制約：
            - 愛好/推奨の場合、指定された連休パターンを優遇（正の重み）
            - 嫌悪/回避の場合、指定された連休パターンを回避（負の重み）
        """
        logger.debug("=== 制約: 連続休暇パターンの設定 ===")

        MAX_HOLIDAY_CONSECUTIVE = 7  # 連続休暇の最大値を7に設定

        # 処理対象の決定
        if target_staff and target_constraint:
            staff_constraints = [(target_staff, target_constraint)]
        else:
            staff_constraints = [(staff, constraint) 
                               for staff in self.staff_data_list 
                               for constraint in staff.constraints 
                               if constraint.category == "連続休暇"]

        for staff, constraint in staff_constraints:
            count_value = constraint.count if constraint.count is not None else "単休"
            base_days = self.KANJI_TO_NUMBER.get(count_value, 1)

            # パターンの長さ範囲を決定
            if constraint.target == "以下":
                target_days_range = range(1, base_days + 1)
            elif constraint.target == "以上":
                target_days_range = range(base_days, MAX_HOLIDAY_CONSECUTIVE + 1)
            else:  # "丁度"
                target_days_range = [base_days]

            if constraint.type == "必須":
                if constraint.sub_category in ["愛好", "推奨"]:
                    if constraint.target == "丁度":
                        # 丁度の場合：パターンマッチングで実装
                        pattern_vars = []
                        for day in range(self.days_in_month - base_days + 1):
                            is_pattern = self.model.NewBoolVar(f'holiday_pattern_{staff.name}_{day}_{base_days}')
                            pattern_vars.append(is_pattern)
                            
                            pattern_days = []
                            for offset in range(base_days):
                                pattern_days.append(self.shifts[(staff.name, day + offset, '公')])
                            
                            if day > 0:
                                pattern_days.append(self.shifts[(staff.name, day - 1, '公')].Not())
                            if day + base_days < self.days_in_month:
                                pattern_days.append(self.shifts[(staff.name, day + base_days, '公')].Not())
                            
                            self.model.AddBoolAnd(pattern_days).OnlyEnforceIf(is_pattern)
                            self.model.AddBoolOr([v.Not() for v in pattern_days]).OnlyEnforceIf(is_pattern.Not())
                        
                        # 必要なパターン数を設定
                        holiday_count = self.rule_data.holiday_count
                        if staff.holiday_override is not None:
                            holiday_count = staff.holiday_override
                        
                        max_patterns = holiday_count // base_days
                        if max_patterns > 0:
                            self.model.Add(sum(pattern_vars) == max_patterns)
                            logger.debug(f"{staff.name}の{base_days}連休{constraint.target}パターン: {max_patterns}回を実現")

                    elif constraint.target == "以下":
                        # base_days + 1日以上の連続休暇を禁止
                        for day in range(self.days_in_month - (base_days + 1) + 1):
                            consecutive_days = [
                                self.shifts[(staff.name, day + i, '公')]
                                for i in range(base_days + 1)
                            ]
                            self.model.AddBoolOr([d.Not() for d in consecutive_days])
                            logger.debug(f"{staff.name}の{day + 1}日目: {base_days + 1}日以上の連続休暇を禁止")

                    elif constraint.target == "以上":
                        # base_days未満の連休を禁止（新規追加）
                        for length in range(1, base_days):
                            for day in range(self.days_in_month - length + 1):
                                pattern_days = [
                                    self.shifts[(staff.name, day + i, '公')]
                                    for i in range(length)
                                ]
                                # 前後が非公休の場合のみパターンを禁止
                                if day > 0:
                                    pattern_days.append(self.shifts[(staff.name, day - 1, '公')].Not())
                                if day + length < self.days_in_month:
                                    pattern_days.append(self.shifts[(staff.name, day + length, '公')].Not())
                                
                                # 確定したパターンを禁止
                                self.model.AddBoolOr([d.Not() for d in pattern_days])
                                logger.debug(f"{staff.name}の{day + 1}日目: {length}連休を禁止（{base_days}連休以上必須）")

                else:  # 嫌悪/回避
                    if constraint.target == "以上":
                        # base_daysの連続休暇以上を禁止
                        for day in range(self.days_in_month - base_days + 1):
                            consecutive_days = [
                                self.shifts[(staff.name, day + i, '公')]
                                for i in range(base_days)
                            ]
                            self.model.AddBoolOr([d.Not() for d in consecutive_days])
                            logger.debug(f"{staff.name}の{day + 1}日目: {base_days}連休以上を禁止")
                    
                    elif constraint.target == "以下":
                        # base_days以下の連休を禁止（1日から指定日数まで）
                        for target_days in range(1, base_days + 1):
                            for day in range(self.days_in_month - target_days + 1):
                                pattern_days = [
                                    self.shifts[(staff.name, day + i, '公')]
                                    for i in range(target_days)
                                ]
                                # 前後の日が勤務日の場合のみパターンを禁止
                                if day > 0:
                                    pattern_days.append(self.shifts[(staff.name, day - 1, '公')].Not())
                                if day + target_days < self.days_in_month:
                                    pattern_days.append(self.shifts[(staff.name, day + target_days, '公')].Not())
                                self.model.AddBoolOr([d.Not() for d in pattern_days])
                                logger.debug(f"{staff.name}の{day + 1}日目: 確定した{target_days}連休を禁止")

                    elif constraint.target == "丁度" and count_value == "単休":
                        # 単休を禁止
                        for day in range(1, self.days_in_month - 1):
                            pattern_days = [
                                self.shifts[(staff.name, day - 1, '公')].Not(),
                                self.shifts[(staff.name, day, '公')],
                                self.shifts[(staff.name, day + 1, '公')].Not()
                            ]
                            self.model.AddBoolOr([d.Not() for d in pattern_days])
                            logger.debug(f"{staff.name}の{day + 1}日目: 単休を禁止")

            else:  # 選好制約
                weight = (getattr(constraint, 'weight', None) or 
                         self.constraint_weights["選好"]["連続休暇"])
                multiplier = 1 if constraint.sub_category in ["愛好", "推奨"] else -1
                
                for target_days in target_days_range:
                    for day in range(self.days_in_month - target_days + 1):
                        is_pattern = self.model.NewBoolVar(f'holiday_pattern_{staff.name}_{day}_{target_days}')
                        
                        pattern_days = []
                        for offset in range(target_days):
                            pattern_days.append(self.shifts[(staff.name, day + offset, '公')])
                        
                        if day > 0:
                            pattern_days.append(self.shifts[(staff.name, day - 1, '公')].Not())
                        if day + target_days < self.days_in_month:
                            pattern_days.append(self.shifts[(staff.name, day + target_days, '公')].Not())
                        
                        self.model.AddBoolAnd(pattern_days).OnlyEnforceIf(is_pattern)
                        self.model.AddBoolOr([v.Not() for v in pattern_days]).OnlyEnforceIf(is_pattern.Not())
                        
                        self.objective_terms.append(is_pattern * weight * multiplier)
                        logger.debug(f"{staff.name}の{day + 1}日目: {target_days}連休 "
                                   f"{'愛好' if constraint.sub_category in ['愛好', '推奨'] else '嫌悪'} "
                                   f"(重み: {weight * multiplier})")

    def add_consecutive_work_pattern(self, staff: StaffData, constraint: Any, target_shifts: List[str]) -> None:
        """制約: 連続勤務パターン
        必須制約：
            - 愛好/推奨の場合、指定された連勤パターンを強制
            - 嫌悪/回避の場合、指定された連勤パターンを禁止
        選好制約：
            - 愛好/推奨の場合、指定された連勤パターンを優遇（正の重み）
            - 嫌悪/回避の場合、指定された連勤パターンを回避（負の重み）
        """
        logger.debug("=== 制約: 連続勤務パターンの設定 ===")

        MAX_WORK_CONSECUTIVE = self.rule_data.consecutive_work_limit
        count_value = constraint.count if constraint.count is not None else "単勤"
        base_days = self.KANJI_TO_NUMBER.get(count_value, 1)

        # パターンの長さ範囲を決定
        if constraint.target == "以下":
            target_days_range = range(1, base_days + 1)
        elif constraint.target == "以上":
            target_days_range = range(base_days, MAX_WORK_CONSECUTIVE + 1)
        else:  # "丁度"
            target_days_range = [base_days]

        # 必須制約の処理
        if constraint.type == "必須":
            if constraint.sub_category in ["愛好", "推奨"]:
                if constraint.target == "丁度":
                    # 丁度の場合：パターンマッチングで実装
                    pattern_vars = []
                    for day in range(self.days_in_month - base_days + 1):
                        is_pattern = self.model.NewBoolVar(f'work_pattern_{staff.name}_{day}_{base_days}')
                        pattern_vars.append(is_pattern)
                        
                        pattern_days = []
                        for offset in range(base_days):
                            work_day = self.model.NewBoolVar(f'work_day_{staff.name}_{day}_{offset}')
                            shift_vars = [self.shifts[(staff.name, day + offset, s)] for s in target_shifts]
                            
                            # 当該日を勤務にする => いずれかのシフトは True
                            self.model.AddBoolOr(shift_vars).OnlyEnforceIf(work_day)
                            self.model.AddBoolAnd([v.Not() for v in shift_vars]).OnlyEnforceIf(work_day.Not())

                            # ★Fix: 逆方向の拘束 (いずれかのシフトがTrueならwork_dayもTrue)
                            for sv in shift_vars:
                                self.model.Add(sv <= work_day)  # shift_var => work_day

                            pattern_days.append(work_day)
                        
                        # 前後を休みとみなす部分
                        if day > 0:
                            prev_rest = self.model.NewBoolVar(f'prev_rest_{staff.name}_{day}')
                            not_working = [self.shifts[(staff.name, day - 1, s)].Not() for s in target_shifts]
                            self.model.AddBoolAnd(not_working).OnlyEnforceIf(prev_rest)
                            pattern_days.append(prev_rest)
                        if day + base_days < self.days_in_month:
                            next_rest = self.model.NewBoolVar(f'next_rest_{staff.name}_{day}')
                            not_working = [self.shifts[(staff.name, day + base_days, s)].Not() for s in target_shifts]
                            self.model.AddBoolAnd(not_working).OnlyEnforceIf(next_rest)
                            pattern_days.append(next_rest)
                        
                        self.model.AddBoolAnd(pattern_days).OnlyEnforceIf(is_pattern)
                        self.model.AddBoolOr([v.Not() for v in pattern_days]).OnlyEnforceIf(is_pattern.Not())
                    
                    # 必要なパターン数を設定
                    work_days = self.days_in_month - (staff.holiday_override or self.rule_data.holiday_count)
                    max_patterns = work_days // base_days
                    if max_patterns > 0:
                        self.model.Add(sum(pattern_vars) == max_patterns)
                        logger.debug(f"{staff.name}の{base_days}連勤{constraint.target}パターン: {max_patterns}回を実現")

                elif constraint.target == "以下":
                    # base_days + 1日以上の連続勤務を禁止
                    for day in range(self.days_in_month - (base_days + 1) + 1):
                        consecutive_days = []
                        for i in range(base_days + 1):
                            work_day = self.model.NewBoolVar(f'work_day_{staff.name}_{day}_{i}')
                            shift_vars = [self.shifts[(staff.name, day + i, s)] for s in target_shifts]
                            
                            self.model.AddBoolOr(shift_vars).OnlyEnforceIf(work_day)
                            self.model.AddBoolAnd([v.Not() for v in shift_vars]).OnlyEnforceIf(work_day.Not())

                            # ★Fix: 逆方向の拘束
                            for sv in shift_vars:
                                self.model.Add(sv <= work_day)

                            consecutive_days.append(work_day)
                        self.model.AddBoolOr([d.Not() for d in consecutive_days])
                        logger.debug(f"{staff.name}の{day + 1}日目: {base_days + 1}日以上の連続勤務を禁止")

                elif constraint.target == "以上":
                    # base_days未満の連勤を禁止
                    for length in range(1, base_days):
                        for day in range(self.days_in_month - length + 1):
                            # 連続する勤務日を検出
                            work_days = []
                            for i in range(length):
                                work_day = self.model.NewBoolVar(f'work_day_{staff.name}_{day}_{i}')
                                shift_vars = [self.shifts[(staff.name, day + i, s)] for s in target_shifts]
                                
                                self.model.AddBoolOr(shift_vars).OnlyEnforceIf(work_day)
                                self.model.AddBoolAnd([v.Not() for v in shift_vars]).OnlyEnforceIf(work_day.Not())
                                
                                for sv in shift_vars:
                                    self.model.Add(sv <= work_day)
                                
                                work_days.append(work_day)
                            
                            # 連続する勤務日があった場合、その前後も勤務日であることを強制
                            is_work_sequence = self.model.NewBoolVar(f'is_work_sequence_{staff.name}_{day}_{length}')
                            self.model.AddBoolAnd(work_days).OnlyEnforceIf(is_work_sequence)
                            self.model.AddBoolOr([d.Not() for d in work_days]).OnlyEnforceIf(is_work_sequence.Not())
                            
                            # 前後どちらかが勤務日である必要がある
                            if day > 0:
                                prev_work = self.model.NewBoolVar(f'prev_work_{staff.name}_{day}')
                                prev_shifts = [self.shifts[(staff.name, day - 1, s)] for s in target_shifts]
                                self.model.AddBoolOr(prev_shifts).OnlyEnforceIf(prev_work)
                                self.model.AddBoolAnd([s.Not() for s in prev_shifts]).OnlyEnforceIf(prev_work.Not())
                            
                            if day + length < self.days_in_month:
                                next_work = self.model.NewBoolVar(f'next_work_{staff.name}_{day}')
                                next_shifts = [self.shifts[(staff.name, day + length, s)] for s in target_shifts]
                                self.model.AddBoolOr(next_shifts).OnlyEnforceIf(next_work)
                                self.model.AddBoolAnd([s.Not() for s in next_shifts]).OnlyEnforceIf(next_work.Not())
                            
                            # 連続勤務が見つかった場合、前後どちらかは必ず勤務日
                            if day > 0 and day + length < self.days_in_month:
                                self.model.AddBoolOr([is_work_sequence.Not(), prev_work, next_work])
                            elif day > 0:
                                self.model.AddBoolOr([is_work_sequence.Not(), prev_work])
                            elif day + length < self.days_in_month:
                                self.model.AddBoolOr([is_work_sequence.Not(), next_work])

            else:  # 嫌悪/回避の必須制約
                if constraint.target == "以上":
                    # base_daysの連続勤務以上を禁止
                    for day in range(self.days_in_month - base_days + 1):
                        consecutive_days = []
                        for i in range(base_days):
                            work_day = self.model.NewBoolVar(f'work_day_{staff.name}_{day}_{i}')
                            shift_vars = [self.shifts[(staff.name, day + i, s)] for s in target_shifts]
                            
                            self.model.AddBoolOr(shift_vars).OnlyEnforceIf(work_day)
                            self.model.AddBoolAnd([v.Not() for v in shift_vars]).OnlyEnforceIf(work_day.Not())
                            
                            # ★Fix
                            for sv in shift_vars:
                                self.model.Add(sv <= work_day)

                            consecutive_days.append(work_day)
                        self.model.AddBoolOr([d.Not() for d in consecutive_days])
                        logger.debug(f"{staff.name}の{day + 1}日目: {base_days}連勤以上を禁止")
                
                elif constraint.target == "以下":
                    # N日以下の連勤パターンを禁止（1日からN日まで）
                    for target_days in range(1, base_days + 1):
                        for day in range(self.days_in_month - target_days + 1):
                            work_days = []
                            for i in range(target_days):
                                work_day = self.model.NewBoolVar(f'work_day_{staff.name}_{day}_{i}')
                                shift_vars = [self.shifts[(staff.name, day + i, s)] for s in target_shifts]
                                
                                self.model.AddBoolOr(shift_vars).OnlyEnforceIf(work_day)
                                self.model.AddBoolAnd([v.Not() for v in shift_vars]).OnlyEnforceIf(work_day.Not())
                                
                                for sv in shift_vars:
                                    self.model.Add(sv <= work_day)
                                
                                work_days.append(work_day)
                            
                            # 連続する勤務日があった場合、その前後は必ず勤務日でなければならない
                            # （これにより、より長い連勤の一部として発生することを許可）
                            is_work_sequence = self.model.NewBoolVar(f'is_work_sequence_{staff.name}_{day}_{target_days}')
                            self.model.AddBoolAnd(work_days).OnlyEnforceIf(is_work_sequence)
                            self.model.AddBoolOr([d.Not() for d in work_days]).OnlyEnforceIf(is_work_sequence.Not())
                            
                            # 前後どちらかが勤務日でない場合は、このパターンを禁止
                            if day > 0 and day + target_days < self.days_in_month:
                                prev_work = self.model.NewBoolVar(f'prev_work_{staff.name}_{day}')
                                next_work = self.model.NewBoolVar(f'next_work_{staff.name}_{day}')
                                
                                prev_shifts = [self.shifts[(staff.name, day - 1, s)] for s in target_shifts]
                                next_shifts = [self.shifts[(staff.name, day + target_days, s)] for s in target_shifts]
                                
                                self.model.AddBoolOr(prev_shifts).OnlyEnforceIf(prev_work)
                                self.model.AddBoolOr(next_shifts).OnlyEnforceIf(next_work)
                                
                                # 連続勤務パターンが見つかった場合、前後どちらかは必ず勤務日
                                self.model.AddBoolOr([is_work_sequence.Not(), prev_work, next_work])
                            elif day > 0:
                                prev_work = self.model.NewBoolVar(f'prev_work_{staff.name}_{day}')
                                prev_shifts = [self.shifts[(staff.name, day - 1, s)] for s in target_shifts]
                                self.model.AddBoolOr(prev_shifts).OnlyEnforceIf(prev_work)
                                self.model.AddBoolOr([is_work_sequence.Not(), prev_work])
                            elif day + target_days < self.days_in_month:
                                next_work = self.model.NewBoolVar(f'next_work_{staff.name}_{day}')
                                next_shifts = [self.shifts[(staff.name, day + target_days, s)] for s in target_shifts]
                                self.model.AddBoolOr(next_shifts).OnlyEnforceIf(next_work)
                                self.model.AddBoolOr([is_work_sequence.Not(), next_work])

                elif constraint.target == "丁度":
                    # N連勤丁度を禁止
                    target_days = self.KANJI_TO_NUMBER.get(count_value, 1)
                    for day in range(self.days_in_month - target_days + 1):
                        # パターンの検出変数
                        is_exact_pattern = self.model.NewBoolVar(f'exact_pattern_{staff.name}_{day}_{target_days}')
                        
                        # N日間の連続勤務を検出
                        work_days = []
                        for i in range(target_days):
                            work_day = self.model.NewBoolVar(f'work_day_{staff.name}_{day}_{i}')
                            shift_vars = [self.shifts[(staff.name, day + i, s)] for s in target_shifts]
                            
                            # その日のいずれかのシフトが入っていれば work_day は True
                            self.model.AddBoolOr(shift_vars).OnlyEnforceIf(work_day)
                            self.model.AddBoolAnd([v.Not() for v in shift_vars]).OnlyEnforceIf(work_day.Not())
                            
                            work_days.append(work_day)
                        
                        # 前後の休みを検出
                        conditions = work_days.copy()
                        
                        # 月初め以外の場合のみ前日をチェック
                        if day > 0:
                            prev_rest = self.model.NewBoolVar(f'prev_rest_{staff.name}_{day}')
                            not_working = [self.shifts[(staff.name, day - 1, s)].Not() for s in target_shifts]
                            self.model.AddBoolAnd(not_working).OnlyEnforceIf(prev_rest)
                            self.model.AddBoolOr([self.shifts[(staff.name, day - 1, s)] for s in target_shifts]).OnlyEnforceIf(prev_rest.Not())
                            conditions.append(prev_rest)

                        # 月末以外の場合のみ翌日をチェック
                        if day + target_days < self.days_in_month:
                            next_rest = self.model.NewBoolVar(f'next_rest_{staff.name}_{day}')
                            not_working = [self.shifts[(staff.name, day + target_days, s)].Not() for s in target_shifts]
                            self.model.AddBoolAnd(not_working).OnlyEnforceIf(next_rest)
                            self.model.AddBoolOr([self.shifts[(staff.name, day + target_days, s)] for s in target_shifts]).OnlyEnforceIf(next_rest.Not())
                            conditions.append(next_rest)
                        
                        # すべての条件が成立する場合を禁止
                        self.model.AddBoolAnd(conditions).OnlyEnforceIf(is_exact_pattern)
                        self.model.AddBoolOr([v.Not() for v in conditions]).OnlyEnforceIf(is_exact_pattern.Not())
                        self.model.Add(is_exact_pattern == 0)
                        
                        logger.debug(f"{staff.name}の{day + 1}日目: {target_days}連勤丁度を禁止")

        # 選好制約の処理
        elif constraint.type == "選好":
            weight = (getattr(constraint, 'weight', None) or 
                     self.constraint_weights["選好"]["連続勤務"])
            multiplier = 1 if constraint.sub_category in ["愛好", "推奨"] else -1
            
            for target_days in target_days_range:
                for day in range(self.days_in_month - target_days + 1):
                    is_pattern = self.model.NewBoolVar(f'work_pattern_{staff.name}_{day}_{target_days}')
                    
                    # 連続勤務のパターンを検出
                    work_days = []
                    for offset in range(target_days):
                        work_day = self.model.NewBoolVar(f'work_day_{staff.name}_{day}_{offset}')
                        shift_vars = [self.shifts[(staff.name, day + offset, s)] for s in target_shifts]
                        
                        self.model.AddBoolOr(shift_vars).OnlyEnforceIf(work_day)
                        self.model.AddBoolAnd([v.Not() for v in shift_vars]).OnlyEnforceIf(work_day.Not())
                        
                        for sv in shift_vars:
                            self.model.Add(sv <= work_day)
                        
                        work_days.append(work_day)

                    # 前後の休みを検出
                    if day > 0:
                        prev_rest = self.model.NewBoolVar(f'prev_rest_{staff.name}_{day}')
                        prev_shifts = [self.shifts[(staff.name, day - 1, s)] for s in target_shifts]
                        self.model.AddBoolOr(prev_shifts).OnlyEnforceIf(prev_rest.Not())
                        self.model.AddBoolAnd([s.Not() for s in prev_shifts]).OnlyEnforceIf(prev_rest)
                    else:
                        prev_rest = self.model.NewConstant(1)  # 月初めは常にTrue

                    if day + target_days < self.days_in_month:
                        next_rest = self.model.NewBoolVar(f'next_rest_{staff.name}_{day}')
                        next_shifts = [self.shifts[(staff.name, day + target_days, s)] for s in target_shifts]
                        self.model.AddBoolOr(next_shifts).OnlyEnforceIf(next_rest.Not())
                        self.model.AddBoolAnd([s.Not() for s in next_shifts]).OnlyEnforceIf(next_rest)
                    else:
                        next_rest = self.model.NewConstant(1)  # 月末は常にTrue

                    # パターンの検出（連続勤務があり、かつ前後が休み）
                    self.model.AddBoolAnd(work_days + [prev_rest, next_rest]).OnlyEnforceIf(is_pattern)
                    self.model.AddBoolOr([d.Not() for d in work_days] + [prev_rest.Not(), next_rest.Not()]).OnlyEnforceIf(is_pattern.Not())

                    # 目的関数に重みを追加
                    self.objective_terms.append(is_pattern * weight * multiplier)
                    logger.debug(f"{staff.name}の{day + 1}日目: {target_days}連勤 "
                               f"{'愛好' if constraint.sub_category in ['愛好', '推奨'] else '嫌悪'} "
                               f"(重み: {weight * multiplier})")

    def add_local_consecutive_work(self):
        """ローカルの連続勤務制限
        
        連続勤務の定義：
        - 対象シフト = 全シフト - [公]
        """
        logger.debug("=== ローカルの連続勤務制限を設定 ===")
        
        # 対象となるシフトタイプを定義
        target_shifts = [s for s in self.SHIFT_TYPES if s != '公' and s != '_']
        
        for staff in self.staff_data_list:
            for constraint in staff.constraints:
                if constraint.category == "連続勤務":
                    self.add_consecutive_work_pattern(
                        staff=staff,
                        constraint=constraint,
                        target_shifts=target_shifts
                    )

    def add_local_consecutive_dayshift_work(self):
        """ローカルの日勤帯連続勤務制限
        
        日勤帯連続勤務の定義：
        - 対象シフト = 全シフト - [／(夜勤), ×(夜勤明け), 公(公休), _(未設定)]
        """
        logger.debug("=== ローカルの日勤帯連続勤務制限を設定 ===")
        
        # 対象となるシフトタイプを定義（全シフトからリセットシフトを除外）
        target_shifts = [s for s in self.SHIFT_TYPES if s not in ['／', '×', '公', '_']]
        
        for staff in self.staff_data_list:
            for constraint in staff.constraints:
                if constraint.category == "日勤帯連勤":
                    self.add_consecutive_work_pattern(
                        staff=staff,
                        constraint=constraint,
                        target_shifts=target_shifts
                    )

    def add_global_consecutive_work(self):
        """グローバルの連続勤務制限
        
        連続勤務の定義：
        - 対象シフト = 全シフト - [公]
        - グローバルルール適用対象のスタッフに同一制約を適用
        """
        logger.debug("=== グローバルの連続勤務制限を設定 ===")
        
        # 対象となるシフトタイプを定義（ローカルと同じ）
        target_shifts = [s for s in self.SHIFT_TYPES if s != '公' and s != '_']
        
        # rule_dataから連続勤務の制約を取得して対象スタッフに適用
        for constraint in self.rule_data.preference_constraints:
            if constraint.category == "連続勤務" and constraint.times == "全員":
                # グローバルルール適用対象のスタッフにのみ制約を適用
                for staff in self.staff_data_list:
                    if not staff.is_global_rule:  # グローバルルール除外でないスタッフのみに適用
                        self.add_consecutive_work_pattern(
                            staff=staff,
                            constraint=constraint,
                            target_shifts=target_shifts
                        )

    def add_global_consecutive_dayshift_work(self):
        """グローバルの日勤帯連続勤務制限
        
        日勤帯連続勤務の定義：
        - 対象シフト = 全シフト - [／(夜勤), ×(夜勤明け), 公(公休), _(未設定)]
        - グローバルルール適用対象のスタッフに同一制約を適用
        """
        logger.debug("=== グローバルの日勤帯連続勤務制限を設定 ===")
        
        # 対象となるシフトタイプを定義（全シフトからリセットシフトを除外）
        target_shifts = [s for s in self.SHIFT_TYPES if s not in ['／', '×', '公', '_']]
        
        # rule_dataから日勤帯連勤の制約を取得して対象スタッフに適用
        for constraint in self.rule_data.preference_constraints:
            if constraint.category == "日勤帯連勤" and constraint.times == "全員":
                # グローバルルール適用対象のスタッフにのみ制約を適用
                for staff in self.staff_data_list:
                    if not staff.is_global_rule:  # グローバルルール除外でないスタッフのみに適用
                        self.add_consecutive_work_pattern(
                            staff=staff,
                            constraint=constraint,
                            target_shifts=target_shifts
                        )

    def add_global_consecutive_shift(self):
        """グローバルの連続シフト制限
        
        グローバルルール適用対象のスタッフに対して、連続シフトの制限を適用する。
        
        夜勤の場合：
        - ×から始まり、[／,×,公]以外のシフトが出現するまでの範囲を1つの区間とする
        - 区間内の／の出現回数 + 1 = 連続夜勤回数
        - 例：×公／×公／▲ → ▲で区間終了、／が2回なので連続夜勤3回
        """
        logger.debug("=== グローバルの連続シフト制限を設定 ===")

        for constraint in self.rule_data.preference_constraints:
            if constraint.category == "連続シフト":
                target_shift_name = constraint.count or ""
                shift_type = self.SHIFT_TYPE_MAPPING.get(target_shift_name)
                if not shift_type:
                    logger.warning(f"未定義のシフトタイプ: {target_shift_name}")
                    continue

                consecutive_count_str = str(constraint.final or "")
                consecutive_count = self.KANJI_TO_NUMBER.get(consecutive_count_str, 1)

                for staff in self.staff_data_list:
                    if not staff.is_global_rule:
                        # 夜勤の場合
                        if target_shift_name == "夜勤":
                            logger.debug(f"=== {staff.name}の夜勤連続制限（{consecutive_count}回）===")
                            
                            # リセット対象となるシフト
                            RESET_SHIFTS = ['▼', '日', '▲', '☆', '_']
                            
                            # スタッフごとにdimensionを作成
                            night_count = self.model.NewIntVar(0, consecutive_count - 1, 
                                                             f'night_count_{staff.name}')
                            night_counts = [night_count]  # 初日用

                            # 各日のdimensionを作成
                            for day in range(1, self.days_in_month):
                                next_count = self.model.NewIntVar(0, consecutive_count - 1, 
                                                                f'night_count_{staff.name}_{day}')
                                night_counts.append(next_count)

                            # 初期値の設定（月初が×なら1、それ以外は0）
                            self.model.Add(night_counts[0] == 1).OnlyEnforceIf(
                                self.shifts[(staff.name, 0, '×')])
                            self.model.Add(night_counts[0] == 0).OnlyEnforceIf(
                                self.shifts[(staff.name, 0, '×')].Not())

                            # 2日目以降の状態遷移を設定
                            for day in range(1, self.days_in_month):
                                # リセット条件をチェック
                                reset_vars = [self.shifts[(staff.name, day, s)] 
                                            for s in RESET_SHIFTS]
                                is_reset = self.model.NewBoolVar(f'is_reset_{staff.name}_{day}')
                                self.model.AddBoolOr(reset_vars).OnlyEnforceIf(is_reset)
                                self.model.AddBoolAnd([v.Not() for v in reset_vars]).OnlyEnforceIf(
                                    is_reset.Not())

                                # 夜勤入り(／)の出現をチェック
                                night_shift = self.shifts[(staff.name, day, '／')]

                                # 状態遷移の定義
                                # リセット時は0
                                self.model.Add(night_counts[day] == 0).OnlyEnforceIf(is_reset)
                                
                                # リセットでない場合
                                if constraint.type == "必須":
                                    # 夜勤入りの場合の値の更新
                                    self.model.Add(night_counts[day] == night_counts[day-1] + 1).OnlyEnforceIf(
                                        [is_reset.Not(), night_shift])
                                    # それ以外は前日の値を維持
                                    self.model.Add(night_counts[day] == night_counts[day-1]).OnlyEnforceIf(
                                        [is_reset.Not(), night_shift.Not()])

                                    # 制限の設定
                                    if constraint.target == "以上":
                                        # consecutive_count以上を禁止
                                        self.model.Add(night_counts[day] < consecutive_count)
                                    else:  # "丁度"の場合
                                        # リセット時にカウンターがconsequtive_count-1だった場合を禁止
                                        is_exact_at_reset = self.model.NewBoolVar(f'exact_at_reset_{staff.name}_{day}')
                                        self.model.Add(night_counts[day-1] == consecutive_count - 1).OnlyEnforceIf(
                                            [is_reset, is_exact_at_reset])
                                        self.model.Add(night_counts[day-1] != consecutive_count - 1).OnlyEnforceIf(
                                            is_exact_at_reset.Not())
                                        self.model.Add(is_exact_at_reset == 0)
                                else:
                                    # 選好制約の場合
                                    weight = constraint.weight
                                    is_violation = self.model.NewBoolVar(f'violation_{staff.name}_{day}')
                                    
                                    if constraint.target == "以上":
                                        # 以上の場合（consecutive_count以上で違反）
                                        self.model.Add(night_counts[day] >= consecutive_count).OnlyEnforceIf(is_violation)
                                        self.model.Add(night_counts[day] < consecutive_count).OnlyEnforceIf(is_violation.Not())
                                    else:  # "丁度"の場合
                                        # 丁度の場合（consecutive_count以外で違反）
                                        self.model.Add(night_counts[day] != consecutive_count).OnlyEnforceIf(is_violation)
                                        self.model.Add(night_counts[day] == consecutive_count).OnlyEnforceIf(is_violation.Not())
                                    
                                    # 違反があった場合に負の重みを付与
                                    self.objective_terms.append(is_violation * weight * -1)

                            logger.debug(f"{staff.name}の夜勤連続制限を設定完了")

                        # 夜勤以外の場合（既存の処理）
                        else:
                            if constraint.type == "必須":
                                for day in range(self.days_in_month - consecutive_count + 1):
                                    consecutive_vars = [
                                        self.shifts[(staff.name, day + i, shift_type)]
                                        for i in range(consecutive_count)
                                    ]
                                    self.model.AddBoolOr([v.Not() for v in consecutive_vars])
                                    logger.debug(
                                        f"{staff.name}の{day + 1}日目: "
                                        f"{target_shift_name}{consecutive_count}連続を禁止"
                                    )
                            else:
                                weight = constraint.weight
                                for day in range(self.days_in_month - consecutive_count + 1):
                                    is_pattern = self.model.NewBoolVar(
                                        f'consecutive_{staff.name}_{day}_{consecutive_count}'
                                    )
                                    consecutive_vars = [
                                        self.shifts[(staff.name, day + i, shift_type)]
                                        for i in range(consecutive_count)
                                    ]
                                    self.model.AddBoolAnd(consecutive_vars).OnlyEnforceIf(is_pattern)
                                    self.model.AddBoolOr([v.Not() for v in consecutive_vars]).OnlyEnforceIf(
                                        is_pattern.Not())

                                    self.objective_terms.append(is_pattern * weight * -1)
                                    logger.debug(
                                        f"{staff.name}の{day + 1}日目: {target_shift_name}"
                                        f"{consecutive_count}連続 回避 (重み: {weight * -1})"
                                    )

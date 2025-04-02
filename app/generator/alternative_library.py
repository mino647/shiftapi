"""
オルタナティブなシフト制約処理ライブラリ
"""

import logging
from typing import Dict, List, Any
from ortools.sat.python import cp_model
from datetime import datetime
from .logger import logger
from ..from_dict import StaffData, ShiftData, RuleData, ShiftEntry
from .mapping import (
    SHIFT_TYPES,
    SHIFT_TYPE_MAPPING,
    KANJI_TO_NUMBER,
    STATUS_MAP
)
import dataclasses

class AlternativeLibrary:
    """オルタナティブなシフト制約処理ライブラリ"""
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

    def add_alternative_constraint(self):
        """オルタナティブな制約の実装例"""
        logger.debug("=== オルタナティブな制約の設定 ===")
        
        # シフト間隔制約を処理
        self.add_local_shift_interval_constraint()
        self.add_global_shift_interval_constraint()
        
        # 出シフト制約を処理
        self.add_specific_day_shift_constraint()
        
    def add_local_shift_interval_constraint(self):
        """ローカルのシフト間隔制約
        
        特定のシフトが出現した後、再び同じシフトが指定日数以内に出現するかをチェック
        - 嫌悪：指定日数以内に再発生を避ける
        - 愛好：指定日数以内に再発生を求める
        """
        logger.debug("=== ローカルのシフト間隔制約を設定 ===")
        
        # スタッフごとに制約を適用
        for staff in self.staff_data_list:
            for constraint in staff.constraints:
                if constraint.category == "シフト間隔":
                    self.add_shift_interval_constraint(staff, constraint)
    
    def add_global_shift_interval_constraint(self):
        """グローバルのシフト間隔制約
        
        特定のシフトが出現した後、再び同じシフトが指定日数以内に出現するかをチェック
        - 嫌悪：指定日数以内に再発生を避ける
        - 愛好：指定日数以内に再発生を求める
        """
        logger.debug("=== グローバルのシフト間隔制約を設定 ===")
        
        # rule_dataからシフト間隔制約を取得して対象スタッフに適用
        for constraint in self.rule_data.preference_constraints:
            if constraint.category == "シフト間隔" and constraint.times == "全員":
                # グローバルルール適用対象のスタッフにのみ制約を適用
                for staff in self.staff_data_list:
                    if not staff.is_global_rule:  # グローバルルール除外でないスタッフのみに適用
                        self.add_shift_interval_constraint(staff, constraint)
    
    def add_shift_interval_constraint(self, staff: StaffData, constraint: Any):
        """シフト間隔制約の実装
        
        Args:
            staff: 対象スタッフ
            constraint: 制約データ
                type: "必須" or "選好"
                category: "シフト間隔"
                sub_category: "嫌悪" or "愛好"（value1から変換）
                count: 対象シフト種類（value2から変換）
                target: 間隔日数（value3から変換）
        """
        # 対象シフトを取得
        target_shift_name = constraint.count
        shift_type = self.SHIFT_TYPE_MAPPING.get(target_shift_name, target_shift_name)
        
        # シフト種類が有効かチェック
        if shift_type not in self.SHIFT_TYPES:
            logger.warning(f"未定義のシフトタイプ: {target_shift_name}")
            return
        
        # 間隔日数を取得
        interval_days = int(constraint.target)
        
        # 好みタイプを取得
        preference_type = constraint.sub_category  # "嫌悪" or "愛好"
        
        logger.debug(f"シフト間隔制約: {staff.name}の{target_shift_name}（{preference_type}、{interval_days}日以内）")
        
        # 各開始日から見て、interval_days以内に同じシフトが現れるかをチェック
        for start_day in range(self.days_in_month):
            # 対象シフトがある日のみ処理
            start_shift = self.shifts[(staff.name, start_day, shift_type)]
            
            # 間隔内に出現する同一シフトをチェック
            interval_end = min(start_day + interval_days + 1, self.days_in_month)
            if interval_end <= start_day + 1:
                continue  # 月末でチェック対象日がない場合はスキップ
            
            # 間隔内の日数分の変数を準備
            interval_shifts = []
            for check_day in range(start_day + 1, interval_end):
                interval_shifts.append(self.shifts[(staff.name, check_day, shift_type)])
            
            if not interval_shifts:
                continue  # チェック対象日がない場合はスキップ
            
            # 必須制約の処理
            if constraint.type == "必須":
                if preference_type == "嫌悪":
                    # 嫌悪：指定日数以内に再発生を禁止
                    # 「シフトが入る」→「間隔内に同じシフトは入らない」
                    for interval_shift in interval_shifts:
                        self.model.AddImplication(start_shift, interval_shift.Not())
                    logger.debug(f"{staff.name}の{start_day + 1}日目: {target_shift_name}の後{interval_days}日以内の再発生を禁止")
                
                elif preference_type == "愛好":
                    # 愛好：過去N日以内に同じシフトが入っていることを強制（最初の出現は除外）
                    # 各日でシフトの出現をチェック
                    for current_day in range(self.days_in_month):
                        current_shift = self.shifts[(staff.name, current_day, shift_type)]
                        
                        # 過去N日以内のシフト変数（現在の日は含まない）
                        past_interval_start = max(0, current_day - interval_days)
                        past_interval_shifts = []
                        for past_day in range(past_interval_start, current_day):
                            past_interval_shifts.append(self.shifts[(staff.name, past_day, shift_type)])
                        
                        # 過去すべてのシフト（月初から現在の前日まで）
                        all_past_shifts = []
                        for past_day in range(0, current_day):
                            all_past_shifts.append(self.shifts[(staff.name, past_day, shift_type)])
                        
                        # 過去の全日程でのシフト出現をチェック（現在日の前日まで）
                        has_any_past_shift = self.model.NewBoolVar(f'has_any_past_{shift_type}_{staff.name}_{current_day}')
                        if all_past_shifts:
                            self.model.AddBoolOr(all_past_shifts).OnlyEnforceIf(has_any_past_shift)
                            self.model.AddBoolAnd([s.Not() for s in all_past_shifts]).OnlyEnforceIf(has_any_past_shift.Not())
                        else:
                            # 最初の日は過去のシフトが存在しない
                            self.model.Add(has_any_past_shift == 0)
                        
                        # 過去N日以内のいずれかの日にシフトがあるかをチェック
                        has_past_interval_shift = self.model.NewBoolVar(f'has_past_interval_{shift_type}_{staff.name}_{current_day}')
                        if past_interval_shifts:
                            self.model.AddBoolOr(past_interval_shifts).OnlyEnforceIf(has_past_interval_shift)
                            self.model.AddBoolAnd([s.Not() for s in past_interval_shifts]).OnlyEnforceIf(has_past_interval_shift.Not())
                        else:
                            # 過去N日間がない場合（月初など）
                            self.model.Add(has_past_interval_shift == 0)
                        
                        # 制約の追加：
                        # 1. 最初のシフト出現の場合は制約なし
                        # 2. 2回目以降のシフト出現の場合、過去N日以内に同じシフトが必要
                        
                        # 「このシフトがあり」かつ「過去にシフトがある（2回目以降）」場合
                        needs_past_interval = self.model.NewBoolVar(f'needs_past_interval_{shift_type}_{staff.name}_{current_day}')
                        self.model.AddBoolAnd([current_shift, has_any_past_shift]).OnlyEnforceIf(needs_past_interval)
                        self.model.AddBoolOr([current_shift.Not(), has_any_past_shift.Not()]).OnlyEnforceIf(needs_past_interval.Not())
                        
                        # 必要な場合は過去N日以内にシフトが必要
                        self.model.AddImplication(needs_past_interval, has_past_interval_shift)
                    
                    logger.debug(f"{staff.name}の{target_shift_name}: 過去{interval_days}日以内に同じシフトが入っていることを強制（最初の出現は除外）")
            
            # 選好制約の処理
            else:
                # 重みを取得
                weight = (getattr(constraint, 'weight', None) or 
                         self.constraint_weights.get("選好", {}).get("シフト間隔", 200))
                
                # 間隔内に同じシフトが出現するかのフラグ
                has_interval_shift = self.model.NewBoolVar(f'has_{shift_type}_{staff.name}_{start_day}_interval')
                self.model.AddBoolOr(interval_shifts).OnlyEnforceIf(has_interval_shift)
                self.model.AddBoolAnd([s.Not() for s in interval_shifts]).OnlyEnforceIf(has_interval_shift.Not())
                
                if preference_type == "嫌悪":
                    # 嫌悪：指定日数以内に再発生があればペナルティ
                    # start_shiftがTrueで、かつhas_interval_shiftがTrueの場合にペナルティ
                    interval_violation = self.model.NewBoolVar(f'interval_violation_{staff.name}_{start_day}')
                    self.model.AddBoolAnd([start_shift, has_interval_shift]).OnlyEnforceIf(interval_violation)
                    self.model.AddBoolOr([start_shift.Not(), has_interval_shift.Not()]).OnlyEnforceIf(interval_violation.Not())
                    
                    # 目的関数に負の重みを追加
                    self.objective_terms.append(interval_violation * weight * -1)
                    logger.debug(f"{staff.name}の{start_day + 1}日目: {target_shift_name}の後{interval_days}日以内の再発生を回避 (重み: {weight * -1})")
                
                elif preference_type == "愛好":
                    # 愛好：指定日数以内に再発生があれば報酬を与える
                    # start_shiftがTrueで、かつhas_interval_shiftがTrueの場合に報酬
                    interval_success = self.model.NewBoolVar(f'interval_success_{staff.name}_{start_day}')
                    self.model.AddBoolAnd([start_shift, has_interval_shift]).OnlyEnforceIf(interval_success)
                    self.model.AddBoolOr([start_shift.Not(), has_interval_shift.Not()]).OnlyEnforceIf(interval_success.Not())
                    
                    # 目的関数に正の重みを追加（成功時に報酬）
                    self.objective_terms.append(interval_success * weight)
                    logger.debug(f"{staff.name}の{start_day + 1}日目: {target_shift_name}の後{interval_days}日以内に再発生で報酬 (重み: {weight})")

    def add_specific_day_shift_constraint(self):
        """特定日の出シフト制約
        
        指定された日に対象スタッフの勤務を早番、日勤、遅番のみに限定する制約
        type: 必須
        category: 出シフト
        subcategory: N日（特定の日付を指定）
        target: 出勤
        """
        logger.debug("=== 特定日の出シフト制約を設定 ===")
        
        # 早番、日勤、遅番のシフトコード
        allowed_shifts = ["▲", "日", "▼"]
        
        # スタッフごとに制約を適用
        for staff in self.staff_data_list:
            for constraint in staff.constraints:
                if constraint.type == "必須" and constraint.category == "出シフト" and constraint.target == "出勤":
                    try:
                        # subcategoryから特定の日付を取得（例: "14" → 14）
                        target_day = int(constraint.sub_category.replace("日", "")) - 1  # 0-indexedに変換
                        
                        # 月の日数内かチェック
                        if target_day < 0 or target_day >= self.days_in_month:
                            logger.warning(f"出シフト制約の日付が月の範囲外: {constraint.sub_category}, スタッフ: {staff.name}")
                            continue
                            
                        logger.debug(f"{staff.name}の出シフト制約: {target_day+1}日は早番/日勤/遅番のみ許可")
                        
                        # 指定された日に対して制約を適用
                        # 禁止されるシフトタイプ（夜勤、夜勤明け、公休など）
                        for shift_type in self.SHIFT_TYPES:
                            if shift_type not in allowed_shifts:
                                # 禁止シフトは0に設定（使用しない）
                                self.model.Add(self.shifts[(staff.name, target_day, shift_type)] == 0)
                        
                        # 許可されるシフトタイプのいずれかが必ず入るよう設定
                        allowed_shifts_vars = []
                        for shift_type in allowed_shifts:
                            allowed_shifts_vars.append(self.shifts[(staff.name, target_day, shift_type)])
                        
                        # 指定された日に許可されたシフトのいずれかが入るよう制約
                        self.model.AddBoolOr(allowed_shifts_vars)
                        
                    except ValueError:
                        logger.warning(f"出シフト制約の日付解析エラー: {constraint.sub_category}, スタッフ: {staff.name}")

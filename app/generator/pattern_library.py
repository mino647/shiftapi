"""
パターンに関する制約を実装するライブラリ
"""

import logging
from typing import Dict, List
from ortools.sat.python import cp_model
from .logger import logger
from ..from_dict import StaffData, ShiftData, RuleData, ShiftEntry
from datetime import datetime
from .mapping import (
    SHIFT_TYPES,
    SHIFT_TYPE_MAPPING,
    KANJI_TO_NUMBER,
    STATUS_MAP
)

class PatternLibrary:
    """シフトパターンに関する制約（曜日希望、ペア制約など）を扱うライブラリ"""
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

    def add_night_pattern(self):
        """制約5: 夜勤パターンの設定"""
        logger.debug("=== 制約5: 夜勤パターンの設定 ===")

        # 夜勤最大回数が0のスタッフは月初の×を禁止
        for staff_data in self.staff_data_list:
            if staff_data.shift_counts.get('夜勤', {}).get('max', 0) == 0:
                c = self.model.Add(self.shifts[(staff_data.name, 0, '×')] == 0)
                c.WithName(f"【夜勤不可スタッフの月初×禁止】{staff_data.name}")

        for stf in self.staff_list:
            for d in range(self.days_in_month - 2):
                c1 = self.model.Add(
                    self.shifts[(stf, d+1, '×')] == 1
                ).OnlyEnforceIf(self.shifts[(stf, d, '／')])
                c1.WithName(f"【夜勤→明け】{stf}:{d+1}→×")

                c2 = self.model.Add(
                    self.shifts[(stf, d+2, '公')] == 1
                ).OnlyEnforceIf(self.shifts[(stf, d, '／')])
                c2.WithName(f"【夜勤→明け→公休】{stf}:{d+2}")

                c3 = self.model.Add(
                    self.shifts[(stf, d, '／')] == 1
                ).OnlyEnforceIf(self.shifts[(stf, d+1, '×')])
                c3.WithName(f"【明けの前は夜勤】{stf}:{d+1}")

            # 月初(×から始まる場合は翌日が公休)
            if self.days_in_month > 1:
                c4 = self.model.Add(
                    self.shifts[(stf, 1, '公')] == 1
                ).OnlyEnforceIf(self.shifts[(stf, 0, '×')])
                c4.WithName(f"【月初夜勤明け→公休】{stf}")

            # 月末(末日の前日が／の場合は末日が×)
            if self.days_in_month >= 2:
                c5 = self.model.Add(
                    self.shifts[(stf, self.days_in_month - 1, '×')] == 1
                ).OnlyEnforceIf(self.shifts[(stf, self.days_in_month - 2, '／')])
                c5.WithName(f"【末日の前日が夜勤→末日が明け】{stf}")

    def add_pairing_constraint(self):
        """制約: ペアリング制約の処理"""
        logger.debug("=== ペアリング制約の設定 ===")

        for staff in self.staff_data_list:
            for constraint in staff.constraints:
                if constraint.category == "ペアリング":
                    # シフトタイプの変換（元のシフトと対象シフト）
                    if constraint.count and constraint.target:
                        source_type = self.SHIFT_TYPE_MAPPING.get(constraint.count, constraint.count)
                        target_type = self.SHIFT_TYPE_MAPPING.get(constraint.target, constraint.target)
                        
                        if source_type not in self.SHIFT_TYPES or target_type not in self.SHIFT_TYPES:
                            logger.warning(f"無効な勤務区分: 元={constraint.count}, 対象={constraint.target}")
                            continue
                    else:
                        logger.warning(f"勤務区分が指定されていません")
                        continue
                    
                    # ペアを組むスタッフの存在確認
                    target_staff = next((s for s in self.staff_data_list if s.name == constraint.sub_category), None)
                    if target_staff is None:
                        logger.warning(f"ペアリング対象のスタッフが見つかりません: {constraint.sub_category}")
                        continue

                    # 各日のペアリング状態を表す変数を作成
                    pair_days = []
                    for day in range(self.days_in_month):
                        is_pair = self.model.NewBoolVar(
                            f'is_pair_{staff.name}_{constraint.sub_category}_{day}_{source_type}_{target_type}'
                        )
                        
                        # 両者のシフト割り当て
                        staff_shift = self.shifts[(staff.name, day, source_type)]
                        target_shift = self.shifts[(constraint.sub_category, day, target_type)]
                        
                        # ペアの条件を定義（一方が source_type のとき、他方が target_type）
                        self.model.Add(
                            staff_shift == 1
                        ).OnlyEnforceIf(is_pair)
                        self.model.Add(
                            target_shift == 1
                        ).OnlyEnforceIf(is_pair)
                        
                        # is_pair が False の場合、両方が成立しない
                        self.model.Add(
                            staff_shift + target_shift <= 1
                        ).OnlyEnforceIf(is_pair.Not())
                        
                        pair_days.append(is_pair)
                    
                    # ペアリング日数の合計
                    pair_count = sum(pair_days)
                    
                    if constraint.type == "必須":
                        if constraint.times == "全て":
                            # 両者の最大回数を取得
                            staff_source_max = staff.shift_counts.get(constraint.count, {}).get('max', 0)
                            target_staff_max = target_staff.shift_counts.get(constraint.target, {}).get('max', 0)
                            
                            # 回数の少ない方を基準にする
                            if staff_source_max <= target_staff_max:
                                base_staff, other_staff = staff.name, constraint.sub_category
                                base_type, other_type = source_type, target_type
                            else:
                                base_staff, other_staff = constraint.sub_category, staff.name
                                base_type, other_type = target_type, source_type
                                
                            logger.info(
                                f"必須ペアリング(全て)を設定: {base_staff}の{base_type}が入るときは"
                                f"必ず{other_staff}も{other_type}"
                            )
                            
                            # 基準となる方のシフトが入るときは、必ずもう片方も対応するシフトを取る
                            for day in range(self.days_in_month):
                                c = self.model.Add(
                                    self.shifts[(other_staff, day, other_type)] == 1
                                ).OnlyEnforceIf(self.shifts[(base_staff, day, base_type)])
                                c.WithName(
                                    f"【必須ペアリング】{base_staff}({base_type})→"
                                    f"{other_staff}({other_type}):{day+1}日"
                                )
                        
                        else:  # 通常の必須制約
                            if constraint.times:
                                target_count = self.KANJI_TO_NUMBER.get(constraint.times.replace("まで", ""), 0)
                                if target_count > 0:
                                    logger.debug(
                                        f"必須ペアリングを設定: {staff.name}の{source_type}と"
                                        f"{constraint.sub_category}の{target_type}を最低{target_count}回"
                                    )
                                    c = self.model.Add(pair_count >= target_count)
                                    c.WithName(
                                        f"【必須ペアリング】{staff.name}({source_type})と"
                                        f"{constraint.sub_category}({target_type}):{target_count}回以上"
                                    )
                                else:
                                    logger.warning(f"回数が指定されていません")
                                    continue
                            else:
                                logger.warning(f"回数が指定されていません")
                                continue
                        
                    else:  # 選好制約
                        weight = self.constraint_weights["選好"]["ペアリング"]
                        if constraint.times == "全て":
                            target_count = self.days_in_month
                            logger.debug(
                                f"選好ペアリング(全て)を設定: {staff.name}の{source_type}と"
                                f"{constraint.sub_category}の{target_type}を最大{target_count}回"
                            )
                        elif constraint.times:
                            target_count = self.KANJI_TO_NUMBER.get(constraint.times.replace("まで", ""), 0)
                            if target_count <= 0:
                                logger.warning(f"無効な回数指定: {constraint.times}")
                                continue
                            logger.debug(
                                f"選好ペアリングを設定: {staff.name}の{source_type}と"
                                f"{constraint.sub_category}の{target_type}を{target_count}回まで評価"
                            )
                        else:
                            logger.warning(f"回数が指定されていません")
                            continue
                        
                        # 達成分のみを評価（超過分は評価しない）
                        achieved_pairs = self.model.NewIntVar(
                            0, target_count,
                            f'achieved_pairs_{staff.name}_{constraint.sub_category}_{source_type}_{target_type}'
                        )
                        self.model.AddMinEquality(achieved_pairs, [pair_count, target_count])
                        self.objective_terms.append(achieved_pairs * weight)

        logger.debug("ペアリング制約の設定完了")

    def add_separate_constraint(self):
        """制約: セパレート制約の処理"""
        logger.debug("=== セパレート制約の設定 ===")

        for staff in self.staff_data_list:
            for constraint in staff.constraints:
                if constraint.category != "セパレート":
                    continue

                # 対象スタッフの存在確認
                target_staff = next(
                    (s for s in self.staff_data_list if s.name == constraint.sub_category),
                    None
                )
                if target_staff is None:
                    logger.warning(f"セパレート対象のスタッフが見つかりません: {constraint.sub_category}")
                    continue

                # シフトタイプの正規化
                source_type = self.SHIFT_TYPE_MAPPING.get(str(constraint.count), str(constraint.count))
                target_type = self.SHIFT_TYPE_MAPPING.get(str(constraint.target), str(constraint.target))

                if source_type not in self.SHIFT_TYPES or target_type not in self.SHIFT_TYPES:
                    logger.warning(f"無効な勤務区分: 主体={constraint.count}, 客体={constraint.target}")
                    continue

                if constraint.times == "全て":
                    # 完全分離（既存の動作）
                    if constraint.type == "必須":
                        for day in range(self.days_in_month):
                            c = self.model.Add(
                                self.shifts[(staff.name, day, source_type)] +
                                self.shifts[(constraint.sub_category, day, target_type)] <= 1
                            )
                            c.WithName(
                                f"【セパレート制約_完全分離】{staff.name}({source_type})と"
                                f"{constraint.sub_category}({target_type}):{day+1}日"
                            )
                    else:  # 選好制約
                        for day in range(self.days_in_month):
                            overlap = self.model.NewBoolVar(
                                f'separate_overlap_{staff.name}_{constraint.sub_category}_{day}'
                            )
                            # 両方のシフトが入っているときにoverlap=1
                            self.model.Add(
                                self.shifts[(staff.name, day, source_type)] +
                                self.shifts[(constraint.sub_category, day, target_type)] <= 1 + overlap
                            )
                            self.objective_terms.append(
                                overlap * -self.constraint_weights["選好"]["セパレート"]
                            )

                else:  # 回数指定がある場合
                    try:
                        timecombo = self.KANJI_TO_NUMBER.get(
                            str(constraint.times).replace("まで", "") if constraint.times else "",
                            0
                        )
                        if timecombo <= 0:
                            logger.warning(f"無効な回数指定です: {constraint.times}")
                            continue

                        # 重複日を数える変数を作成
                        overlap_days = []
                        for day in range(self.days_in_month):
                            overlap = self.model.NewBoolVar(
                                f'separate_overlap_{staff.name}_{constraint.sub_category}_{day}'
                            )
                            # 両方のシフトが入っているときにoverlap=1
                            self.model.Add(overlap == 1).OnlyEnforceIf([
                                self.shifts[(staff.name, day, source_type)],
                                self.shifts[(constraint.sub_category, day, target_type)]
                            ])
                            self.model.Add(overlap == 0).OnlyEnforceIf([
                                self.shifts[(staff.name, day, source_type)].Not()
                            ])
                            self.model.Add(overlap == 0).OnlyEnforceIf([
                                self.shifts[(constraint.sub_category, day, target_type)].Not()
                            ])
                            overlap_days.append(overlap)

                        total_overlaps = sum(overlap_days)

                        if constraint.type == "必須":
                            # 重複回数がtimecombo以下であることを保証
                            c = self.model.Add(total_overlaps <= timecombo)
                            c.WithName(
                                f"【セパレート制約_回数制限】{staff.name}({source_type})と"
                                f"{constraint.sub_category}({target_type}):{timecombo}回まで"
                            )
                        else:  # 選好制約
                            # timecomboを超える重複にペナルティを付与
                            excess = self.model.NewIntVar(
                                0, self.days_in_month,
                                f'separate_excess_{staff.name}_{constraint.sub_category}'
                            )
                            self.model.Add(excess >= total_overlaps - timecombo)
                            self.objective_terms.append(
                                excess * -self.constraint_weights["選好"]["セパレート"]
                            )

                    except (ValueError, AttributeError):
                        logger.warning(f"回数の解析に失敗しました: {constraint.times}")
                        continue

    def add_weekday_constraint(self):
        """制約: 曜日希望の処理"""
        logger.debug("=== 曜日希望制約の設定 ===")

        for staff in self.staff_data_list:
            for constraint in staff.constraints:
                if constraint.category != "曜日希望":
                    continue

                # 出勤（早番/日勤/遅番のいずれか）の特別処理
                is_working_shift = constraint.times == "出勤"
                
                # 通常の処理（出勤以外の場合）
                if not is_working_shift:
                    # シフトタイプの正規化
                    shift_type = self.SHIFT_TYPE_MAPPING.get(str(constraint.times), str(constraint.times))
                    if shift_type not in self.SHIFT_TYPES:
                        logger.warning(f"無効な勤務区分: {constraint.times}")
                        continue

                # 「土／日」の特殊処理
                is_weekend = constraint.target == "土／日"
                
                if is_weekend:
                    # 土曜日(5)と日曜日(6)のペアを検出
                    weekend_pairs = []
                    
                    # 曜日配列を生成（0=月曜日, 1=火曜日, ..., 6=日曜日）
                    weekday_array = [datetime(self.year, self.month, d+1).weekday() for d in range(self.days_in_month)]
                    
                    # 土曜日（5）の位置を全て取得
                    saturday_indexes = [i for i, wd in enumerate(weekday_array) if wd == 5]
                    
                    # 各土曜日に対応する日曜日（翌日）が月内にあるかチェック
                    for sat_idx in saturday_indexes:
                        sun_idx = sat_idx + 1
                        if sun_idx < self.days_in_month and weekday_array[sun_idx] == 6:
                            weekend_pairs.append((sat_idx, sun_idx))
                    
                    # 「第N」または「全て」の処理
                    target_pairs = []
                    if constraint.count == "全て":
                        # すべてのペアを対象にする
                        target_pairs = weekend_pairs
                    else:
                        # 「第一」「第二」など、特定の週を対象にする
                        try:
                            nth = ["第一", "第二", "第三", "第四", "第五"].index(constraint.count)
                            if nth < len(weekend_pairs):
                                target_pairs = [weekend_pairs[nth]]
                        except (ValueError, IndexError):
                            logger.warning(f"無効な週指定: {constraint.count}")
                            continue
                    
                    if not target_pairs:
                        logger.warning(f"対象の土／日ペアが見つかりません: {constraint.count}{constraint.target}")
                        continue
                    
                    # 好き/嫌いの処理
                    is_aversion = (constraint.sub_category == "嫌悪")
                    
                    if constraint.type == "必須":
                        # 各ペアに対して処理
                        for sat_idx, sun_idx in target_pairs:
                            if is_working_shift:
                                # 出勤シフト（早番/日勤/遅番）の特別処理
                                if is_aversion:
                                    # 嫌悪：少なくとも1日は出勤しない（両方出勤はNG）
                                    c = self.model.Add(
                                        self.shifts[(staff.name, sat_idx, "▲")] + self.shifts[(staff.name, sat_idx, "日")] + self.shifts[(staff.name, sat_idx, "▼")] +
                                        self.shifts[(staff.name, sun_idx, "▲")] + self.shifts[(staff.name, sun_idx, "日")] + self.shifts[(staff.name, sun_idx, "▼")] <= 5
                                    )
                                    c.WithName(f"【曜日希望_必須】{staff.name}: {sat_idx+1}日(土)or{sun_idx+1}日(日)は出勤しない")
                                else:
                                    # 愛好：少なくとも1日は出勤する
                                    c = self.model.Add(
                                        self.shifts[(staff.name, sat_idx, "▲")] + self.shifts[(staff.name, sat_idx, "日")] + self.shifts[(staff.name, sat_idx, "▼")] +
                                        self.shifts[(staff.name, sun_idx, "▲")] + self.shifts[(staff.name, sun_idx, "日")] + self.shifts[(staff.name, sun_idx, "▼")] >= 1
                                    )
                                    c.WithName(f"【曜日希望_必須】{staff.name}: {sat_idx+1}日(土)or{sun_idx+1}日(日)は出勤")
                            else:
                                # 通常シフト（指定されたタイプ）
                                if is_aversion:
                                    # 嫌悪：少なくとも1日は指定シフトでない（両方指定シフトはNG）
                                    c = self.model.Add(
                                        self.shifts[(staff.name, sat_idx, shift_type)] + 
                                        self.shifts[(staff.name, sun_idx, shift_type)] <= 1
                                    )
                                    c.WithName(f"【曜日希望_必須】{staff.name}: {sat_idx+1}日(土)or{sun_idx+1}日(日)は{shift_type}にしない")
                                else:
                                    # 愛好：少なくとも1日は指定シフトになる
                                    c = self.model.Add(
                                        self.shifts[(staff.name, sat_idx, shift_type)] + 
                                        self.shifts[(staff.name, sun_idx, shift_type)] >= 1
                                    )
                                    c.WithName(f"【曜日希望_必須】{staff.name}: {sat_idx+1}日(土)or{sun_idx+1}日(日)は{shift_type}")
                    else:
                        # 選好制約
                        weight = self.constraint_weights["選好"]["曜日希望"]
                        
                        # 各ペアに対して処理
                        for sat_idx, sun_idx in target_pairs:
                            if is_working_shift:
                                # 出勤シフト（早番/日勤/遅番）の特別処理
                                if is_aversion:
                                    # 嫌悪：両方出勤の場合にペナルティ
                                    both_working = self.model.NewBoolVar(f'both_working_{staff.name}_{sat_idx}_{sun_idx}')
                                    
                                    # 土曜日が出勤か
                                    sat_working = self.model.NewBoolVar(f'sat_working_{staff.name}_{sat_idx}')
                                    self.model.Add(
                                        self.shifts[(staff.name, sat_idx, "▲")] + 
                                        self.shifts[(staff.name, sat_idx, "日")] + 
                                        self.shifts[(staff.name, sat_idx, "▼")] >= 1
                                    ).OnlyEnforceIf(sat_working)
                                    self.model.Add(
                                        self.shifts[(staff.name, sat_idx, "▲")] + 
                                        self.shifts[(staff.name, sat_idx, "日")] + 
                                        self.shifts[(staff.name, sat_idx, "▼")] == 0
                                    ).OnlyEnforceIf(sat_working.Not())
                                    
                                    # 日曜日が出勤か
                                    sun_working = self.model.NewBoolVar(f'sun_working_{staff.name}_{sun_idx}')
                                    self.model.Add(
                                        self.shifts[(staff.name, sun_idx, "▲")] + 
                                        self.shifts[(staff.name, sun_idx, "日")] + 
                                        self.shifts[(staff.name, sun_idx, "▼")] >= 1
                                    ).OnlyEnforceIf(sun_working)
                                    self.model.Add(
                                        self.shifts[(staff.name, sun_idx, "▲")] + 
                                        self.shifts[(staff.name, sun_idx, "日")] + 
                                        self.shifts[(staff.name, sun_idx, "▼")] == 0
                                    ).OnlyEnforceIf(sun_working.Not())
                                    
                                    # 両方出勤の場合
                                    self.model.AddBoolAnd([sat_working, sun_working]).OnlyEnforceIf(both_working)
                                    self.model.AddBoolOr([sat_working.Not(), sun_working.Not()]).OnlyEnforceIf(both_working.Not())
                                    
                                    # ペナルティを加算
                                    self.objective_terms.append(-both_working * weight)
                                else:
                                    # 愛好：少なくとも1日出勤で報酬
                                    any_working = self.model.NewBoolVar(f'any_working_{staff.name}_{sat_idx}_{sun_idx}')
                                    
                                    # 土曜日か日曜日のいずれかが出勤
                                    self.model.Add(
                                        self.shifts[(staff.name, sat_idx, "▲")] + self.shifts[(staff.name, sat_idx, "日")] + self.shifts[(staff.name, sat_idx, "▼")] +
                                        self.shifts[(staff.name, sun_idx, "▲")] + self.shifts[(staff.name, sun_idx, "日")] + self.shifts[(staff.name, sun_idx, "▼")] >= 1
                                    ).OnlyEnforceIf(any_working)
                                    self.model.Add(
                                        self.shifts[(staff.name, sat_idx, "▲")] + self.shifts[(staff.name, sat_idx, "日")] + self.shifts[(staff.name, sat_idx, "▼")] +
                                        self.shifts[(staff.name, sun_idx, "▲")] + self.shifts[(staff.name, sun_idx, "日")] + self.shifts[(staff.name, sun_idx, "▼")] == 0
                                    ).OnlyEnforceIf(any_working.Not())
                                    
                                    # 報酬を加算
                                    self.objective_terms.append(any_working * weight)
                            else:
                                # 通常シフト（指定されたタイプ）
                                if is_aversion:
                                    # 嫌悪：両方指定シフトの場合にペナルティ
                                    both_shifts = self.model.NewBoolVar(f'both_{shift_type}_{staff.name}_{sat_idx}_{sun_idx}')
                                    self.model.Add(
                                        self.shifts[(staff.name, sat_idx, shift_type)] + 
                                        self.shifts[(staff.name, sun_idx, shift_type)] == 2
                                    ).OnlyEnforceIf(both_shifts)
                                    self.model.Add(
                                        self.shifts[(staff.name, sat_idx, shift_type)] + 
                                        self.shifts[(staff.name, sun_idx, shift_type)] <= 1
                                    ).OnlyEnforceIf(both_shifts.Not())
                                    
                                    # ペナルティを加算
                                    self.objective_terms.append(-both_shifts * weight)
                                else:
                                    # 愛好：少なくとも1日指定シフトで報酬
                                    any_shift = self.model.NewBoolVar(f'any_{shift_type}_{staff.name}_{sat_idx}_{sun_idx}')
                                    self.model.Add(
                                        self.shifts[(staff.name, sat_idx, shift_type)] + 
                                        self.shifts[(staff.name, sun_idx, shift_type)] >= 1
                                    ).OnlyEnforceIf(any_shift)
                                    self.model.Add(
                                        self.shifts[(staff.name, sat_idx, shift_type)] + 
                                        self.shifts[(staff.name, sun_idx, shift_type)] == 0
                                    ).OnlyEnforceIf(any_shift.Not())
                                    
                                    # 報酬を加算
                                    self.objective_terms.append(any_shift * weight)
                else:
                    # 通常の曜日処理
                    # 曜日の取得（"月曜日"→0, "火曜日"→1, ...）
                    weekday = "月火水木金土日".index(constraint.target.replace("曜日", ""))
                    
                    # 対象日の特定（weekday()の値をそのまま使用）
                    weekday_array = [datetime(self.year, self.month, d+1).weekday() for d in range(self.days_in_month)]
                    target_days = []

                    if constraint.count == "全て":
                        # その曜日の全ての日を対象とする
                        target_days = [day for day in range(self.days_in_month) if weekday_array[day] == weekday]
                    else:
                        # "第N"の数値を取得（"第一"→0, "第二"→1, ...）
                        if not constraint.count:
                            continue
                        # 文字列を配列に分割して処理
                        nth = ["第一", "第二", "第三", "第四", "第五"].index(constraint.count)
                        
                        # その曜日が出現する日付を順番に収集
                        weekday_occurrences = [
                            day + 1 for day in range(self.days_in_month)
                            if datetime(self.year, self.month, day+1).weekday() == weekday
                        ]
                        
                        # 指定された順番の曜日を追加（nth=0が第一に対応）
                        if nth < len(weekday_occurrences):
                            target_days = [weekday_occurrences[nth] - 1]  # 0-basedに戻す

                    if not target_days:
                        logger.warning(
                            f"対象の曜日が見つかりません: {constraint.count}{constraint.target}"
                            f"（{self.year}年{self.month}月）"
                        )
                        continue

                    # 好き/嫌いの処理
                    is_aversion = (constraint.sub_category == "嫌悪")
                    
                    if constraint.type == "必須":
                        for day in target_days:
                            if is_working_shift:  # 出勤の特別処理
                                if is_aversion:
                                    # 嫌悪：早番/日勤/遅番のいずれも入れない
                                    c = self.model.Add(
                                        self.shifts[(staff.name, day, "▲")] + 
                                        self.shifts[(staff.name, day, "日")] + 
                                        self.shifts[(staff.name, day, "▼")] == 0
                                    )
                                    c.WithName(f"【曜日希望_必須】{staff.name}: {day+1}日は出勤を避ける")
                                else:
                                    # 愛好：早番/日勤/遅番のいずれかが入る
                                    c = self.model.Add(
                                        self.shifts[(staff.name, day, "▲")] + 
                                        self.shifts[(staff.name, day, "日")] + 
                                        self.shifts[(staff.name, day, "▼")] >= 1
                                    )
                                    c.WithName(f"【曜日希望_必須】{staff.name}: {day+1}日は出勤")
                            else:  # 通常の処理
                                if is_aversion:
                                    c = self.model.Add(self.shifts[(staff.name, day, shift_type)] == 0)
                                    c.WithName(f"【曜日希望_必須】{staff.name}: {day+1}日は{shift_type}を避ける")
                                else:
                                    c = self.model.Add(self.shifts[(staff.name, day, shift_type)] == 1)
                                    c.WithName(f"【曜日希望_必須】{staff.name}: {day+1}日は{shift_type}")
                    else:  # 選好制約
                        weight = self.constraint_weights["選好"]["曜日希望"]
                        for day in target_days:
                            if is_working_shift:  # 出勤の特別処理
                                if is_aversion:
                                    # 嫌悪：早番/日勤/遅番のいずれかが入るとペナルティ
                                    is_working = self.model.NewBoolVar(f'is_working_{staff.name}_{day}')
                                    self.model.Add(
                                        self.shifts[(staff.name, day, "▲")] + 
                                        self.shifts[(staff.name, day, "日")] + 
                                        self.shifts[(staff.name, day, "▼")] >= 1
                                    ).OnlyEnforceIf(is_working)
                                    self.model.Add(
                                        self.shifts[(staff.name, day, "▲")] + 
                                        self.shifts[(staff.name, day, "日")] + 
                                        self.shifts[(staff.name, day, "▼")] == 0
                                    ).OnlyEnforceIf(is_working.Not())
                                    self.objective_terms.append(-is_working * weight)
                                else:
                                    # 愛好：早番/日勤/遅番のいずれかが入ると報酬
                                    is_working = self.model.NewBoolVar(f'is_working_{staff.name}_{day}')
                                    self.model.Add(
                                        self.shifts[(staff.name, day, "▲")] + 
                                        self.shifts[(staff.name, day, "日")] + 
                                        self.shifts[(staff.name, day, "▼")] >= 1
                                    ).OnlyEnforceIf(is_working)
                                    self.model.Add(
                                        self.shifts[(staff.name, day, "▲")] + 
                                        self.shifts[(staff.name, day, "日")] + 
                                        self.shifts[(staff.name, day, "▼")] == 0
                                    ).OnlyEnforceIf(is_working.Not())
                                    self.objective_terms.append(is_working * weight)
                            else:  # 通常の処理
                                if is_aversion:
                                    self.objective_terms.append(
                                        -self.shifts[(staff.name, day, shift_type)] * weight
                                    )
                                else:
                                    self.objective_terms.append(
                                        self.shifts[(staff.name, day, shift_type)] * weight
                                    )

        logger.debug("曜日希望制約の設定完了")

    def add_local_shift_pattern_constraint(self):
        """個人に適用されるシフトパターン制約（愛好/嫌悪）"""
        logger.debug("=== ローカルシフトパターン制約の設定 ===")

        for staff in self.staff_data_list:
            for constraint in staff.constraints:
                if constraint.category != "シフトパターン":
                    continue

                from_shift = self.SHIFT_TYPE_MAPPING.get(str(constraint.count), str(constraint.count))
                to_shift = self.SHIFT_TYPE_MAPPING.get(str(constraint.target), str(constraint.target))
                
                self._calculate_shift_pattern_constraint(
                    staff_name=staff.name,
                    from_shift=from_shift,
                    to_shift=to_shift,
                    constraint_type=constraint.type,
                    sub_category=constraint.sub_category,
                    shift_counts=staff.shift_counts,
                    is_local=True
                )

        logger.debug("ローカルシフトパターン制約の設定完了")

    def _calculate_shift_pattern_constraint(
        self,
        staff_name: str,
        from_shift: str,
        to_shift: str,
        constraint_type: str,
        sub_category: str,
        shift_counts: dict,
        is_local: bool,
        weight: int = 0
    ):
        """シフトパターン制約の計算処理"""
        if constraint_type == "必須":
            if sub_category in ["愛好", "推奨"]:
                # from_shiftの次の日は必ずto_shiftにする
                for day in range(self.days_in_month - 1):
                    c = self.model.Add(
                        self.shifts[(staff_name, day + 1, to_shift)] == 1
                    ).OnlyEnforceIf(self.shifts[(staff_name, day, from_shift)])
                    c.WithName(
                        f"【シフトパターン_必須愛好/推奨】{staff_name}:{day+1}日の"
                        f"{from_shift}→{day+2}日の{to_shift}を強制"
                    )
            
            else:  # 嫌悪/回避
                # from_shiftの次の日は絶対にto_shiftにしない
                for day in range(self.days_in_month - 1):
                    c = self.model.Add(
                        self.shifts[(staff_name, day + 1, to_shift)] == 0
                    ).OnlyEnforceIf(self.shifts[(staff_name, day, from_shift)])
                    c.WithName(
                        f"【シフトパターン_必須嫌悪/回避】{staff_name}:{day+1}日の"
                        f"{from_shift}→{day+2}日の{to_shift}を禁止"
                    )
        
        else:  # 選好制約の場合（既存の処理）
            if is_local:
                weight = self.constraint_weights["選好"]["シフトパターン"]
                multiplier = 1 if sub_category == "愛好" else -1
            else:
                multiplier = 1 if sub_category == "推奨" else -1
            
            for day in range(self.days_in_month - 1):
                is_pattern = self.model.NewBoolVar(
                    f'pattern_{staff_name}_{day}_{from_shift}_{to_shift}'
                )
                
                # パターン成立の条件
                self.model.Add(
                    self.shifts[(staff_name, day, from_shift)] == 1
                ).OnlyEnforceIf(is_pattern)
                self.model.Add(
                    self.shifts[(staff_name, day + 1, to_shift)] == 1
                ).OnlyEnforceIf(is_pattern)
                
                # パターン不成立の条件
                self.model.AddBoolOr([
                    self.shifts[(staff_name, day, from_shift)].Not(),
                    self.shifts[(staff_name, day + 1, to_shift)].Not()
                ]).OnlyEnforceIf(is_pattern.Not())
                
                # 目的関数に追加
                self.objective_terms.append(is_pattern * weight * multiplier)

    def add_global_shift_pattern_constraint(self):
        """全体に適用されるシフトパターン制約（推奨/回避）"""
        logger.debug("=== グローバルシフトパターン制約の設定 ===")

        # rule_dataからシフトパターン制約を取得して処理
        for constraint in self.rule_data.preference_constraints:
            if constraint.category != "シフトパターン":
                continue

            from_shift = self.SHIFT_TYPE_MAPPING.get(str(constraint.count), str(constraint.count))
            to_shift = self.SHIFT_TYPE_MAPPING.get(str(constraint.target), str(constraint.target))
            
            # グローバルルール除外でないスタッフに適用
            for staff in self.staff_data_list:
                if not staff.is_global_rule:
                    self._calculate_shift_pattern_constraint(
                        staff_name=staff.name,
                        from_shift=from_shift,
                        to_shift=to_shift,
                        constraint_type=constraint.type,
                        sub_category=constraint.sub_category,  # "推奨" or "回避"
                        shift_counts=staff.shift_counts,
                        is_local=False,
                        weight=constraint.weight  # rule_dataから直接重みを取得
                    )

        logger.debug("グローバルシフトパターン制約の設定完了")
    
    def add_shift_balance_constraints(self):
        """早番と遅番のバランスに関する制約を追加する"""
        logger.debug("=== シフトバランス制約の設定 ===")

        # シフトバランス制約を取得
        balance_constraints = [
            constraint for constraint in self.rule_data.preference_constraints
            if constraint.category == "シフトバランス"
        ]
        
        if not balance_constraints:
            return
            
        for constraint in balance_constraints:
            weight = constraint.weight
            balance_type = constraint.target
            
            for staff in self.staff_list:
                # 月間の早番・遅番回数を計算する変数
                early_total = self.model.NewIntVar(0, self.days_in_month, f'early_total_{staff}')
                late_total = self.model.NewIntVar(0, self.days_in_month, f'late_total_{staff}')
                
                # 早番・遅番の合計を計算
                self.model.Add(early_total == sum(self.shifts[(staff, day, '▲')] 
                                        for day in range(self.days_in_month)))
                self.model.Add(late_total == sum(self.shifts[(staff, day, '▼')] 
                                        for day in range(self.days_in_month)))
                
                if balance_type == "丁度":
                    # 完全一致の変数（達成時にプラスの重みを加算）
                    balance_var = self.model.NewBoolVar(f'exact_balance_{staff}')
                    self.model.Add(early_total == late_total).OnlyEnforceIf(balance_var)
                    self.model.Add(early_total != late_total).OnlyEnforceIf(balance_var.Not())
                    self.objective_terms.append(balance_var * weight)  # Not()を削除
                    
                elif balance_type == "±1":
                    # ±1以内の変数（達成時にプラスの重みを加算）
                    balance_var = self.model.NewBoolVar(f'close_balance_{staff}')
                    # 早番が遅番より1多い、または遅番が早番より1多い
                    early_more = self.model.NewBoolVar(f'early_more_{staff}')
                    late_more = self.model.NewBoolVar(f'late_more_{staff}')
                    
                    self.model.Add(early_total == late_total + 1).OnlyEnforceIf(early_more)
                    self.model.Add(late_total == early_total + 1).OnlyEnforceIf(late_more)
                    self.model.AddBoolOr([early_more, late_more]).OnlyEnforceIf(balance_var)
                    self.model.Add(early_total != late_total).OnlyEnforceIf(balance_var)
                    
                    self.objective_terms.append(balance_var * weight)
                    
                elif balance_type == "早＋1":
                    # 早番が遅番より1多い（達成時にプラスの重みを加算）
                    balance_var = self.model.NewBoolVar(f'early_plus_one_{staff}')
                    self.model.Add(early_total == late_total + 1).OnlyEnforceIf(balance_var)
                    self.model.Add(early_total != late_total + 1).OnlyEnforceIf(balance_var.Not())
                    self.objective_terms.append(balance_var * weight)  # Not()を削除
                    
                elif balance_type == "遅＋1":
                    # 遅番が早番より1多い（達成時にプラスの重みを加算）
                    balance_var = self.model.NewBoolVar(f'late_plus_one_{staff}')
                    self.model.Add(late_total == early_total + 1).OnlyEnforceIf(balance_var)
                    self.model.Add(late_total != early_total + 1).OnlyEnforceIf(balance_var.Not())
                    self.objective_terms.append(balance_var * weight)  # Not()を削除

        logger.debug("シフトバランス制約の設定完了")

    def add_pair_overlap_constraints(self):
        """ペア重複制約を追加する（全て回避）
        
        特定のシフトタイプにおいて、スタッフ2人が同じ日に組まれる回数を制限する。
        グローバルルール対象外（is_global_rule = False）のスタッフのみに適用される。
        """
        logger.debug("=== ペア重複制約の設定開始 ===")
        
        # グローバルルール対象外のスタッフのみを抽出
        target_staff = [
            staff.name for staff in self.staff_data_list 
            if not staff.is_global_rule  # Falseの場合に含める
        ]
        
        # デバッグ情報を追加
        logger.debug(f"全スタッフ数: {len(self.staff_data_list)}")
        logger.debug(f"制約対象スタッフ数: {len(target_staff)}")
        logger.debug(f"制約対象スタッフ: {target_staff}")
        
        if len(target_staff) < 2:
            logger.debug("制約対象のスタッフが2人未満です")
            return

        # ペア重複制約を取得
        pair_constraints = [
            constraint for constraint in self.rule_data.preference_constraints
            if constraint.category == "ペア重複"
        ]
        
        if not pair_constraints:
            return

        # グローバルルール対象のスタッフのみを抽出
        global_staff = [
            staff.name for staff in self.staff_data_list 
            if staff.is_global_rule
        ]

        for constraint in pair_constraints:
            # 1. シフトタイプの変換が必要
            shift_type = SHIFT_TYPE_MAPPING.get(str(constraint.count), str(constraint.count))
            target_count = KANJI_TO_NUMBER.get(str(constraint.final), 0)
            
            # 各ペアについての変数を保持
            pair_day_vars = {}
            
            # 2. ここで重要なバグ: 全日程のペア検出変数を作成
            for staff1_idx in range(len(target_staff)):
                for staff2_idx in range(staff1_idx + 1, len(target_staff)):
                    staff1 = target_staff[staff1_idx]
                    staff2 = target_staff[staff2_idx]
                    
                    # 3. 各日のペア検出変数を作成
                    day_vars = []
                    for day in range(self.days_in_month):
                        is_pair = self.model.NewBoolVar(f'is_pair_{staff1}_{staff2}_{day}_{shift_type}')
                        
                        # 4. ここが間違い: 条件が逆
                        # 現在のコード
                        self.model.Add(
                            self.shifts[(staff1, day, shift_type)] + 
                            self.shifts[(staff2, day, shift_type)] == 2
                        ).OnlyEnforceIf(is_pair)
                        
                        # 修正後のコード
                        self.model.Add(
                            self.shifts[(staff1, day, shift_type)] + 
                            self.shifts[(staff2, day, shift_type)] >= 2
                        ).OnlyEnforceIf(is_pair)
                        
                        self.model.Add(
                            self.shifts[(staff1, day, shift_type)] + 
                            self.shifts[(staff2, day, shift_type)] < 2
                        ).OnlyEnforceIf(is_pair.Not())
                        
                        day_vars.append(is_pair)
                    
                    pair_day_vars[(staff1, staff2)] = day_vars

            # 5. ペアの回数制限も逆になっている
            for (staff1, staff2), day_vars in pair_day_vars.items():
                count_var = self.model.NewIntVar(0, self.days_in_month, f'pair_count_{staff1}_{staff2}')
                self.model.Add(count_var == sum(day_vars))
                
                if constraint.target == "以上":
                    if constraint.type == "必須":
                        # 修正: 指定回数以上のペアを禁止
                        c = self.model.Add(count_var < target_count)
                        c.WithName(f"【ペア重複_必須】{staff1}-{staff2}: {target_count}回以上を禁止")
                    else:
                        # 修正: 指定回数以上のペアにペナルティ
                        is_over = self.model.NewBoolVar(f'is_over_{staff1}_{staff2}')
                        self.model.Add(count_var >= target_count).OnlyEnforceIf(is_over)
                        self.model.Add(count_var < target_count).OnlyEnforceIf(is_over.Not())
                        self.objective_terms.append(is_over * -constraint.weight)
                        logger.debug(f"{staff1}-{staff2}のペア重複（{target_count}回以上）にペナルティ {constraint.weight}")
                else:  # "丁度"
                    if constraint.type == "必須":
                        # 修正: 指定回数と一致する場合を禁止
                        c = self.model.Add(count_var != target_count)
                        c.WithName(f"【ペア重複_必須】{staff1}-{staff2}: {target_count}回丁度を禁止")
                    else:
                        # 修正: 指定回数と一致する場合にペナルティ
                        is_exact = self.model.NewBoolVar(f'is_exact_{staff1}_{staff2}')
                        self.model.Add(count_var == target_count).OnlyEnforceIf(is_exact)
                        self.model.Add(count_var != target_count).OnlyEnforceIf(is_exact.Not())
                        self.objective_terms.append(is_exact * -constraint.weight)
                        logger.debug(f"{staff1}-{staff2}のペア重複（{target_count}回丁度）にペナルティ {constraint.weight}")

            logger.debug("ペア重複制約の設定完了")

    def add_custom_preset_constraint(self):
        """カスタムプリセット制約を設定"""
        logger.debug("=== カスタムプリセット制約の処理開始 ===")

        for staff_data in self.staff_data_list:
            for constraint in staff_data.constraints:
                if constraint.category == "カスタムプリセット":
                    target_staff = constraint.sub_category
                    if target_staff in [s.name for s in self.staff_data_list]:
                        if constraint.target == "早＋早と入＋入を回避":
                            # 早番同士を避ける
                            for day in range(self.days_in_month):
                                self.model.Add(self.shifts[(staff_data.name, day, "▲")] + 
                                             self.shifts[(target_staff, day, "▲")] <= 1)
                                # 夜勤同士を避ける
                                self.model.Add(self.shifts[(staff_data.name, day, "／")] + 
                                             self.shifts[(target_staff, day, "／")] <= 1)

                        elif constraint.target == "早日遅＋早日遅と夜＋夜を回避":
                            for day in range(self.days_in_month):
                                # 違反パターンを検出する変数
                                violation = self.model.NewBoolVar(f'edl_violation_{staff_data.name}_{target_staff}_{day}')
                                
                                # staff_nameが早日遅のいずれかを持っているか
                                staff_has_edl = self.model.NewBoolVar(f'staff_has_edl_{staff_data.name}_{day}')
                                self.model.Add(
                                    self.shifts[(staff_data.name, day, "▲")] + 
                                    self.shifts[(staff_data.name, day, "日")] + 
                                    self.shifts[(staff_data.name, day, "▼")] >= 1
                                ).OnlyEnforceIf(staff_has_edl)
                                self.model.Add(
                                    self.shifts[(staff_data.name, day, "▲")] + 
                                    self.shifts[(staff_data.name, day, "日")] + 
                                    self.shifts[(staff_data.name, day, "▼")] == 0
                                ).OnlyEnforceIf(staff_has_edl.Not())
                                
                                # target_staffが早日遅のいずれかを持っているか
                                target_has_edl = self.model.NewBoolVar(f'target_has_edl_{target_staff}_{day}')
                                self.model.Add(
                                    self.shifts[(target_staff, day, "▲")] + 
                                    self.shifts[(target_staff, day, "日")] + 
                                    self.shifts[(target_staff, day, "▼")] >= 1
                                ).OnlyEnforceIf(target_has_edl)
                                self.model.Add(
                                    self.shifts[(target_staff, day, "▲")] + 
                                    self.shifts[(target_staff, day, "日")] + 
                                    self.shifts[(target_staff, day, "▼")] == 0
                                ).OnlyEnforceIf(target_has_edl.Not())
                                
                                # 両方が早日遅を持っている場合に違反
                                self.model.Add(violation == 1).OnlyEnforceIf([staff_has_edl, target_has_edl])
                                self.model.Add(violation == 0).OnlyEnforceIf([staff_has_edl.Not()])
                                self.model.Add(violation == 0).OnlyEnforceIf([target_has_edl.Not()])
                                
                                # 夜勤同士を避ける制約を追加
                                night_violation = self.model.NewBoolVar(f'night_violation_{staff_data.name}_{target_staff}_{day}')
                                self.model.Add(night_violation == 1).OnlyEnforceIf([self.shifts[(staff_data.name, day, "／")], self.shifts[(target_staff, day, "／")]])
                                self.model.Add(night_violation == 0).OnlyEnforceIf([self.shifts[(staff_data.name, day, "／")].Not()])
                                self.model.Add(night_violation == 0).OnlyEnforceIf([self.shifts[(target_staff, day, "／")].Not()])

                                if constraint.type == "必須":
                                    self.model.Add(violation == 0)  # 早日遅の制約
                                    self.model.Add(night_violation == 0)  # 夜勤の制約
                                else:
                                    # 選好制約の場合はペナルティを設定
                                    self.objective_terms.append(violation * -self.constraint_weights["選好"]["カスタムプリセット"])
                                    self.objective_terms.append(night_violation * -self.constraint_weights["選好"]["カスタムプリセット"])

                        elif constraint.target == "早＋明と遅＋入を推奨":
                            for day in range(self.days_in_month):
                                if constraint.type == "必須":
                                    # 夜勤明けの人がいる場合、相手は早番でなければならない
                                    self.model.Add(self.shifts[(staff_data.name, day, "▲")] == 1).OnlyEnforceIf(self.shifts[(target_staff, day, "×")])
                                    self.model.Add(self.shifts[(target_staff, day, "▲")] == 1).OnlyEnforceIf(self.shifts[(staff_data.name, day, "×")])
                                    
                                    # 夜勤の人がいる場合、相手は遅番でなければならない
                                    self.model.Add(self.shifts[(staff_data.name, day, "▼")] == 1).OnlyEnforceIf(self.shifts[(target_staff, day, "／")])
                                    self.model.Add(self.shifts[(target_staff, day, "▼")] == 1).OnlyEnforceIf(self.shifts[(staff_data.name, day, "／")])
                                else:
                                    # 選好制約の場合
                                    early_next = self.model.NewBoolVar(f'early_next_{staff_data.name}_{target_staff}_{day}')
                                    self.model.Add(early_next == 1).OnlyEnforceIf([self.shifts[(staff_data.name, day, "▲")], self.shifts[(target_staff, day, "×")]])
                                    self.model.Add(early_next == 0).OnlyEnforceIf([self.shifts[(staff_data.name, day, "▲")].Not()])
                                    self.model.Add(early_next == 0).OnlyEnforceIf([self.shifts[(target_staff, day, "×")].Not()])

                                    late_night = self.model.NewBoolVar(f'late_night_{staff_data.name}_{target_staff}_{day}')
                                    self.model.Add(late_night == 1).OnlyEnforceIf([self.shifts[(staff_data.name, day, "▼")], self.shifts[(target_staff, day, "／")]])
                                    self.model.Add(late_night == 0).OnlyEnforceIf([self.shifts[(staff_data.name, day, "▼")].Not()])
                                    self.model.Add(late_night == 0).OnlyEnforceIf([self.shifts[(target_staff, day, "／")].Not()])

                                    target_early_next = self.model.NewBoolVar(f'target_early_next_{staff_data.name}_{target_staff}_{day}')
                                    self.model.Add(target_early_next == 1).OnlyEnforceIf([self.shifts[(target_staff, day, "▲")], self.shifts[(staff_data.name, day, "×")]])
                                    self.model.Add(target_early_next == 0).OnlyEnforceIf([self.shifts[(target_staff, day, "▲")].Not()])
                                    self.model.Add(target_early_next == 0).OnlyEnforceIf([self.shifts[(staff_data.name, day, "×")].Not()])

                                    target_late_night = self.model.NewBoolVar(f'target_late_night_{staff_data.name}_{target_staff}_{day}')
                                    self.model.Add(target_late_night == 1).OnlyEnforceIf([self.shifts[(target_staff, day, "▼")], self.shifts[(staff_data.name, day, "／")]])
                                    self.model.Add(target_late_night == 0).OnlyEnforceIf([self.shifts[(target_staff, day, "▼")].Not()])
                                    self.model.Add(target_late_night == 0).OnlyEnforceIf([self.shifts[(staff_data.name, day, "／")].Not()])

                                    self.objective_terms.append((early_next + late_night + target_early_next + target_late_night) * 
                                                             self.constraint_weights["選好"]["カスタムプリセット"])

                        elif constraint.target == "早日＋明と日遅＋入を回避":
                            for day in range(self.days_in_month):
                                if constraint.type == "必須":
                                    # 引き継ぎ時に顔を合わせる組み合わせを禁止
                                    self.model.Add(self.shifts[(staff_data.name, day, "×")] + self.shifts[(target_staff, day, "▲")] <= 1)  # 夜勤明け＋早番
                                    self.model.Add(self.shifts[(staff_data.name, day, "×")] + self.shifts[(target_staff, day, "日")] <= 1)  # 夜勤明け＋日勤
                                    self.model.Add(self.shifts[(staff_data.name, day, "／")] + self.shifts[(target_staff, day, "▼")] <= 1)  # 夜勤＋遅番
                                    self.model.Add(self.shifts[(staff_data.name, day, "／")] + self.shifts[(target_staff, day, "日")] <= 1)  # 夜勤＋日勤
                                    self.model.Add(self.shifts[(staff_data.name, day, "日")] + self.shifts[(target_staff, day, "／")] <= 1)  # 日勤＋夜勤
                                    self.model.Add(self.shifts[(staff_data.name, day, "日")] + self.shifts[(target_staff, day, "×")] <= 1)  # 日勤＋夜勤明け
                                    # 逆方向も同様
                                    self.model.Add(self.shifts[(target_staff, day, "×")] + self.shifts[(staff_data.name, day, "▲")] <= 1)
                                    self.model.Add(self.shifts[(target_staff, day, "×")] + self.shifts[(staff_data.name, day, "日")] <= 1)
                                    self.model.Add(self.shifts[(target_staff, day, "／")] + self.shifts[(staff_data.name, day, "▼")] <= 1)
                                    self.model.Add(self.shifts[(target_staff, day, "／")] + self.shifts[(staff_data.name, day, "日")] <= 1)
                                    self.model.Add(self.shifts[(target_staff, day, "日")] + self.shifts[(staff_data.name, day, "／")] <= 1)
                                    self.model.Add(self.shifts[(target_staff, day, "日")] + self.shifts[(staff_data.name, day, "×")] <= 1)
                                else:
                                    # 引き継ぎ時の顔合わせにペナルティを設定
                                    for s1, s2 in [("×","▲"), ("×","日"), ("▲","×"), ("日","×"), 
                                                 ("／","▼"), ("／","日"), ("▼","／"), ("日","／"),
                                                 ("日","×"), ("×","日"), ("日","／"), ("／","日")]:  # 日勤関連を追加
                                        match = self.model.NewBoolVar(f'match_{staff_data.name}_{target_staff}_{day}_{s1}_{s2}')
                                        self.model.Add(match == 1).OnlyEnforceIf([self.shifts[(staff_data.name, day, s1)], self.shifts[(target_staff, day, s2)]])
                                        self.model.Add(match == 0).OnlyEnforceIf([self.shifts[(staff_data.name, day, s1)].Not()])
                                        self.model.Add(match == 0).OnlyEnforceIf([self.shifts[(target_staff, day, s2)].Not()])
                                        self.objective_terms.append(match * -self.constraint_weights["選好"]["カスタムプリセット"])

    logger.debug("=== カスタムプリセット制約の処理完了 ===")

    def calculate_holiday_guarantee(self, staff_name: str, n_days: int):
        """連休の計算ロジックを実装する共通メソッド"""
        logger.debug(f"=== {staff_name}の{n_days}連休を計算 ===")
        
        consecutive_holidays = []
        
        # 希望シフトでの連休をカウント（2連休以上をカウント）
        fixed_holiday_count = 0
        fixed_days = set()
        for entry in self.shift_data.entries:
            if entry.staff_name == staff_name and entry.shift_type == "公":
                fixed_days.add(entry.day - 1)  # 0-basedに変換
        
        # 連続した日付をグループ化して連休を数える
        current_sequence = []
        for day in range(self.days_in_month):
            if day in fixed_days:
                current_sequence.append(day)
            else:
                if len(current_sequence) >= n_days:
                    fixed_holiday_count += 1
                current_sequence = []
        # 最後の連続をチェック
        if len(current_sequence) >= n_days:
            fixed_holiday_count += 1
        
        for day in range(self.days_in_month - n_days + 1):
            is_holiday_start = self.model.NewBoolVar(f'{staff_name}_holiday_start_{day}_{n_days}')
            
            # この日が希望シフトの連休に含まれているかチェック
            is_fixed_sequence = all(d in fixed_days for d in range(day, day + n_days))
            
            if is_fixed_sequence:
                # 希望シフトの連休は無視（別にカウント済み）
                self.model.Add(is_holiday_start == 0)
                continue
            
            # n_days分の連続した公休があることを示す制約
            holiday_sequence = []
            for offset in range(n_days):
                holiday_sequence.append(self.shifts[(staff_name, day + offset, '公')])
            
            # n_days+1日目が公休でないことを確認（ちょうどn_days連休）
            if day + n_days < self.days_in_month:
                next_day = self.shifts[(staff_name, day + n_days, '公')]
                is_exact_length = self.model.NewBoolVar(f'{staff_name}_exact_length_{day}_{n_days}')
                self.model.Add(next_day == 0).OnlyEnforceIf(is_exact_length)
                self.model.Add(next_day == 1).OnlyEnforceIf(is_exact_length.Not())
            else:
                is_exact_length = self.model.NewConstant(1)
            
            # 前日が公休でない、またはday=0
            is_valid_start = self.model.NewBoolVar(f'{staff_name}_valid_start_{day}_{n_days}')
            if day == 0:
                self.model.Add(is_valid_start == 1)
            else:
                prev_shift = self.shifts[(staff_name, day-1, '公')]
                self.model.Add(is_valid_start == 1).OnlyEnforceIf(prev_shift.Not())
                self.model.Add(is_valid_start == 0).OnlyEnforceIf(prev_shift)
            
            # 条件を組み合わせて制約を設定
            self.model.Add(is_holiday_start == 0).OnlyEnforceIf(is_valid_start.Not())
            self.model.Add(is_holiday_start == 0).OnlyEnforceIf(is_exact_length.Not())
            self.model.Add(sum(holiday_sequence) == n_days).OnlyEnforceIf([is_holiday_start, is_valid_start, is_exact_length])
            self.model.Add(sum(holiday_sequence) < n_days).OnlyEnforceIf([is_holiday_start.Not(), is_valid_start, is_exact_length])
            
            consecutive_holidays.append(is_holiday_start)
        
        # 連休の合計回数を計算（希望シフトの連休 + 新規生成の連休）
        count_var = self.model.NewIntVar(0, self.days_in_month, f'{staff_name}_holiday_count_{n_days}')
        self.model.Add(count_var == fixed_holiday_count + sum(consecutive_holidays))
        
        logger.debug(f"{staff_name}の{n_days}連休の計算完了（希望シフトの連休: {fixed_holiday_count}）")
        return count_var, consecutive_holidays

    def add_local_holiday_guarantee_constraint(self):
        """個別スタッフの連休保証を処理するメソッド"""
        logger.debug("=== 連休保証の制約設定開始 ===")
        
        for staff in self.staff_data_list:
            for constraint in staff.constraints:
                if constraint.category == "連休保証":
                    # 連休日数の取得（"二連休"→2, "三連休"→3など）
                    n_days = self.KANJI_TO_NUMBER.get(constraint.sub_category)
                    if not n_days:
                        logger.warning(f"不正な連休日数: {constraint.sub_category}")
                        continue
                    
                    # 目標回数の取得（"1回まで"→1, "2回まで"→2など）
                    target = constraint.target.replace("回まで", "")
                    target_count = int(target)
                    
                    # 連休回数の計算
                    count_var, _ = self.calculate_holiday_guarantee(staff.name, n_days)
                    
                    if constraint.type == "必須":
                        # 必須制約：指定回数の連休を保証
                        c = self.model.Add(count_var >= target_count)
                        c.WithName(f"【連休保証_必須】{staff.name}: {n_days}連休を{target_count}回以上")
                        logger.debug(f"必須制約を追加: {staff.name}, {n_days}連休, {target_count}回以上")
                    
                    elif constraint.type == "選好":
                        # 選好制約：指定回数まで重みを加算
                        weight = self.constraint_weights["選好"]["連休保証"]
                        for i in range(target_count):
                            # i+1回目の連休があるかどうかを示す変数
                            has_holiday = self.model.NewBoolVar(f'{staff.name}_has_{n_days}_holiday_{i+1}')
                            
                            # count_var >= i+1 の場合にhas_holidayが1になる
                            self.model.Add(count_var >= i+1).OnlyEnforceIf(has_holiday)
                            self.model.Add(count_var < i+1).OnlyEnforceIf(has_holiday.Not())
                            
                            # 目的関数に重みを追加
                            self.objective_terms.append(has_holiday * weight)
                            logger.debug(f"選好制約を追加: {staff.name}, {n_days}連休, {i+1}回目, 重み={weight}")
        
        logger.debug("=== 連休保証の制約設定完了 ===")

    def add_global_holiday_guarantee_constraint(self):
        """グローバルルールの連休保証を処理するメソッド"""
        logger.debug("=== グローバル連休保証の制約設定開始 ===")
        
        # グローバルルール対象のスタッフを抽出
        target_staff = [
            staff.name for staff in self.staff_data_list 
            if not staff.is_global_rule
        ]

        for constraint in self.rule_data.preference_constraints:
            if constraint.category == "連休保証":
                # 連休日数の取得（"二連休"→2, "三連休"→3など）
                n_days = self.KANJI_TO_NUMBER.get(constraint.count or "")
                if not n_days:
                    logger.warning(f"不正な連休日数: {constraint.count}")
                    continue
                
                # 目標回数の取得（"1回まで"→1, "2回まで"→2など）
                target_count = (constraint.target or "").replace("回まで", "")
                target_count = int(target_count)
                if not target_count:
                    logger.warning(f"不正な目標回数: {constraint.target}")
                    continue
                
                for staff_name in target_staff:
                    # 連休回数の計算
                    count_var, _ = self.calculate_holiday_guarantee(staff_name, n_days)
                    
                    if constraint.type == "必須":
                        # 必須制約：指定回数の連休を保証
                        c = self.model.Add(count_var >= target_count)
                        c.WithName(f"【連休保証_必須_グローバル】{staff_name}: {n_days}連休を{target_count}回以上")
                        logger.debug(f"必須制約を追加: {staff_name}, {n_days}連休, {target_count}回以上")
                    
                    elif constraint.type == "選好":
                        # 選好制約：指定回数まで重みを加算
                        weight = constraint.weight
                        for i in range(target_count):
                            # i+1回目の連休があるかどうかを示す変数
                            has_holiday = self.model.NewBoolVar(f'{staff_name}_has_{n_days}_holiday_global_{i+1}')
                            
                            # count_var >= i+1 の場合にhas_holidayが1になる
                            self.model.Add(count_var >= i+1).OnlyEnforceIf(has_holiday)
                            self.model.Add(count_var < i+1).OnlyEnforceIf(has_holiday.Not())
                            
                            # 目的関数に重みを追加
                            self.objective_terms.append(has_holiday * weight)
                            logger.debug(f"選好制約を追加: {staff_name}, {n_days}連休, {i+1}回目, 重み={weight}")
        
        logger.debug("=== グローバル連休保証の制約設定完了 ===")

     

 
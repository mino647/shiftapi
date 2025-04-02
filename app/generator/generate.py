"""
シフト生成モジュール (Constraintを関数化した完成版)
"""

import logging
from typing import Dict, List, Optional
from ortools.sat.python import cp_model
from datetime import datetime
from .logger import logger, log_function
from ..from_dict import StaffData, ShiftData, RuleData, ShiftEntry
import calendar
from dataclasses import dataclass
import time
import random
import math
import multiprocessing
import sys
from multiprocessing import Process, Queue
from queue import Empty
from ..firebase_client import write_solution_printer_log  # 追加

# ==== 修正ここから ====
# QTimer / QEventLoop を使うために PyQt6.QtCore からインポート
from PyQt6.QtCore import QTimer, QEventLoop
# ==== 修正ここまで ====

from .mapping import (
    SHIFT_TYPES,
    SHIFT_TYPE_MAPPING,
    KANJI_TO_NUMBER,
    STATUS_MAP,
    Constraint
)
from .basic_library import BasicLibrary
from .pattern_library import PatternLibrary
from .sequence_library import SequenceLibrary
from .alternative_library import AlternativeLibrary


class ShiftConstraintLibrary(BasicLibrary, PatternLibrary, SequenceLibrary, AlternativeLibrary):
    """制約ライブラリを統合するクラス"""
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
        super().__init__(
            model=model,
            shifts=shifts,
            staff_data_list=staff_data_list,
            rule_data=rule_data,
            shift_data=shift_data,
            days_in_month=days_in_month,
            year=year,
            month=month,
            staff_list=staff_list,
            reliability_map=reliability_map,
            constraint_weights=constraint_weights,
        )

    def finalize_objective(self):
        """最後にMaximizeする式を返す"""
        logger.debug("=== 目的関数の集約 ===")
        return sum(self.objective_terms)


class SolutionPrinter(cp_model.CpSolverSolutionCallback):
    def __init__(self, progress_callback=None):
        cp_model.CpSolverSolutionCallback.__init__(self)
        self.__solutions = 0
        self.__start_time = time.time()
        self.progress_callback = progress_callback
        # 最適な目的関数値を保存する変数を追加
        self.best_objective_value = None

    def on_solution_callback(self):
        self.__solutions += 1
        current_time = time.time()
        elapsed_time = current_time - self.__start_time
        objective_val = self.ObjectiveValue()
        # 目的関数値を更新
        self.best_objective_value = objective_val
        
        message = (
            f"解 {self.__solutions} が見つかりました "
            f"(経過時間: {elapsed_time:.1f}秒, 目的関数値: {objective_val:.0f})"
        )
        
        # コンソールには直接出力
        print(message)
        
        # Firestoreに書き込み
        write_solution_printer_log(message)
        
        # UIには progress_callback 経由で出力
        if self.progress_callback:
            self.progress_callback(message)


class ShiftGenerator:
    """シフト生成を行うクラス"""
    SHIFT_TYPES = SHIFT_TYPES
    SHIFT_TYPE_MAPPING = SHIFT_TYPE_MAPPING
    KANJI_TO_NUMBER = KANJI_TO_NUMBER
    STATUS_MAP = STATUS_MAP

    # 制約メソッドのリスト
    DEFAULT_CONSTRAINTS = [
        "add_one_shift_per_day",
        "add_required_staff",
        "add_monthly_holiday_limit",
        "add_consecutive_work_limit",
        "add_night_pattern",
        "add_work_count_limit",
        "add_shift_pattern",
        "add_work_pattern",
        "add_shift_type",
        "add_star_shift_constraint",
        "add_under_shift_constraint",
        "add_underscore_penalty_to_objective",
        "add_pairing_constraint",
        "add_separate_constraint",
        "add_weekday_constraint",
        "add_holiday_pattern_constraint",
        "add_global_holiday_pattern_constraint",
        "add_local_consecutive_work",
        "add_local_consecutive_dayshift_work",
        "add_global_consecutive_work",
        "add_global_consecutive_dayshift_work",
        "add_global_consecutive_shift",
        "add_local_shift_pattern_constraint",
        "add_global_shift_pattern_constraint",
        "add_shift_balance_constraints",
        "add_pair_overlap_constraints",
        "add_global_standard_reliability",
        "add_global_custom_reliability",
        "add_numbered_shift_preference",
        "add_custom_preset_constraint",
        "add_local_holiday_guarantee_constraint",
        "add_global_holiday_guarantee_constraint",
        "add_local_shift_interval_constraint", 
        "add_specific_day_shift_constraint"
    ]

    def __init__(self, weights: Optional[Dict] = None):
        self.total_preference_score = None
        self.CONSTRAINT_WEIGHTS = {
            "選好": {
                "曜日希望": 200,
                "勤務区分": 100,
                "休暇パターン": 200,
                "出勤パターン": 200,
                "シフトパターン": 200,
                "ペアリング": 100,
                "セパレート": 200,
                "カスタムプリセット": 200,
                "シフトバランス": 300,
                "夜勤ペア重複": -333,
                "夜勤ペア重複3回以上": -10000,
                "同一勤務の3連続": -10000,
                "日勤帯連勤": 100
            }
        }
        if weights:
            self.CONSTRAINT_WEIGHTS = weights
        
        # 目的関数値を保持するインスタンス変数を追加
        self.last_objective_value = None

    def get_weekday_array(self, year: int, month: int, days_in_month: int) -> List[int]:
        """月の日付配列を曜日配列に変換（0=月曜、6=日曜）"""
        first_day = datetime(year, month, 1)
        return [(first_day.weekday() + day) % 7 for day in range(days_in_month)]

    def normalize_shift_type(self, shift_type: str) -> str:
        """シフトタイプを正規化する"""
        return self.SHIFT_TYPE_MAPPING.get(shift_type, shift_type)

    def solve_in_process(self, staff_data_list, rule_data, shift_data, active_constraints, result_queue, progress_queue):
        """別プロセスで実行される解探索"""
        # ==== ここを追加（子プロセスが立ち上がった直後にファイルハンドラを除去） ====
        import logging
        base_logger = logging.getLogger("ShiftScheduler")
        for h in base_logger.handlers[:]:
            if isinstance(h, logging.FileHandler):
                base_logger.removeHandler(h)
                h.close()
        # ============================================================
        if hasattr(sys, '_MEIPASS'):
            multiprocessing.set_start_method('spawn', force=True)
        try:
            # スタッフリストの作成
            staff_list = [s.name for s in staff_data_list]
            
            # 日数の計算
            _, days_in_month = calendar.monthrange(shift_data.year, shift_data.month)
            
            # モデルの構築
            model = cp_model.CpModel()
            shifts = {}
            for st in staff_list:
                for d in range(days_in_month):
                    for stype in self.SHIFT_TYPES.keys():
                        var_name = f"shift_{st}_{d}_{stype}"
                        shifts[(st, d, stype)] = model.NewBoolVar(var_name)

            # 信頼度マップの構築
            staff_reliability_map = {}
            for sd in staff_data_list:
                staff_reliability_map[sd.name] = sd.reliability_override or 30

            # 制約ライブラリの構築と制約の追加
            clib = ShiftConstraintLibrary(
                model=model,
                shifts=shifts,
                staff_data_list=staff_data_list,
                rule_data=rule_data,
                shift_data=shift_data,
                days_in_month=days_in_month,
                year=shift_data.year,
                month=shift_data.month,
                staff_list=staff_list,
                reliability_map=staff_reliability_map,
                constraint_weights=self.CONSTRAINT_WEIGHTS
            )

            # 制約の適用
            constraints_to_apply = active_constraints or self.DEFAULT_CONSTRAINTS
            for constraint in constraints_to_apply:
                if hasattr(clib, constraint):
                    getattr(clib, constraint)()

            # 希望シフトの追加
            if shift_data and shift_data.entries:
                for ent in shift_data.entries:
                    model.Add(shifts[(ent.staff_name, ent.day-1, ent.shift_type)] == 1)

            # 目的関数の設定
            clib.add_preference_objective()
            objective_expr = clib.finalize_objective()
            model.Maximize(objective_expr)

            # ソルバーの実行
            def progress_handler(message):
                progress_queue.put(message)
            
            solution_printer = SolutionPrinter(progress_handler)
            solver = cp_model.CpSolver()
            solver.parameters.num_search_workers = max(1, multiprocessing.cpu_count() - 1)
            solver.parameters.max_time_in_seconds = shift_data.search_time
            
            status = solver.Solve(model, solution_printer)

            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                entries = []
                for st in staff_list:
                    for d in range(days_in_month):
                        for stype in self.SHIFT_TYPES.keys():
                            if solver.Value(shifts[(st, d, stype)]):
                                entries.append({
                                    'staff_name': st,
                                    'day': d+1,
                                    'shift_type': stype
                                })
                result_queue.put(('success', entries))
            else:
                result_queue.put(('status', status))
                
        except Exception as e:
            result_queue.put(('error', str(e)))

    @log_function
    def generate_shift(
        self,
        staff_data_list: List[StaffData],
        rule_data: RuleData,
        shift_data: ShiftData,
        active_constraints: Optional[List[str]] = None,
        progress_callback=None,
        turbo_mode: bool = False
    ) -> Optional[ShiftData]:
        """シフトを生成する"""
        try:
            if not staff_data_list:
                logger.error("スタッフデータが空です")
                return None

            staff_list = [s.name for s in staff_data_list]
            year = shift_data.year if shift_data else datetime.now().year
            month = shift_data.month if shift_data else datetime.now().month
            _, days_in_month = calendar.monthrange(year, month)

            # 初期パラメータのログをFirestoreに書き込み
            write_solution_printer_log("=== シフト生成パラメータ ===", reset=True)
            write_solution_printer_log(f"対象期間: {year}年{month}月 (日数:{days_in_month})")
            write_solution_printer_log(f"基本公休日数: {rule_data.holiday_count}日")
            write_solution_printer_log(f"連続勤務制限: {rule_data.consecutive_work_limit}日")
            write_solution_printer_log(f"必要人数:")
            write_solution_printer_log(f"  - 通常日勤: {rule_data.weekday_staff}人")
            write_solution_printer_log(f"  - 日曜日勤: {rule_data.sunday_staff}人")
            write_solution_printer_log("=== スタッフ情報 ===")
            write_solution_printer_log(f"総スタッフ数: {len(staff_data_list)}名")

            model = cp_model.CpModel()
            shifts = {}
            for st in staff_list:
                for d in range(days_in_month):
                    for stype in self.SHIFT_TYPES.keys():
                        var_name = f"shift_{st}_{d}_{stype}"
                        shifts[(st, d, stype)] = model.NewBoolVar(var_name)

            # 信頼度デフォルト30
            staff_reliability_map = {}
            for sd in staff_data_list:
                if sd.reliability_override is not None:
                    staff_reliability_map[sd.name] = sd.reliability_override
                else:
                    staff_reliability_map[sd.name] = 30

            # ConstraintLibraryを作り、制約を追加
            clib = ShiftConstraintLibrary(
                model=model,
                shifts=shifts,
                staff_data_list=staff_data_list,
                rule_data=rule_data,
                shift_data=shift_data,
                days_in_month=days_in_month,
                year=year,
                month=month,
                staff_list=staff_list,
                reliability_map=staff_reliability_map,
                constraint_weights=self.CONSTRAINT_WEIGHTS
            )

            # 制約の適用
            if active_constraints is None:
                constraints_to_apply = self.DEFAULT_CONSTRAINTS
            else:
                constraints_to_apply = active_constraints  # まずこれを代入
                logger.info(f"デバッグモード: 以下の制約のみを適用: {constraints_to_apply}")
                write_solution_printer_log("=== デバッグモード ===") 
            for constraint in constraints_to_apply:
                if hasattr(clib, constraint):
                  
                    getattr(clib, constraint)()
               

            # 希望シフトの追加
            if shift_data and shift_data.entries:
                for ent in shift_data.entries:
                    c = model.Add(shifts[(ent.staff_name, ent.day-1, ent.shift_type)] == 1)
                    c.WithName(f"【希望シフト】{ent.staff_name}:{ent.day}日:{ent.shift_type}")

            # 選好制約 (目的関数)
            clib.add_preference_objective()
            objective_expr = clib.finalize_objective()
            model.Maximize(objective_expr)

            solver = cp_model.CpSolver()
            solution_printer = SolutionPrinter(progress_callback)
            solver.parameters.random_seed = random.randint(0, 1000000)
            solver.parameters.max_time_in_seconds = shift_data.search_time
            
            # スレッド数の設定
            available_threads = multiprocessing.cpu_count()
            if turbo_mode:
                # ターボモード（シングルプロセス）
                solver.parameters.num_search_workers = min(available_threads, 12)
                write_solution_printer_log(f"ターボモード: {solver.parameters.num_search_workers}スレッドで実行")
                solver.parameters.max_time_in_seconds = shift_data.search_time
                status = solver.Solve(model, solution_printer)
                
                # 目的関数値を取得して保存
                if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                    self.last_objective_value = solution_printer.best_objective_value
                else:
                    self.last_objective_value = None
            else:
                # バランスモード（マルチプロセス）
                if available_threads <= 2:
                    solver.parameters.num_search_workers = 1
                else:
                    solver.parameters.num_search_workers = min(available_threads, 12) - 1
                write_solution_printer_log(f"バランスモード: {solver.parameters.num_search_workers}スレッドで実行")
                
                # ==== 修正ここから ====
                result_queue = Queue()
                progress_queue = Queue()
                
                solver_process = Process(
                    target=self.solve_in_process,
                    args=(
                        staff_data_list,
                        rule_data,
                        shift_data,
                        constraints_to_apply,
                        result_queue,
                        progress_queue
                    )
                )
                solver_process.start()

                # QTimer+QEventLoopでプロセス終了を待つ
                event_loop = QEventLoop()
                poll_timer = QTimer()
                poll_timer.setInterval(100)  # 100msごとにポーリング

                # ローカル変数に結果を格納して後で返す
                result_type = None
                result_value = None

                def poll_subprocess():
                    nonlocal result_type, result_value
                    try:
                        # 進捗メッセージ取得
                        while True:
                            try:
                                message = progress_queue.get_nowait()
                                if progress_callback:
                                    progress_callback(message)
                            except Empty:
                                break
                        # プロセス終了チェック
                        if not solver_process.is_alive():
                            poll_timer.stop()
                            solver_process.join()
                            # 結果取得
                            try:
                                result_type, result_value = result_queue.get_nowait()
                            except Empty:
                                result_type, result_value = ('error', f'予期せぬ終了。ステータス: {STATUS_MAP.get(cp_model.MODEL_INVALID, "不明なエラー")}')
                                write_solution_printer_log(result_value)  # エラーメッセージを追加
                            event_loop.quit()
                    except Exception as ex:
                        logger.error(f"進捗処理エラー: {str(ex)}")
                        poll_timer.stop()
                        solver_process.join()
                        result_type, result_value = ('error', str(ex))
                        write_solution_printer_log(f"エラーが発生: {str(ex)}")  # エラーメッセージを追加
                        event_loop.quit()

                poll_timer.timeout.connect(poll_subprocess)
                poll_timer.start()

                # イベントループを回してサブプロセス完了を待つ
                event_loop.exec()

                # ここで result_type, result_value に結果が入っている
                if result_type == 'error':
                    logger.error(str(result_value))
                    if progress_callback:
                        progress_callback(str(result_value))
                    return None
                elif result_type == 'success':
                    entries_data = result_value
                else:
                    entries_data = None
                # ==== 修正ここまで ====

            # 結果の処理ロジックを共通化
            def create_shift_data(entries_data) -> ShiftData:
                entries = []
                staff_display_info = {
                    sd.name: {'role': sd.role, 'is_part_time': sd.is_part_time}
                    for sd in staff_data_list
                }
                for entry_data in entries_data:
                    staff_name = entry_data['staff_name']
                    entry = ShiftEntry(
                        staff_name=staff_name,
                        day=entry_data['day'],
                        shift_type=entry_data['shift_type'],
                        role=staff_display_info[staff_name]['role'],
                        is_part_time=staff_display_info[staff_name]['is_part_time']
                    )
                    entries.append(entry)
                return ShiftData(
                    year=year,
                    month=month,
                    search_time=shift_data.search_time,
                    entries=entries,
                    preference_entries=[]  # 空のリストを追加
                )

            # 結果判定（共通）
            if turbo_mode:
                # ターボモードの場合は結果を直接取得
                if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                    entries_data = []
                    for st in staff_list:
                        for d in range(days_in_month):
                            for stype in self.SHIFT_TYPES.keys():
                                if solver.Value(shifts[(st, d, stype)]):
                                    entries_data.append({
                                        'staff_name': st,
                                        'day': d+1,
                                        'shift_type': stype
                                    })
                else:
                    entries_data = None

            # 共通の結果処理
            if entries_data:
                return create_shift_data(entries_data)
            
            # エラーメッセージは最後に一度だけ出力
            logger.error(f"解が見つかりません。ステータス: {STATUS_MAP.get(cp_model.INFEASIBLE)}")
            return None

        except Exception as e:
            logger.error(f"エラーが発生: {str(e)}")
            if progress_callback:
                progress_callback(f"エラーが発生: {str(e)}")
            self.last_objective_value = None  # エラー時は目的関数値をNoneに設定
            return None

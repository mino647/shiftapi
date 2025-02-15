"""
辞書形式のデータをクラスインスタンスに変換するモジュール
"""
from typing import Dict, List, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class ShiftCount:
    """シフト回数の制限を表すデータクラス
    
    Attributes:
        min (int): 最小シフト回数
        max (int): 最大シフト回数
    """
    min: int
    max: int
    
    def get(self, key: str, default=None) -> Optional[int]:
        """辞書のようなget操作をサポート
        
        Args:
            key (str): 取得したい属性名（'min'または'max'）
            default: キーが存在しない場合のデフォルト値
            
        Returns:
            Optional[int]: 属性値またはデフォルト値
        """
        return getattr(self, key, default)

@dataclass
class StaffConstraint:
    """スタッフの制約を表すデータクラス"""
    type: str
    category: str
    sub_category: str
    count: str
    target: str
    times: str

@dataclass
class RuleConstraint:
    """ルールの制約を表すデータクラス"""
    type: str
    category: str
    sub_category: str
    count: str
    final: str
    target: str
    weight: int
    times: str

@dataclass
class StaffData:
    """スタッフ情報を表すデータクラス"""
    name: str
    role: str
    is_day_shift_only: bool
    is_part_time: bool
    is_global_rule: bool
    shift_counts: Dict[str, Dict[str, int]]
    preferences: str
    holiday_override: Optional[bool]
    reliability_override: Optional[int]
    constraints: List[StaffConstraint]

@dataclass
class RuleData:
    """ルール情報を表すデータクラス"""
    holiday_count: int
    consecutive_work_limit: int
    weekday_staff: float
    weekday_preference_level: Optional[int]
    sunday_staff: int
    sunday_preference_level: Optional[int]
    early_staff: int
    late_staff: int
    night_staff: int
    weekday_reliability: Optional[int]
    sunday_reliability: Optional[int]
    preference_constraints: List[RuleConstraint]

@dataclass
class ShiftEntry:
    """シフトエントリーを表すデータクラス"""
    staff_name: str
    day: int
    shift_type: str
    role: str
    is_part_time: bool

@dataclass
class ShiftData:
    """シフトデータを表すデータクラス"""
    year: int
    month: int
    search_time: int
    entries: List[ShiftEntry]
    preference_entries: List[ShiftEntry]

@dataclass
class WeightData:
    """重み付けデータを表すデータクラス"""
    曜日希望: int
    勤務希望: int
    連続休暇: int
    連続勤務: int
    日勤帯連勤: int
    連休保証: int
    シフトパターン: int
    ペアリング: int
    セパレート: int
    カスタムプリセット: int
    シフトバランス: int
    夜勤ペア重複: int
    夜勤ペア重複3回以上: int
    同一勤務の3連続: int

class DictToInstance:
    """辞書データからインスタンスを生成するクラス"""
    
    @staticmethod
    def create_staff_constraint(data: Dict) -> StaffConstraint:
        """辞書からStaffConstraintインスタンスを生成"""
        return StaffConstraint(
            type=data["type"],
            category=data["category"],
            sub_category=data["sub_category"],
            count=data.get("count", ""),
            target=data["target"],
            times=data.get("times", "")
        )

    @staticmethod
    def create_rule_constraint(data: Dict) -> RuleConstraint:
        """辞書からRuleConstraintインスタンスを生成"""
        return RuleConstraint(
            type=data["type"],
            category=data["category"],
            sub_category=data["sub_category"],
            count=data["count"],
            final=data["final"],
            target=data["target"],
            weight=data["weight"],
            times=data["times"]
        )

    @staticmethod
    def create_staff_data(data: Dict) -> StaffData:
        """辞書からStaffDataインスタンスを生成"""
        shift_counts = {
            shift_type: {
                "min": count_data["min"],
                "max": count_data["max"]
            }
            for shift_type, count_data in data["shift_counts"].items()
        }
        
        return StaffData(
            name=data["name"],
            role=data["role"],
            is_day_shift_only=data["is_day_shift_only"],
            is_part_time=data["is_part_time"],
            is_global_rule=data["is_global_rule"],
            shift_counts=shift_counts,
            preferences=data["preferences"],
            holiday_override=data["holiday_override"],
            reliability_override=data["reliability_override"],
            constraints=[
                DictToInstance.create_staff_constraint(c)
                for c in data.get("constraints", [])
            ]
        )

    @staticmethod
    def create_rule_data(data: Dict) -> RuleData:
        """辞書からRuleDataインスタンスを生成"""
        return RuleData(
            holiday_count=data["holiday_count"],
            consecutive_work_limit=data["consecutive_work_limit"],
            weekday_staff=data["weekday_staff"],
            weekday_preference_level=data["weekday_preference_level"],
            sunday_staff=data["sunday_staff"],
            sunday_preference_level=data["sunday_preference_level"],
            early_staff=data["early_staff"],
            late_staff=data["late_staff"],
            night_staff=data["night_staff"],
            weekday_reliability=data["weekday_reliability"],
            sunday_reliability=data["sunday_reliability"],
            preference_constraints=[
                DictToInstance.create_rule_constraint(c)
                for c in data.get("preference_constraints", [])
            ]
        )

    @staticmethod
    def create_shift_entry(data: Dict) -> ShiftEntry:
        """辞書からShiftEntryインスタンスを生成"""
        return ShiftEntry(
            staff_name=data["staff_name"],
            day=data["day"],
            shift_type=data["shift_type"],
            role=data["role"],
            is_part_time=data["is_part_time"]
        )

    @staticmethod
    def create_shift_data(data: Dict) -> ShiftData:
        """辞書からShiftDataインスタンスを生成"""
        return ShiftData(
            year=data["year"],
            month=data["month"],
            search_time=data["search_time"],
            entries=[
                DictToInstance.create_shift_entry(entry)
                for entry in data.get("entries", [])
            ],
            preference_entries=[
                DictToInstance.create_shift_entry(entry)
                for entry in data.get("preference_entries", [])
            ]
        )

    @staticmethod
    def create_weight_data(data: Dict) -> Dict:
        """辞書からWeightDataを生成（そのまま返す）"""
        return data 
"""
Webアプリ版のデータをデスクトップアプリ版の形式に変換するモジュール
"""
from dataclasses import dataclass
from typing import List, Optional, Dict


@dataclass
class StaffData:
    name: str
    role: str
    is_day_shift_only: bool
    is_part_time: bool
    is_global_rule: bool
    shift_counts: Dict[str, Dict[str, int]]
    preferences: str
    holiday_override: Optional[int]
    reliability_override: Optional[int]
    constraints: List

@dataclass
class PreferenceConstraint:
    type: str
    category: str
    sub_category: str
    count: str
    final: str
    target: str
    weight: int
    times: str

@dataclass
class RuleData:
    holiday_count: int
    consecutive_work_limit: int
    weekday_staff: float
    weekday_preference_level: Optional[int]
    sunday_staff: float
    sunday_preference_level: Optional[int]
    early_staff: int
    late_staff: int
    night_staff: float
    weekday_reliability: Optional[int]
    sunday_reliability: Optional[int]
    preference_constraints: List[PreferenceConstraint]

    def __post_init__(self):
        # 文字列で来た場合は整数に変換
        self.holiday_count = int(self.holiday_count)
        self.consecutive_work_limit = int(self.consecutive_work_limit)

@dataclass
class ShiftEntry:
    staff_name: str
    day: int
    shift_type: str
    role: str
    is_part_time: bool

@dataclass
class ShiftData:
    year: int
    month: int
    search_time: int
    entries: List[ShiftEntry]
    preference_entries: List

def convert_constraint(constraint: dict, include_preference_fields: bool = False) -> dict:
    """制約を変換する"""
    # カテゴリごとの制約マッピング定義
    CONSTRAINT_MAPPINGS = {
        # value2までのパターン
        ( "勤務希望", "カスタムプリセット"): {
            "type": "type",
            "category": "category",
            "sub_category": "value1",
            "count": "",
            "final": "",
            "target": "value2"
        },
        
        # value3までのパターン
        ("シフトパターン", "連続勤務", "日勤帯連勤", "連続休暇", "連休保証", "シフトバランス"): {
            "type": "type",
            "category": "category",
            "sub_category": "value1",
            "count": "value2",
            "final": "",
            "target": "value3"
        },
        
        # value4までのパターン
        ("曜日希望", "ペアリング", "セパレート", "ペア重複", "連続シフト"): {
            "type": "type",
            "category": "category",
            "sub_category": "value1",
            "count": "value2",
            "final": "value3",
            "target": "value4"
        },
        ("シフト適性"): {
            "type": "type",
            "category": "category",
            "sub_category": "value2",
            "count": "",
            "final": "",
            "target": "value3"

        },
    }

    # カテゴリに応じたマッピングを適用
    for categories, mapping in CONSTRAINT_MAPPINGS.items():
        if constraint["category"] in categories:
            result = {
                key: constraint[value] if value else ""
                for key, value in mapping.items()
            }
            break
    
    # preference_constraints用のフィールドを追加
    if include_preference_fields:
        result.update({
            "weight": constraint["weight"],
            "times": constraint["times"]
        })
    
    return result


def convert_rule_data(web_data: dict) -> dict:
    """
    Webアプリ版のデータをデスクトップアプリ版の形式に変換する
    
    Args:
        web_data: ruleDataオブジェクト
    """
    desktop_rules = {
        "holiday_count": web_data["basicSettings"]["baseHolidays"],
        "consecutive_work_limit": web_data["basicSettings"]["consecutiveWorkDays"],
        "weekday_staff": web_data["requiredStaffCount"]["日勤"],
        "weekday_preference_level": None,
        "sunday_staff": web_data["requiredStaffCount"]["日曜の日勤"],
        "sunday_preference_level": None,
        "early_staff": web_data["requiredStaffCount"]["早番"],
        "late_staff": web_data["requiredStaffCount"]["遅番"],
        "night_staff": web_data["requiredStaffCount"]["夜勤"],
        "weekday_reliability": web_data["basicSettings"]["normalShiftSuitability"] if web_data["basicSettings"]["useNormalShiftSuitability"] else None,
        "sunday_reliability": web_data["basicSettings"]["sundayShiftSuitability"] if web_data["basicSettings"]["useSundayShiftSuitability"] else None,
        "preference_constraints": []
    }
    
    return {"rules": desktop_rules}

def convert_staff_constraint(constraint: dict) -> dict:
    """スタッフの制約を変換する"""
    # スタッフ用の制約マッピング定義
    STAFF_CONSTRAINT_MAPPINGS = {
        # value2までのパターン（countなし）
        ("勤務希望", "カスタムプリセット"): {
            "type": "type",
            "category": "category",
            "sub_category": "value1",
            "target": "value2"
        },
        
        # value3までのパターン（timesなし）
        ("シフトパターン", "連続勤務", "日勤帯連勤", "連続休暇", "連休保証", "シフトバランス"): {
            "type": "type",
            "category": "category",
            "sub_category": "value1",
            "count": "value2",
            "target": "value3"
        },
        
        # value4までのパターン（timesあり）
        ("曜日希望", "ペアリング", "セパレート", "ペア重複", "連続シフト"): {
            "type": "type",
            "category": "category",
            "sub_category": "value1",
            "count": "value2",
            "target": "value3",
            "times": "value4"
        }
    }
    
    # カテゴリに応じたマッピングを適用
    for categories, mapping in STAFF_CONSTRAINT_MAPPINGS.items():
        if constraint["category"] in categories:
            result = {
                key: constraint[value] if value else ""
                for key, value in mapping.items()
            }
            break
    
    return result

def convert_staffdata(web_data: dict) -> dict:
    """
    Webアプリ版のスタッフデータをデスクトップアプリ版の形式に変換する
    
    Args:
        web_data: staffDataオブジェクト
    """
    desktop_staffs = []
    
    # staffListの各スタッフを変換
    for staff in web_data["staffList"]:
        desktop_staff = {
            "name": staff["name"],
            "role": staff["role"],
            "is_day_shift_only": staff["is_day_shift_only"],
            "is_part_time": staff["is_part_time"],
            "is_global_rule": staff["is_global_rule"],
            "shift_counts": {
                "早番": staff["shift_count"]["早番"],
                "日勤": staff["shift_count"]["日勤"],
                "遅番": staff["shift_count"]["遅番"],
                "夜勤": staff["shift_count"]["夜勤"]
            },
            "preferences": "",  # 空欄固定
            "holiday_override": None if staff["holiday_overwrite"] is False else staff["holiday_overwrite"],
            "reliability_override": None if staff["reliability_overwrite"] is False else staff["reliability_overwrite"],
            "constraints": []
        }
        
        # 制約の変換（weightなし）
        if "constraints" in staff:
            for constraint in staff["constraints"]:
                desktop_staff["constraints"].append(
                    convert_staff_constraint(constraint)
                )
        
        desktop_staffs.append(desktop_staff)
    
    return {"staffs": desktop_staffs}  # staffsキーでリストを返す

def convert_shiftdata(web_data: dict, staff_data: dict, rule_data: dict) -> dict:
    """
    Webアプリ版のシフトデータをデスクトップアプリ版の形式に変換する
    
    Args:
        web_data: shiftDataオブジェクト
        staff_data: staffDataオブジェクト（スタッフ情報取得用）
        rule_data: ruleDataオブジェクト（年月情報取得用）
    """
    # スタッフ名から情報を素早く取得するための辞書を作成
    staff_info = {
        staff["name"]: {
            "role": staff["role"],
            "is_part_time": staff["is_part_time"]
        }
        for staff in staff_data["staffList"]
    }

    return {
        "year": rule_data["basicSettings"]["year"],
        "month": rule_data["basicSettings"]["month"],
        "search_time": web_data["searchTime"],
        "entries": [
            {
                "staff_name": entry["staff_name"],
                "day": entry["day"],
                "shift_type": entry["shift_type"],
                "role": staff_info[entry["staff_name"]]["role"],
                "is_part_time": staff_info[entry["staff_name"]]["is_part_time"]
            } for entry in web_data["entries"]
        ],
        "preference_entries": []  # 空の配列を追加
    }

def convert_weightdata(web_data: dict) -> dict:
    """
    Webアプリ版の重み設定をデスクトップアプリ版の形式に変換する
    
    Args:
        web_data: weightDataオブジェクト
    """
    return {
        "選好": {
            "曜日希望": web_data["weightData"]["曜日希望"],
            "勤務希望": web_data["weightData"]["勤務希望"],
            "連続休暇": web_data["weightData"]["連続休暇"],
            "連続勤務": web_data["weightData"]["連続勤務"],
            "日勤帯連勤": web_data["weightData"]["日勤帯連勤"],
            "連休保証": web_data["weightData"]["連休保証"],
            "シフトパターン": web_data["weightData"]["シフトパターン"],
            "ペアリング": web_data["weightData"]["ペアリング"],
            "セパレート": web_data["weightData"]["セパレート"],
            "カスタムプリセット": web_data["weightData"]["カスタムプリセット"],
            # 固定値
            "シフトバランス": 300,
            "夜勤ペア重複": -3000,
            "夜勤ペア重複3回以上": -10000,
            "同一勤務の3連続": -10000
        }
    }


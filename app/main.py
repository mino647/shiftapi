"""
FastAPIアプリケーションのエントリーポイント

このモジュールは、シフト管理ツールのAPIサーバーのメインエントリーポイントです。
Firestoreとの接続やルーティングの設定を行います。
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .firebase_client import FirestoreListener, get_firestore_client, write_result_to_firestore
from app.convert import convert_rule_data, convert_staffdata, convert_shiftdata, convert_weightdata
import logging
from fastapi.responses import HTMLResponse
import json
from .from_dict import StaffData, ShiftEntry, ShiftData, RuleData
from typing import List, Optional, Dict
from datetime import datetime
from google.cloud import firestore
from .generator import ShiftGenerator  # 既存のクラスをインポート
from .from_dict import DictToInstance
from .api_logger import api_logger



# その後でloggerをインポート
# from .generator.logger import logger

app = FastAPI(
    title="シフト管理API",
    description="シフト生成・管理のためのバックエンドAPI"
)

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本番環境では適切に制限する
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Firestoreリスナーのインスタンス
firestore_listener = FirestoreListener()

@app.on_event("startup")
async def startup_event():
    """アプリケーション起動時にFirestoreリスナーを開始"""
    # ロガーの初期化確認
    api_logger.info("APIサーバー起動")
    api_logger.debug("ログシステム初期化完了")
    
    # 既存の処理
    api_logger.debug("アプリケーション起動")
    api_logger.info("Firestoreリスナー開始")
    firestore_listener.start_listening()

@app.get("/")
async def root():
    """動作確認用のエンドポイント"""
    return {"message": "シフト管理APIサーバー起動中"}

@app.get("/convert-test")
async def convert_test():
    """変換テスト用エンドポイント"""
    try:
        db = get_firestore_client()
        doc_ref = db.collection('requests').document('que')
        doc = doc_ref.get()
        
        if doc.exists:
            response_data = doc.to_dict()
            if 'json' in response_data:
                input_data = response_data['json']  # jsonデータを取得
                rule_data = input_data['ruleData']  # ruleDataを取得
                converted_data = convert_rule_data(rule_data)  # 変換
                return {
                    "status": "success",
                    "original": rule_data,
                    "converted": converted_data
                }
        
        return {"status": "error", "message": "データが見つかりません"}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/firebase-test")
async def firebase_test():
    """Firestore接続テスト"""
    try:
        db = get_firestore_client()
        # que 1ドキュメントにアクセス
        test_ref = db.collection('requests').document('que')
        doc = test_ref.get()
        return {
            "status": "success",
            "exists": doc.exists,
            "data": doc.to_dict() if doc.exists else None
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/view-convert", response_class=HTMLResponse)
async def view_convert():
    """変換結果をHTML形式で表示"""
    try:
        db = get_firestore_client()
        doc_ref = db.collection('requests').document('que')
        doc = doc_ref.get()
        
        if doc.exists:
            response_data = doc.to_dict()
            if 'json' in response_data:
                input_data = response_data['json']
                rule_data = input_data.get('ruleData')
                staff_data = input_data.get('staffData')
                shift_data = input_data.get('shiftData')  # shiftDataも取得
                
                if rule_data and staff_data and shift_data:  # shiftDataも確認
                    converted_rule = convert_rule_data(rule_data)
                    converted_staff = convert_staffdata(staff_data)
                    converted_shift = convert_shiftdata(shift_data, staff_data, rule_data)
                    converted_weight = convert_weightdata(input_data)  # weightDataを変換
                    
                    # HTML形式で整形
                    html_content = f"""
                    <html>
                        <head>
                            <title>変換結果</title>
                            <style>
                                pre {{ 
                                    background: #f4f4f4; 
                                    padding: 15px; 
                                    border-radius: 5px;
                                    white-space: pre-wrap;
                                }}
                            </style>
                        </head>
                        <body>
                            <h2>元のデータ:</h2>
                            <pre>{json.dumps({"ruleData": rule_data, "staffData": staff_data, "shiftData": shift_data}, indent=2, ensure_ascii=False)}</pre>
                            
                            <h2>変換後のデータ:</h2>
                            <pre>{json.dumps({"rules": converted_rule["rules"], 
                                            "staffs": converted_staff["staffs"], 
                                            "shifts": converted_shift,
                                            "weights": converted_weight}, indent=2, ensure_ascii=False)}</pre>
                        </body>
                    </html>
                    """
                    return html_content
                
        return "データが見つかりません"
        
    except Exception as e:
        return f"エラー: {str(e)}"

@app.post("/generate-shift")
async def generate_shift():
    """シフトを生成するエンドポイント"""
    try:
        # 1. Firestoreからデータ取得
        db = get_firestore_client()
        doc_ref = db.collection('requests').document('que')
        doc = doc_ref.get()
        
        if doc.exists:
            response_data = doc.to_dict()
            if 'json' in response_data:
                # 文字列からJSONオブジェクトにパース
                input_data = json.loads(response_data['json'])
                
                # 2. データの変換とインスタンス化
                converted_data = {
                    "staffData": convert_staffdata(input_data['staffData']),
                    "ruleData": convert_rule_data(input_data['ruleData']),
                    "shiftData": convert_shiftdata(
                        input_data['shiftData'],
                        input_data['staffData'],
                        input_data['ruleData']
                    ),
                    "weightData": convert_weightdata(input_data)
                }

                # from_dictでインスタンス化
                staff_instances = [
                    DictToInstance.create_staff_data(staff)
                    for staff in converted_data["staffData"]["staffs"]
                ]
                rule_instance = DictToInstance.create_rule_data(converted_data["ruleData"]["rules"])
                shift_instance = DictToInstance.create_shift_data(converted_data["shiftData"])
                weight_instance = DictToInstance.create_weight_data(converted_data["weightData"])

                # 4. シフト生成
                generator = ShiftGenerator(weights=weight_instance)
                solution = generator.generate_shift(
                    staff_data_list=staff_instances,
                    rule_data=rule_instance,
                    shift_data=shift_instance,
                    turbo_mode=True
                )
                
                if solution:
                    result_id = write_result_to_firestore(solution, input_data)
                    api_logger.debug("=== ソルバー実行完了 ===")
                    
                    api_logger.info("シフト生成成功")
                    
                    # solutionを必要な形式に変換
                    shifts_dict = {}
                    for entry in solution.entries:
                        if entry.staff_name not in shifts_dict:
                            shifts_dict[entry.staff_name] = [''] * 32
                        shifts_dict[entry.staff_name][entry.day] = entry.shift_type
                    
                    # editキーでラップ
                    return {
                        "edit": {
                            'year': solution.year,
                            'month': solution.month,
                            'shifts': shifts_dict
                        }
                    }
                else:
                    api_logger.warning("シフト生成失敗（解なし）")
                    return {"status": "warning", "message": "シフトを生成できませんでした"}
            
        return {"status": "error", "message": "データが見つかりません"}
        
    except Exception as e:
        api_logger.error(f"全体エラー: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.get("/generate-shift-test")
async def generate_shift_test():
    """動作確認用のGETエンドポイント"""
    try:
        # 1. Firestoreからデータ取得
        db = get_firestore_client()
        doc_ref = db.collection('requests').document('que')
        doc = doc_ref.get()
        
        # デバッグ用ログ追加
        print("Firestore接続確認:", db)
        print("ドキュメント存在確認:", doc.exists)
        if doc.exists:
            print("取得データ:", doc.to_dict())
            return {"status": "success", "data": doc.to_dict()}
            
        return {"status": "error", "message": "データが見つかりません"}
        
    except Exception as e:
        print("エラー発生:", str(e))
        return {"status": "error", "message": str(e)}

@app.get("/generate-test")
async def generate_test():
    """シフト生成テスト用エンドポイント（デバッグ用）"""
    try:
        api_logger.debug("=== シフト生成テスト開始 ===")
        
        # 1. Firestoreデータ取得
        db = get_firestore_client()
        doc_ref = db.collection('requests').document('que')
        doc = doc_ref.get()
        
        if not doc.exists:
            api_logger.error("Firestoreにデータが存在しません")
            return {"status": "error", "message": "データが見つかりません"}
            
        response_data = doc.to_dict()
        if 'json' not in response_data:
            api_logger.error("JSONデータが存在しません")
            return {"status": "error", "message": "JSONデータが見つかりません"}
            
        # 文字列からJSONオブジェクトにパース
        input_data = json.loads(response_data['json'])
        api_logger.debug(f"入力データ: {input_data.keys()}")

        # 2. データ変換
        api_logger.debug("=== データ変換開始 ===")
        converted_data = {
            "staffData": convert_staffdata(input_data['staffData']),
            "ruleData": convert_rule_data(input_data['ruleData']),
            "shiftData": convert_shiftdata(
                input_data['shiftData'],
                input_data['staffData'],
                input_data['ruleData']
            ),
            "weightData": convert_weightdata(input_data)
        }
        api_logger.debug(f"変換後データ: {converted_data.keys()}")

        # 3. インスタンス化
        api_logger.debug("=== インスタンス化開始 ===")
        try:
            staff_instances = [
                DictToInstance.create_staff_data(staff)
                for staff in converted_data["staffData"]["staffs"]
            ]
            rule_instance = DictToInstance.create_rule_data(converted_data["ruleData"]["rules"])
            shift_instance = DictToInstance.create_shift_data(converted_data["shiftData"])
            weight_instance = DictToInstance.create_weight_data(converted_data["weightData"])
            api_logger.debug("インスタンス化完了")
        except Exception as e:
            api_logger.error(f"インスタンス化エラー: {str(e)}")
            raise

        # シフト生成部分をより詳細にログ
        api_logger.debug("=== ソルバー実行開始 ===")
        generator = ShiftGenerator(weights=weight_instance)
        
        # ソルバーに渡す直前のデータを確認
        api_logger.debug(f"スタッフデータ数: {len(staff_instances)}")
        api_logger.debug(f"ルールデータ: {rule_instance}")
        api_logger.debug(f"シフトデータ: {shift_instance}")
        
        # ソルバー実行（この部分で CPU 使用率が上がるはず）
        solution = generator.generate_shift(
            staff_data_list=staff_instances,
            rule_data=rule_instance,
            shift_data=shift_instance,
            turbo_mode=True
        )
        api_logger.debug("=== ソルバー実行完了 ===")
        
        if solution:
            api_logger.info("シフト生成成功")
            
            # solutionを必要な形式に変換
            shifts_dict = {}
            for entry in solution.entries:
                if entry.staff_name not in shifts_dict:
                    shifts_dict[entry.staff_name] = [''] * 32
                shifts_dict[entry.staff_name][entry.day] = entry.shift_type
            
            formatted_solution = {
                'year': solution.year,
                'month': solution.month,
                'shifts': shifts_dict
            }
            
            return {
                "status": "success",
                "solution": formatted_solution,  # 変換後のデータ
                "debug_info": {
                    "staff_count": len(staff_instances),
                    "converted_data": converted_data
                }
            }
        else:
            api_logger.warning("シフト生成失敗（解なし）")
            return {"status": "warning", "message": "シフトを生成できませんでした"}
            
    except Exception as e:
        api_logger.error(f"全体エラー: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.get("/preview-convert", response_class=HTMLResponse)
async def preview_convert():
    """変換結果をプレビュー表示"""
    try:
        db = get_firestore_client()
        doc_ref = db.collection('requests').document('que')
        doc = doc_ref.get()
        
        if doc.exists:
            response_data = doc.to_dict()
            if 'json' in response_data:
                input_data = response_data['json']
                
                # 各種データの変換
                rule_data = convert_rule_data(input_data.get('ruleData', {}))
                staff_data = convert_staffdata(input_data.get('staffData', {}))
                shift_data = convert_shiftdata(
                    input_data.get('shiftData', {}),
                    input_data.get('staffData', {}),
                    input_data.get('ruleData', {})
                )
                weight_data = convert_weightdata(input_data)
                
                # HTML形式で整形
                html_content = f"""
                <html>
                    <head>
                        <title>データ変換プレビュー</title>
                        <style>
                            body {{
                                font-family: Arial, sans-serif;
                                margin: 20px;
                            }}
                            .container {{
                                display: flex;
                                gap: 20px;
                            }}
                            .data-section {{
                                flex: 1;
                            }}
                            pre {{
                                background: #f5f5f5;
                                padding: 15px;
                                border-radius: 5px;
                                overflow-x: auto;
                                white-space: pre-wrap;
                            }}
                            h2 {{
                                color: #333;
                                border-bottom: 2px solid #ddd;
                                padding-bottom: 5px;
                            }}
                        </style>
                    </head>
                    <body>
                        <h1>データ変換プレビュー</h1>
                        <div class="container">
                            <div class="data-section">
                                <h2>元のデータ</h2>
                                <pre>{json.dumps(input_data, indent=2, ensure_ascii=False)}</pre>
                            </div>
                            <div class="data-section">
                                <h2>変換後のデータ</h2>
                                <pre>{json.dumps({
                                    "ruleData": rule_data,
                                    "staffData": staff_data,
                                    "shiftData": shift_data,
                                    "weightData": weight_data
                                }, indent=2, ensure_ascii=False)}</pre>
                            </div>
                        </div>
                    </body>
                </html>
                """
                return html_content
                
        return "データが見つかりません"
        
    except Exception as e:
        
        return f"エラーが発生しました: {str(e)}"

__all__ = ['StaffData', 'ShiftEntry', 'ShiftData', 'RuleData'] 
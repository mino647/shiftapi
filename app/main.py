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
from .convert import StaffData, ShiftEntry, ShiftData, RuleData
from typing import List, Optional, Dict
from datetime import datetime
from google.cloud import firestore
from .generator import ShiftGenerator  # 既存のクラスをインポート

# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

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
    firestore_listener.start_listening()  # これだけでOK！

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
            # 2. データを変換
            response_data = doc.to_dict()
            if 'json' in response_data:
                input_data = response_data['json']
                rule_data = convert_rule_data(input_data['ruleData'])
                staff_data = convert_staffdata(input_data['staffData'])
                shift_data = convert_shiftdata(input_data['shiftData'], input_data['staffData'], input_data['ruleData'])
                weight_data = convert_weightdata(input_data)
                
                # シフト生成
                generator = ShiftGenerator(weights=weight_data)
                solution = generator.generate_shift(
                    staff_data_list=staff_data["staffs"],
                    rule_data=rule_data["rules"],
                    shift_data=shift_data,
                    turbo_mode=True
                )
                
                if solution:
                    # Firestoreに保存
                    result_id = write_result_to_firestore(solution, input_data)
                    return {"status": "success", "result_id": result_id}
            
        return {"status": "error", "message": "データが見つかりません"}
        
    except Exception as e:
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
    """シフト生成テスト用エンドポイント"""
    try:
        print("=== ShiftGenerator確認 ===")
        try:
            from .generator import ShiftGenerator
            print("ShiftGeneratorクラス:", ShiftGenerator)  # クラスが正しくインポートされているか確認
        except ImportError as e:
            print("ShiftGeneratorのインポートエラー:", str(e))
            return {"status": "error", "message": f"ShiftGeneratorのインポートに失敗: {str(e)}"}
        
        # Step 1: Firestoreデータ取得のデバッグ
        print("=== Step 1: Firestore データ取得 ===")
        db = get_firestore_client()
        doc_ref = db.collection('requests').document('que')
        doc = doc_ref.get()
        
        if doc.exists:
            response_data = doc.to_dict()
            if 'json' in response_data:
                input_data = response_data['json']
                print("取得したinput_data:", input_data.keys())
                
                # Step 2: データ変換のデバッグ
                print("\n=== Step 2: データ変換 ===")
                try:
                    rule_data = convert_rule_data(input_data['ruleData'])
                    print("rule_data変換完了:", rule_data.keys())
                except Exception as e:
                    print("rule_data変換エラー:", str(e))

                try:
                    staff_data = convert_staffdata(input_data['staffData'])
                    print("staff_data変換完了:", staff_data.keys())
                except Exception as e:
                    print("staff_data変換エラー:", str(e))

                try:
                    shift_data = convert_shiftdata(input_data['shiftData'], input_data['staffData'], input_data['ruleData'])
                    print("shift_data変換完了:", shift_data.keys() if isinstance(shift_data, dict) else "非辞書型")
                except Exception as e:
                    print("shift_data変換エラー:", str(e))

                try:
                    weight_data = convert_weightdata(input_data)
                    print("weight_data変換完了:", weight_data.keys())
                except Exception as e:
                    print("weight_data変換エラー:", str(e))

                # Step 3: ShiftGenerator実行のデバッグ
                print("\n=== Step 3: ShiftGenerator実行 ===")
                try:
                    print("\n=== ShiftGenerator初期化 ===")
                    generator = ShiftGenerator(weights=weight_data['選好'])
                    print("generator type:", type(generator))
                    print("generator methods:", dir(generator))
                    
                    print("\n=== generate_shift呼び出し直前 ===")
                    solution = generator.generate_shift(
                        staff_data_list=staff_data["staffs"],
                        rule_data=rule_data["rules"],
                        shift_data=shift_data,
                        turbo_mode=True
                    )
                    print("generate_shift呼び出し完了")
                except Exception as e:
                    print("ShiftGenerator処理エラー:", str(e))
                    import traceback
                    print("詳細なエラー情報:", traceback.format_exc())
                    raise e
                
                if solution is None:
                    print("\nソリューションがNullです")
                    return {
                        "status": "warning",
                        "message": "シフトを生成できませんでした。制約条件を確認してください。",
                        "debug_info": {
                            "rule_data": rule_data,
                            "staff_data": staff_data,
                            "shift_data": shift_data,
                            "weight_data": weight_data
                        }
                    }
                
                return {
                    "status": "success",
                    "solution": solution
                }
        
        return {"status": "error", "message": "データが見つかりません"}
        
    except Exception as e:
        print("全体エラー:", str(e))
        return {"status": "error", "message": str(e), "traceback": str(e.__traceback__)}

__all__ = ['StaffData', 'ShiftEntry', 'ShiftData', 'RuleData'] 
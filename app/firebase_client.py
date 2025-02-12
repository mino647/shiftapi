"""
Firestoreとの接続とリアルタイムリスナーの実装を行うモジュール
"""
from google.cloud import firestore
import firebase_admin
from firebase_admin import credentials, firestore
import logging
import os
from datetime import datetime
from app.convert import convert_rule_data, convert_staffdata, convert_shiftdata, convert_weightdata
from absl import logging as absl_logging
from google.cloud.firestore import SERVER_TIMESTAMP

# abseilのログ初期化
absl_logging.set_verbosity(absl_logging.INFO)
absl_logging.use_absl_handler()

logger = logging.getLogger(__name__)

def get_firestore_client():
    """Firestoreクライアントを取得する"""
    if not firebase_admin._apps:
        # プロジェクトルートからの絶対パスを構築
        current_dir = os.path.dirname(os.path.abspath(__file__))  # app/
        root_dir = os.path.dirname(current_dir)                   # api/
        cred_path = os.path.join(root_dir, 'credentials', 'serviceAccountKey.json')
        
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    return firestore.client()

class FirestoreListener:
    def __init__(self):
        self.db = get_firestore_client()
        self.watch = None
    
    def start_listening(self):
        """queドキュメントの監視を開始"""
        doc_ref = self.db.collection('requests').document('que')
        
        def on_snapshot(doc_snapshot, changes, read_time):
            if doc_snapshot:
                doc = doc_snapshot[0]
                data = doc.to_dict()
                if data and 'json' in data:
                    input_data = data['json']
                    # デバッグ出力を追加

                    
                    # staffDataとruleDataとshiftDataを取得
                    staff_data = input_data.get('staffData')
                    rule_data = input_data.get('ruleData')
                    shift_data = input_data.get('shiftData')
                    
                    if staff_data and rule_data and shift_data:
                        converted_data = {
                            "rules": convert_rule_data(rule_data)["rules"],
                            "staffs": convert_staffdata(staff_data)["staffs"],
                            "shifts": convert_shiftdata(shift_data, staff_data, rule_data),
                            "weights": convert_weightdata(input_data)  # weightDataを変換
                        }

        
        try:
            self.watch = doc_ref.on_snapshot(on_snapshot)
            logger.info("Firestoreリスナーを開始しました")
        except Exception as e:
            logger.error(f"リスナーの開始に失敗: {e}") 

def write_result_to_firestore(solution: dict, input_data: dict) -> str:
    """生成結果をFirestoreに保存する"""
    try:
        db = get_firestore_client()
        results_ref = db.collection('results')
        
        # ドキュメントID生成
        timestamp = datetime.now().strftime('%Y-%m%d-%H%M')
        doc_id = timestamp
        
        # 古い結果をチェック（10件以上ある場合）
        old_results = results_ref.order_by('created_at').limit(11).get()
        if len(old_results) >= 10:
            # 最も古いドキュメントのIDを使用
            doc_id = old_results[0].id
        
        # 結果を保存
        new_result = {
            'status': 'success',
            'created_at': SERVER_TIMESTAMP,
            'input': input_data,
            'shifts': solution
        }
        
        results_ref.document(doc_id).set(new_result)
        return doc_id
        
    except Exception as e:
        logger.error(f"Firestoreへの書き込みに失敗: {e}")
        raise 
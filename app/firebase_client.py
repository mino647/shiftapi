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
import requests
from google.cloud.firestore import transactional

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
            """queドキュメントが更新されたときの処理"""
            if doc_snapshot:
                doc = doc_snapshot[0]
                data = doc.to_dict()
                if data and 'json' in data:
                    try:
                        # GETからPOSTに変更
                        response = requests.post('http://localhost:8000/generate-shift')
                        logger.info(f"シフト生成実行: {response.status_code}")
                    except Exception as e:
                        logger.error(f"シフト生成呼び出しエラー: {str(e)}")

        
        try:
            self.watch = doc_ref.on_snapshot(on_snapshot)
            logger.info("Firestoreリスナーを開始しました")
        except Exception as e:
            logger.error(f"リスナーの開始に失敗: {e}") 

def write_result_to_firestore(solution, input_data: dict) -> str:
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
        
        # ShiftDataを必要な形式に変換
        shifts_dict = {}
        for entry in solution.entries:
            if entry.staff_name not in shifts_dict:
                shifts_dict[entry.staff_name] = [''] * 31
            shifts_dict[entry.staff_name][entry.day - 1] = entry.shift_type
        
        formatted_shifts = {
            'year': solution.year,
            'month': solution.month,
            'shifts': shifts_dict
        }
        
        new_result = {
            'status': 'success',
            'created_at': SERVER_TIMESTAMP,
            'edit': formatted_shifts  # editキーでラップしたシフトデータのみ
        }
        
        results_ref.document(doc_id).set(new_result)
        return doc_id
        
    except Exception as e:
        logger.error(f"Firestoreへの書き込みに失敗: {e}")
        raise 

def write_notification(message: str) -> None:
    """制約違反などの通知メッセージをFirestoreに保存する"""
    try:
        db = get_firestore_client()
        notifications_ref = db.collection('notifications').document('current')
        
        @transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            
            # 現在のIDを取得（ドキュメントが存在しない場合は0）
            current_id = 0
            if doc.exists:
                # 既存のフィールドから最大のIDを取得
                fields = doc.to_dict()
                if fields:
                    current_id = max(fields.get('id', 0), 0)
            
            # 新しいメッセージを追加
            new_id = current_id + 1
            transaction.set(doc_ref, {
                'id': new_id,
                'date': SERVER_TIMESTAMP,
                'msg': message
            })
        
        # トランザクションを実行
        transaction = db.transaction()
        update_in_transaction(transaction, notifications_ref)
        
        logger.info(f"通知を保存しました: {message}")
        
    except Exception as e:
        logger.error(f"通知の保存に失敗: {str(e)}") 
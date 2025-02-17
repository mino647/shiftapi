"""
Firestoreとの接続とリアルタイムリスナーの実装を行うモジュール
"""
from google.cloud import firestore
import firebase_admin
from firebase_admin import credentials, firestore
import logging
import os
from datetime import datetime, timezone, timedelta
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
        # アプリケーション起動時のタイムスタンプを保持
        self.start_time = datetime.now(timezone(timedelta(hours=9)))
    
    def start_listening(self):
        """queドキュメントの監視を開始"""
        doc_ref = self.db.collection('requests').document('que')
        
        def on_snapshot(doc_snapshot, changes, read_time):
            """queドキュメントが更新されたときの処理"""
            if not doc_snapshot:
                return

            doc = doc_snapshot[0]
            data = doc.to_dict()
            
            # ドキュメントの更新時刻を確認
            update_time = doc.update_time.astimezone(timezone(timedelta(hours=9)))
            
            # アプリケーション起動後の更新のみ処理
            if update_time <= self.start_time:
                logger.info("アプリケーション起動前のデータのため、処理をスキップします")
                return
                
            if data and 'json' in data:
                try:
                    logger.info("FastAPIへのリクエストを開始します")
                    response = requests.post('http://127.0.0.1:8000/generate-shift')
                    logger.info(f"FastAPIからのレスポンス受信: {response.status_code}")
                except Exception as e:
                    logger.error(f"シフト生成呼び出しエラー: {str(e)}")
        
        try:
            self.watch = doc_ref.on_snapshot(on_snapshot)
            logger.info("Firestoreリスナーを開始しました")
        except Exception as e:
            logger.error(f"リスナーの開始に失敗: {e}") 

class DebugFirestoreListener:
    """デバッグ用のFirestoreリスナー"""
    def __init__(self):
        self.db = get_firestore_client()
        self.debug_watch = None
        self.start_time = datetime.now(timezone(timedelta(hours=9)))
    
    def start_listening(self):
        """デバッグ用queドキュメントの監視を開始"""
        debug_ref = self.db.collection('debug').document('que')
        
        def on_debug_snapshot(doc_snapshot, changes, read_time):
            """デバッグ用queドキュメントが更新されたときの処理"""
            if not doc_snapshot:
                return

            doc = doc_snapshot[0]
            update_time = doc.update_time.astimezone(timezone(timedelta(hours=9)))
            
            if update_time <= self.start_time:
                logger.info("アプリケーション起動前のデータのため、処理をスキップします")
                return
                
            if doc_snapshot:
                doc = doc_snapshot[0]
                data = doc.to_dict()
                if data:
                    try:
                        response = requests.post('http://127.0.0.1:8000/debug-generate-shift')
                        logger.info(f"デバッグ用FastAPIからのレスポンス受信: {response.status_code}")
                    except Exception as e:
                        logger.error(f"デバッグ用シフト生成呼び出しエラー: {str(e)}")
        
        try:
            self.debug_watch = debug_ref.on_snapshot(on_debug_snapshot)
            logger.info("デバッグ用Firestoreリスナーを開始しました")
        except Exception as e:
            logger.error(f"デバッグ用リスナーの開始に失敗: {e}")

def write_result_to_firestore(solution, input_data: dict) -> str:
    """生成結果をFirestoreに保存する"""
    try:
        db = get_firestore_client()
        results_ref = db.collection('results')
        
        # 全ての結果を取得して古いものを削除
        all_results = results_ref.get()
        sorted_results = sorted(all_results, key=lambda x: x.id, reverse=True)  # IDで降順ソート
        if len(sorted_results) >= 10:
            # 10件を超える古いドキュメントを削除
            for old_doc in sorted_results[9:]:  # 最新の10件以外を削除
                old_doc.reference.delete()
        
        # ドキュメントID生成
        timestamp = datetime.now().strftime('%Y-%m%d-%H%M%S')  # %Sで秒を追加
        doc_id = timestamp
        
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
            current_data = doc.to_dict() or {'notifications': []}
            notifications = current_data.get('notifications', [])
            
            # JST (UTC+9) のタイムスタンプを生成
            jst = timezone(timedelta(hours=9))
            current_time = datetime.now(jst)
            
            notifications.append({
                'id': len(notifications) + 1,
                'date': current_time,
                'msg': message
            })
            
            if len(notifications) > 10:
                notifications = notifications[-10:]
            
            transaction.set(doc_ref, {
                'notifications': notifications
            })
        
        # トランザクションを実行
        transaction = db.transaction()
        update_in_transaction(transaction, notifications_ref)
        
        logger.info(f"通知を保存しました: {message}")
        
    except Exception as e:
        logger.error(f"通知の保存に失敗: {str(e)}") 

def write_solution_printer_log(message: str, reset: bool = False) -> None:
    """SolutionPrinterのログをFirestoreに保存する"""
    try:
        db = get_firestore_client()
        progress_ref = db.collection('progress').document('solutions')
        
        if reset:
            # 新しいセッション開始時は配列をリセット
            progress_ref.set({
                'solutions': []
            })
            logger.info("進捗ログをリセットしました")
            return
        
        # 新しいメッセージを追加
        jst = timezone(timedelta(hours=9))
        current_time = datetime.now(jst)
        
        # 現在の配列を取得（ドキュメントが存在しない場合は作成）
        doc = progress_ref.get()
        solutions = doc.to_dict().get('solutions', [])
        solutions.append({
            'id': len(solutions) + 1,
            'date': current_time,
            'msg': message
        })
        
        # 更新された配列をセット
        progress_ref.set({
            'solutions': solutions
        })
        
        logger.info(f"進捗を保存しました: {message}")
        
    except Exception as e:
        logger.error(f"進捗の保存に失敗: {str(e)}") 
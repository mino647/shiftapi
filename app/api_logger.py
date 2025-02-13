"""
FastAPI用ログ管理モジュール

APIサーバーのログを管理し、デバッグとトラブルシューティングを支援します。
"""

import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from datetime import datetime
import traceback

class APILogger:
    """APIサーバー用ログ管理クラス"""
    
    def __init__(self, name: str = "APIServer"):
        """ロガーの初期化
        
        Args:
            name: ロガーの名前
        """
        try:
            # 現在の作業ディレクトリを表示
            print(f"Current working directory: {os.getcwd()}")
            
            # プロジェクトのルートディレクトリを取得（シンプルに現在のディレクトリを使用）
            project_root = os.getcwd()
            print(f"Project root: {project_root}")

            # logsディレクトリのパスを設定（シンプルに直接指定）
            log_dir = os.path.join(project_root, 'logs')
            print(f"Attempting to create log directory at: {log_dir}")
            
            # ディレクトリ作成（存在確認付き）
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
                print(f"Created log directory: {log_dir}")
            else:
                print(f"Log directory already exists: {log_dir}")

            # ログファイルパスの設定（ファイル名を変更）
            current_date = datetime.now().strftime('%Y%m%d')
            log_file = os.path.join(log_dir, f'apidebug_{current_date}.log')
            print(f"Log file path: {log_file}")

            # ロガーの設定
            self.logger = logging.getLogger(name)
            self.logger.setLevel(logging.DEBUG)
            
            # 既存のハンドラをクリア
            if self.logger.handlers:
                print("Clearing existing handlers")
                self.logger.handlers = []
            
            self.logger.propagate = False

            # フォーマッターの設定
            formatter = logging.Formatter(
                '[%(asctime)s] %(levelname)s [%(name)s] %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )

            # ファイルハンドラの設定
            try:
                file_handler = RotatingFileHandler(
                    log_file,
                    maxBytes=1024*1024,
                    backupCount=5,
                    encoding='utf-8',
                    mode='a'  # 追加モードで開く
                )
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)
                print(f"Successfully added file handler for: {log_file}")
                
                # ファイルが書き込み可能か確認
                with open(log_file, 'a') as f:
                    f.write("Logger initialization test\n")
                print("Successfully wrote test message to log file")
                
            except Exception as e:
                print(f"Failed to setup file handler: {str(e)}")
                print("Traceback:")
                traceback.print_exc()

            # コンソールハンドラの設定
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.DEBUG)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
            print("Successfully added console handler")

            # 初期化完了ログ
            self.info(f"Logger initialized: {log_file}")
            print("Logger initialization completed")

        except Exception as e:
            print(f"Error during logger initialization: {str(e)}")
            print("Traceback:")
            traceback.print_exc()

    def debug(self, message: str) -> None:
        """デバッグログを記録"""
        self.logger.debug(message)
        print(f"DEBUG: {message}")  # コンソールにも出力

    def info(self, message: str) -> None:
        """情報ログを記録"""
        self.logger.info(message)
        print(f"INFO: {message}")  # コンソールにも出力

    def warning(self, message: str) -> None:
        """警告ログを記録"""
        self.logger.warning(message)
        print(f"WARNING: {message}")  # コンソールにも出力

    def error(self, message: str) -> None:
        """エラーログを記録"""
        self.logger.error(message)
        print(f"ERROR: {message}")  # コンソールにも出力

    def critical(self, message: str) -> None:
        """重大エラーログを記録"""
        self.logger.critical(message)
        print(f"CRITICAL: {message}")  # コンソールにも出力

# シングルトンインスタンスの作成
try:
    print("Creating APILogger instance...")
    api_logger = APILogger()
    print("APILogger instance created successfully")
except Exception as e:
    print(f"Failed to create APILogger instance: {str(e)}")
    traceback.print_exc() 
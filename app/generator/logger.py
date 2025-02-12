"""
ログ管理モジュール

アプリケーション全体のログを管理し、デバッグとトラブルシューティングを支援します。
"""

import logging
from logging.handlers import RotatingFileHandler
import os
from datetime import datetime
from typing import Optional, NoReturn, Callable, Any
import functools
import sys

class ProgressDialogHandler(logging.Handler):
    """ProgressDialog用のカスタムログハンドラー"""
    def __init__(self, progress_dialog):
        super().__init__()
        self.progress_dialog = progress_dialog
        self.setLevel(logging.INFO)

    def emit(self, record):
        if record.levelno >= logging.INFO:
            self.progress_dialog.append_message(self.format(record))

class Logger:
    """ログ管理クラス"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize_logger()
        return cls._instance
    
    def _initialize_logger(self) -> None:
        """ロガーの初期化"""
        try:
            # アプリケーションのベースパスを取得
            if getattr(sys, 'frozen', False):
                base_path = os.path.dirname(sys.executable)
            else:
                base_path = os.path.dirname(os.path.dirname(__file__))  # srcディレクトリを取得
            
            # ログディレクトリのパスを設定
            log_dir = os.path.join(base_path, 'logs')
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            
            # ログファイルのパスを設定
            log_file = os.path.join(log_dir, 'debug.log')
            
            # ロガーの設定
            self.logger = logging.getLogger('ShiftScheduler')
            self.logger.setLevel(logging.DEBUG)
            
            # ファイルハンドラの設定（サイズベースでローテーション）
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=1024*1024,  # 1MB
                backupCount=5,
                encoding='utf-8'
            )
            file_handler.setLevel(logging.DEBUG)
            
            # コンソールハンドラの設定
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            
            # フォーマッターの設定
            formatter = logging.Formatter(
                '[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            
            # ハンドラの追加
            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)
        except OSError as e:
            print(f'ログディレクトリの作成に失敗: {e}')
            return None
    
    def debug(self, message: str) -> None:
        """デバッグログを記録
        
        Args:
            message: ログメッセージ
        """
        self.logger.debug(message)
    
    def info(self, message: str) -> None:
        """情報ログを記録
        
        Args:
            message: ログメッセージ
        """
        self.logger.info(message)
    
    def warning(self, message: str) -> None:
        """警告ログを記録
        
        Args:
            message: ログメッセージ
        """
        self.logger.warning(message)
    
    def error(self, message: str) -> None:
        """エラーログを記録
        
        Args:
            message: ログメッセージ
        """
        self.logger.error(message)
    
    def critical(self, message: str) -> None:
        """重大エラーログを記録
        
        Args:
            message: ログメッセージ
        """
        self.logger.critical(message)
    
    def add_progress_handler(self, progress_dialog):
        """ProgressDialog用のハンドラーを追加"""
        handler = ProgressDialogHandler(progress_dialog)
        handler.setFormatter(logging.Formatter('%(message)s'))
        self.logger.addHandler(handler)
        return handler
    
    def remove_progress_handler(self, handler):
        """ProgressDialog用のハンドラーを削除"""
        self.logger.removeHandler(handler)
    
    def cleanup(self) -> None:
        """ロガーのクリーンアップ処理"""
        if hasattr(self, 'logger'):
            for handler in self.logger.handlers[:]:
                handler.close()
                self.logger.removeHandler(handler)

def log_function(func: Callable) -> Callable:
    """関数の実行をログに記録するデコレータ
    
    Args:
        func: ログを取る対象の関数
    
    Returns:
        ラップされた関数
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        logger = Logger()
        func_name = func.__name__
        
        # 引数のログ
        args_repr = [repr(a) for a in args]
        kwargs_repr = [f"{k}={v!r}" for k, v in kwargs.items()]
        signature = ", ".join(args_repr + kwargs_repr)
        
        logger.debug(f"関数 {func_name} が呼び出されました。引数: {signature}")
        
        try:
            # 関数の実行
            result = func(*args, **kwargs)
            
            # 戻り値のログ（必要に応じて）
            logger.debug(f"関数 {func_name} が正常終了しました。戻り値: {result}")
            return result
            
        except Exception as e:
            # エラーのログ
            logger.error(f"関数 {func_name} でエラーが発生: {str(e)}")
            raise
    
    return wrapper

def log_class(cls: Any) -> Any:
    """クラスの全メソッドをログに記録するデコレータ
    
    Args:
        cls: ログを取る対象のクラス
    
    Returns:
        ラップされたクラス
    """
    for attr_name, attr_value in vars(cls).items():
        if callable(attr_value) and not attr_name.startswith('__'):
            setattr(cls, attr_name, log_function(attr_value))
    return cls

# グローバルなロガーインスタンス
logger = Logger()

# 使用例
if __name__ == '__main__':
    # 関数デコレータの使用例
    @log_function
    def example_function(x: int, y: int) -> int:
        return x + y
    
    # クラスデコレータの使用例
    @log_class
    class ExampleClass:
        def method1(self) -> str:
            return "Hello"
        
        def method2(self, x: int) -> int:
            return x * 2
import time
import requests
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s.%(msecs)03d %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

def test_request():
    logger.info("リクエスト開始")
    start = time.time()
    
    response = requests.post("http://127.0.0.1:8000/generate-shift")
    
    elapsed = time.time() - start
    logger.info(f"リクエスト完了: {elapsed:.3f}秒")
    logger.info(f"ステータス: {response.status_code}")

if __name__ == "__main__":
    test_request() 
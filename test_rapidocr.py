import numpy as np
from vision_service import _get_ocr_engine, _run_ocr

img = np.zeros((100, 100, 3), dtype=np.uint8)
# what if we feed something that causes an issue?
# Actually, the user specifically mentioned "a different language".
# RapidOCR on a different language text. I need to make an image with non-English text.
import cv2
img2 = np.zeros((100, 400, 3), dtype=np.uint8)
img2.fill(255)
# I don't have non-English fonts easily available to write on cv2. 
# But wait... why would different language get stuck in the queue?
# "it's just staying still in the queue" means the tier2_loop is blocked!

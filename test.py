# import pytesseract
# from PIL import Image, ImageFilter

# # 打开图片文件
# image = Image.open("rk.jpg")

# # 转为灰度图像
# gray_image = image.convert("L")

# # 二值化（阈值处理）
# threshold_image = gray_image.point(lambda p: p > 128 and 255)

# # 使用 Tesseract 进行 OCR 识别
# text = pytesseract.image_to_string(threshold_image)

# # 输出识别的文本
# print("识别到的文本：")
# print(text)



# import pytesseract
# from PIL import Image

# # 配置 Tesseract 的路径（根据你的安装位置修改路径）
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# # 读取图片
# image = Image.open('rk.jpg')

# # 提取图片中的文本
# text = pytesseract.image_to_string(image)

# print(text)


# import pytesseract
# from PIL import Image

# # 配置 Tesseract 路径（如果需要的话）
# pytesseract.pytesseract.tesseract_cmd = r'/usr/bin/tesseract'  # 替换为实际的 Tesseract 安装路径

# # 载入图片
# image_path = 'rk.jpg'  # 替换为你自己的图片路径
# image = Image.open(image_path)

# # 直接进行 OCR 识别
# text = pytesseract.image_to_string(image)

# # 打印识别的文本
# print("OCR 结果：")
# print(text)
import cv2
import os
from datetime import datetime

# 创建保存目录 ~/ocr
save_dir = os.path.expanduser("~/ocr")
os.makedirs(save_dir, exist_ok=True)

# 摄像头设备号 (默认使用 /dev/video0)
camera_index = 0

# 打开摄像头
cap = cv2.VideoCapture(camera_index)
if not cap.isOpened():
    print("无法打开摄像头！")
    exit()

# 获取摄像头支持的分辨率
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print(f"摄像头分辨率: {width}x{height}")

# 设置窗口大小与图像一致
cv2.namedWindow("摄像头实时画面", cv2.WINDOW_NORMAL)
cv2.resizeWindow("摄像头实时画面", width, height)

print("按 'p' 拍照，按 'q' 退出程序。")

while True:
    # 捕获一帧图像
    ret, frame = cap.read()
    if not ret:
        print("无法读取摄像头画面！")
        break

    # 显示画面
    cv2.imshow("摄像头实时画面", frame)

    # 检测按键事件
    key = cv2.waitKey(2) & 0xFF
    if key == ord('p'):  # 按 'p' 拍照
        # 生成唯一文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(save_dir, f"photo_{timestamp}.jpg")
        success = cv2.imwrite(save_path, frame)
        
        # 打印保存信息
        if success:
            print(f"照片已成功保存到 {save_path}")
        else:
            print("保存照片失败，请检查路径或文件权限。")
            
    elif key == ord('q'):  # 按 'q' 退出
        print("退出程序。")
        break

# 释放资源
cap.release()
cv2.destroyAllWindows()


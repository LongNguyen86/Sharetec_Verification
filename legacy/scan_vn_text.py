import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
EXCLUDE_DIRS = {"venv", ".venv", "__pycache__", ".git", "workspace"}

# Biểu thức chính quy chứa toàn bộ ký tự có dấu của Tiếng Việt
VN_CHARS_REGEX = re.compile(
    r'[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]', 
    re.IGNORECASE
)

def scan_file(file_path: Path):
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
        found = False
        for i, line in enumerate(lines):
            # Nếu tìm thấy ký tự tiếng Việt trong dòng
            if VN_CHARS_REGEX.search(line):
                if not found:
                    print(f"\n📄 File: {file_path.relative_to(BASE_DIR)}")
                    found = True
                print(f"   - Dòng {i+1}: {line.strip()}")
    except Exception as e:
        print(f"⚠️ Lỗi khi đọc file {file_path.name}: {e}")

def main():
    print("🔍 ĐANG QUÉT CÁC CÂU TIẾNG VIỆT CÒN SÓT LẠI TRONG PROJECT...\n")
    count_files = 0
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for file in files:
            if file.endswith(".py") and file not in ["translate_all.py", "scan_vn_text.py"]:
                scan_file(Path(root) / file)
                count_files += 1

    print(f"\n✅ Đã quét xong {count_files} file Python!")

if __name__ == "__main__":
    main()
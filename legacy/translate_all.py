import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Danh sách "vét máng" các cụm từ còn sót lại do dính f-string hoặc ký tự xuống dòng
DICTIONARY = {
    # main_ingest.py
    "Đang tải SharePoint Path:": "Fetching SharePoint Path:",
    
    # main_validate_mapping.py
    " lỗi\\n": " errors\\n",
    " lỗi\"": " errors\"",
    "các file Mapping.": "Mapping files.",
    "Chi tiết báo cáo tổng hợp đã được xuất ra file:": "Detailed aggregate report has been exported to file:",
    
    # io_engine/sharepoint_client.py
    "mà bắt đăng nhập lại.": "and forced login.",
    "Hệ thống đã tự động xóa Session cũ. Anh hãy CHẠY LẠI LỆNH TRÊN N8N MỘT LẦN NỮA để mở trình duyệt nhé!": "System automatically deleted old Session. PLEASE RE-RUN THE COMMAND ON n8n to open the browser again!",
    "Đang tải file lẻ qua Playwright API:": "Downloading individual file via Playwright API:",
    "Không tải được files.": "Failed to download files.",
    "Không tải được file.": "Failed to download file.",
    
    # Đề phòng các từ lẻ khác
    " lỗi": " errors",
    "HỢP LỆ": "VALID",
    "TỔNG KẾT": "SUMMARY",
}

EXCLUDE_DIRS = {"venv", ".venv", "__pycache__", ".git", "workspace"}

def translate_file(file_path: Path):
    try:
        content = file_path.read_text(encoding="utf-8")
        modified = False

        for vn, en in DICTIONARY.items():
            if vn in content:
                content = content.replace(vn, en)
                modified = True

        if modified:
            file_path.write_text(content, encoding="utf-8")
            print(f"✅ Vét sạch tiếng Việt tại: {file_path.relative_to(BASE_DIR)}")

    except Exception as e:
        print(f"❌ Error processing {file_path.name}: {e}")

def main():
    print("🚀 Auto-translating remaining Vietnamese strings...\n")
    
    count = 0
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        for file in files:
            if file.endswith(".py") and file not in ["translate_all.py", "scan_vn_text.py"]:
                translate_file(Path(root) / file)
                count += 1

    print(f"\n🎉 Done! Running scanner to verify...")
    
    # Tự động import và chạy lại file scan luôn cho anh xem
    try:
        import legacy.scan_vn_text as scan_vn_text
        print("\n" + "="*50)
        scan_vn_text.main()
    except Exception:
        pass

if __name__ == "__main__":
    main()
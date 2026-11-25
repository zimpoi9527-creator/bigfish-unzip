# Copyright (c) 2025 TR-Supowe，MIT License
# 控制台版入口（改造版）。核心逻辑在 unzip_core.py，与图形界面共用。

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unzip_core as core

if os.environ.get('UNZIP_FORCE_UTF8'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


class ConsoleReporter(core.Reporter):
    def info(self, msg):
        print(f"[INFO] {msg}")

    def success(self, msg):
        print(f"[SUCCESS] {msg}")

    def error(self, msg):
        print(f"[ERROR] {msg}")

    def warn(self, msg):
        print(f"[WARN] {msg}")

    def debug(self, msg):
        print(f"[DEBUG] {msg}")

    def stage(self, title):
        print(f"\n--- {title} ---")

    def task(self, index, total, name):
        print("\n" + "#" * 20 + f" 总任务 {index}/{total}: {name} " + "#" * 20)

    def rename(self, original, normalized, note=''):
        print(f"[INFO] {note or '改名'}: {original} -> {normalized}")


def get_application_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_7z_executable_path():
    import shutil
    base = get_application_path()
    for candidate in (base, os.path.dirname(base)):
        bundled = os.path.join(candidate, '7z', '7z.exe')
        if os.path.exists(bundled):
            return bundled
    return shutil.which("7z")


def select_password_from_library(app_dir):
    lib_path = os.path.join(app_dir, 'passwords')
    if not os.path.isdir(lib_path):
        os.makedirs(lib_path)
    print("\n--- 密码库选择 ---")
    try:
        txt_files = sorted([f for f in os.listdir(lib_path) if f.lower().endswith('.txt')])
        if not txt_files:
            print("[WARN] 密码库为空，没有任何 .txt 文件。")
            return []
        print("发现以下密码本:")
        for i, filename in enumerate(txt_files):
            if i >= 10:
                print(f"      ... (及其他 {len(txt_files) - 10} 个文件)")
                break
            print(f"  [{i}] {filename}")
        while True:
            choice = input(f"请输入要使用的密码本编号 (0-{min(len(txt_files) - 1, 9)}): ")
            if choice.isdigit() and 0 <= int(choice) < min(len(txt_files), 10):
                selected = os.path.join(lib_path, txt_files[int(choice)])
                with open(selected, 'r', encoding='utf-8') as f:
                    passwords = [line.strip() for line in f if line.strip()]
                print(f"[INFO] 成功从 '{os.path.basename(selected)}' 加载 {len(passwords)} 个密码。")
                return passwords
            print("[ERROR] 输入无效，请输入列表中的正确编号。")
    except Exception as e:
        print(f"[ERROR] 访问密码库时出错: {e}")
        return []


def main():
    print("=" * 60)
    print(" 大肥鱼解压 v1.2.2 - 多层/加密/伪装压缩包一键解压工具")
    print(" 任意后缀识别 · 分卷智能拼合 · 深度扫描伪装包 · 二级嵌套 · 失败原因报告")
    print("=" * 60)
    if len(sys.argv) < 2:
        print("[ERROR] 使用方法: 请将一个或多个文件/文件夹拖拽到本程序的 .bat 启动脚本上。")
        input("按 Enter 键退出...")
        return

    app_dir = get_application_path()
    seven = get_7z_executable_path()
    if not seven:
        print("[ERROR] 7-Zip程序未找到，请检查配置或系统PATH环境变量。")
        input("按 Enter 键退出...")
        return
    print(f"[INFO] 使用 7-Zip 程序于: {seven}")

    passwords = []
    try:
        password_str = input("[INPUT] 请输入密码 (直接回车可使用密码库或空密码): ")
        if password_str:
            passwords = [password_str]
        else:
            choice = input(" -> 请选择操作: [1] 使用空密码解压 [2] 从密码库选择密码 (默认1): ")
            passwords = select_password_from_library(app_dir) if choice == '2' else [""]
    except Exception as e:
        print(f"[ERROR] 获取密码时发生错误: {e}")
        input("按 Enter 键退出...")
        return

    # 可选参数：--out DIR  --no-separate  --delete-source
    args = sys.argv[1:]
    out_dir, separate, delete_src, recursive, paths = None, True, False, True, []
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--out' and i + 1 < len(args):
            out_dir = args[i + 1]; i += 2; continue
        if a.startswith('--out='):
            out_dir = a.split('=', 1)[1]; i += 1; continue
        if a == '--no-separate':
            separate = False; i += 1; continue
        if a == '--delete-source':
            delete_src = True; i += 1; continue
        if a == '--no-recursive':
            recursive = False; i += 1; continue
        paths.append(a); i += 1

    print(f"\n[INFO] 检测到 {len(paths)} 个拖拽项目，开始处理...")
    print(f"[INFO] 解压位置: {out_dir or '压缩包所在目录'} | "
          f"{'单独文件夹' if separate else '直接放入'} | "
          f"{'完成后删除源包' if delete_src else '保留源包'} | "
          f"{'解压二级压缩' if recursive else '仅解第一层'}")
    engine = core.Engine(passwords, seven, app_dir, reporter=ConsoleReporter(),
                         output_dir=out_dir, separate_folder=separate,
                         delete_source=delete_src, recursive=recursive)
    stats = engine.run(paths)
    print("\n" + "#" * 25 + " 所有任务处理完毕 " + "#" * 25)
    print(f"[SUMMARY] 成功 {stats['ok']} 个，失败 {stats['fail']} 个，"
          f"二级压缩 {stats['nested']} 个，删除源包 {stats['deleted']} 个")
    input("按 Enter 键退出...")


if __name__ == '__main__':
    main()

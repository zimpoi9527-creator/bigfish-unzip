# Copyright (c) 2025 TR-Supowe，MIT License
# 改造：任意后缀 + 分卷智能识别。本文件是 GUI / CLI 共用的解压引擎。
#
# 设计要点：
#   · 一律按文件头魔数判断格式，扩展名不参与决策；
#   · 分卷先聚合成集合再解压，第一卷/全部卷被改名都能认；
#   · 分卷通过「影子目录 + 硬链接」拼成 001/002…，不复制、不改动源文件；
#   · 先按普通压缩包解，失败才尝试分卷拼合，正常包不会被误判；
#   · 通过 Reporter 回调把阶段/改名/百分比推给界面。

import os
import re
import shutil
import subprocess
import tempfile
import time
import random
import string
from collections import deque

# --------------------------------------------------------------------------
# 内容嗅探
# --------------------------------------------------------------------------
ARCHIVE_MAGICS = [
    (b'PK\x03\x04', 'zip'),
    (b'PK\x05\x06', 'zip-empty'),
    (b'PK\x07\x08', 'zip-spanned'),
    (b'7z\xbc\xaf\x27\x1c', '7z'),
    (b'Rar!\x1a\x07\x01\x00', 'rar5'),
    (b'Rar!\x1a\x07\x00', 'rar4'),
    (b'\x1f\x8b\x08', 'gzip'),
    (b'BZh', 'bzip2'),
    (b'\xfd7zXZ\x00', 'xz'),
    (b'\x28\xb5\x2f\xfd', 'zstd'),
    (b'MSCF', 'cab'),
    (b'ITSF', 'chm'),
]


def read_head(path, size=8):
    try:
        with open(path, 'rb') as f:
            return f.read(size)
    except OSError:
        return b''


def sniff_archive(path):
    """按文件头魔数判断压缩格式；不看扩展名。返回格式名或 None。"""
    head = read_head(path, 8)
    if not head:
        return None
    for magic, kind in ARCHIVE_MAGICS:
        if head.startswith(magic):
            return kind
    return None


# 魔数 -> 规范扩展名（用于把伪装包改名成真实后缀）
EXT_BY_KIND = {
    'zip': '.zip',
    'zip-empty': '.zip',
    'zip-spanned': '.zip',
    '7z': '.7z',
    'rar4': '.rar',
    'rar5': '.rar',
    'gzip': '.gz',
    'bzip2': '.bz2',
    'xz': '.xz',
    'zstd': '.zst',
    'cab': '.cab',
}

# 本身就是 ZIP 容器的“正常格式”和可执行文件，绝不能当伪装包改名
SKIP_RENAME_EXT = {
    '.zip', '.7z', '.rar', '.gz', '.tgz', '.bz2', '.xz', '.zst', '.cab',
    '.docx', '.docm', '.dotx', '.xlsx', '.xlsm', '.xltx',
    '.pptx', '.pptm', '.potx', '.odt', '.ods', '.odp', '.epub',
    '.jar', '.apk', '.whl', '.nupkg', '.vsix', '.ipa', '.cbz', '.xpi',
    '.exe', '.dll', '.msi', '.sys',
}


# --------------------------------------------------------------------------
# 分卷命名
# --------------------------------------------------------------------------
SPLIT_TAIL_RE = re.compile(
    r'^(?P<base>.+?)'
    r'(?P<tail>\.(?:'
    r'part0*(?P<pnum>\d{1,3})(?:\.rar)?'
    r'|[zr](?P<znum>\d{2,3})'
    r'|0{2,3}(?P<num>[1-9]\d{0,5})'
    r'))$',
    re.IGNORECASE,
)

INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def parse_split_name(filename):
    """解析分卷文件名，返回 (base, index, kind) 或 None。"""
    m = SPLIT_TAIL_RE.match(filename)
    if not m:
        return None
    base = m.group('base')
    if not base:
        return None
    if m.group('pnum'):
        return base, int(m.group('pnum')), 'part'
    if m.group('znum'):
        z = m.group('znum')
        return base, int(z), 'z' if z.lower().startswith('z') else 'r'
    if m.group('num'):
        return base, int(m.group('num')), 'num'
    return None


def natural_key(text):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', text)]


def sanitize_base(name, fallback='archive'):
    name = INVALID_NAME_CHARS.sub('_', name).strip().rstrip('.')
    return name or fallback


def list_files(directory):
    try:
        return [os.path.join(directory, f) for f in sorted(os.listdir(directory))
                if os.path.isfile(os.path.join(directory, f))]
    except OSError:
        return []


def safe_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def size_compatible(anchor_size, part_sizes):
    """判断 anchor 的体积是否符合“某分卷组的第一卷”。

    分卷除最后一卷外体积都相同，所以第一卷体积 == 各组最大体积；
    只有两卷、且第二卷更小时，第一卷会略大 —— 放宽到 2 倍以内。
    """
    if anchor_size <= 0 or not part_sizes:
        return False
    mx = max(part_sizes)
    if mx <= 0:
        return False
    return mx <= anchor_size <= mx * 2


def name_key_for_unknown(path):
    """给“无编号”的候选卷排序：名字里的数字 > 自然序 > 修改时间。"""
    base = os.path.basename(path)
    m = re.search(r'(\d{1,6})(?=\D*$)', base)
    return (
        0 if m else 1,
        int(m.group(1)) if m else 0,
        natural_key(base),
        os.path.getmtime(path) if os.path.exists(path) else 0,
    )


def _named_groups(directory):
    groups = {}
    for p in list_files(directory):
        info = parse_split_name(os.path.basename(p))
        if info:
            base, idx, _kind = info
            groups.setdefault(base.lower(), {}).setdefault(idx, p)
    return groups


def _attach_missing_first(directory, base_lower, members, taken):
    """缺第一卷时，把伪装成其它后缀的第一卷找出来补进去。

    线索 a：去掉最后一个扩展名后等于分卷基名（a.jpg -> a.002…）
    线索 b：组里唯一带压缩头魔数、且体积与已知分卷一致的文件
    """
    if 1 in members:
        return members
    grouped = set(taken) | {p for p in members.values()}
    all_files = list_files(directory)

    cands = []
    for p in all_files:
        if p in grouped or parse_split_name(os.path.basename(p)):
            continue
        if os.path.splitext(os.path.basename(p))[0].lower() == base_lower:
            cands.append(p)

    if not cands:
        sizes = {safe_size(p) for p in members.values()}
        for p in all_files:
            if p in grouped or parse_split_name(os.path.basename(p)):
                continue
            if sniff_archive(p) and size_compatible(safe_size(p), sizes):
                cands.append(p)

    if len(cands) == 1:
        members[1] = cands[0]
    return members


def collect_split_group(anchor_path):
    """判断 anchor 是否属于某个分卷集合；返回 [(index, path)] 或 None。"""
    anchor = os.path.abspath(anchor_path)
    if not os.path.isfile(anchor):
        return None
    directory, filename = os.path.dirname(anchor), os.path.basename(anchor)
    groups = _named_groups(directory)
    all_grouped = {p for mem in groups.values() for p in mem.values()}
    anchor_is_archive = bool(sniff_archive(anchor))

    def _finish(members):
        if len(members) >= 2 and 1 in members:
            return sorted(members.items())
        return None

    info = parse_split_name(filename)
    if info:  # 情形 1：自己带分卷编号
        base_lower = info[0].lower()
        members = dict(groups.get(base_lower, {}))
        members[info[1]] = anchor
        members = _attach_missing_first(directory, base_lower, members, all_grouped)
        result = _finish(members)
        if result:
            return result

    stem = os.path.splitext(filename)[0].lower()
    if stem in groups:  # 情形 2：同基名分卷存在，自己是缺号那卷
        members = dict(groups[stem])
        members[1] = anchor
        result = _finish(_attach_missing_first(directory, stem, members, all_grouped))
        if result:
            return result

    if not anchor_is_archive:
        return None

    anchor_size = safe_size(anchor)

    # 情形 3：自己是某个“缺第一卷”的分卷组的第一卷（名字完全不同）
    for base_lower, mem in groups.items():
        if 1 in mem or not mem:
            continue
        if size_compatible(anchor_size, {safe_size(p) for p in mem.values()}):
            members = dict(mem)
            members[1] = anchor
            result = _finish(members)
            if result:
                return result

    # 情形 4：全部卷被改成任意后缀，自己是唯一带压缩头的文件
    if anchor_size <= 0:
        return None
    cands = []
    for p in list_files(directory):
        if p == anchor or p in all_grouped:
            continue
        size = safe_size(p)
        if size < 512 or size > anchor_size:
            continue
        if sniff_archive(p):
            continue
        cands.append(p)
    if not cands:
        return None
    cands.sort(key=name_key_for_unknown)
    members = {1: anchor}
    for idx, p in enumerate(cands, start=2):
        members[idx] = p
    return sorted(members.items())


def scan_directory_for_sets(directory):
    """返回 (sets, singles)：sets 为按序分卷集合列表，singles 为其余文件。"""
    files = list_files(directory)
    groups = _named_groups(directory)
    taken = set()
    sets = []

    for base_lower in sorted(groups):
        members = dict(groups[base_lower])
        if not members:
            continue
        members = _attach_missing_first(directory, base_lower, members, taken)
        if len(members) < 2 or 1 not in members:
            continue
        ordered = sorted(members.items())
        sets.append(ordered)
        taken.update(p for _i, p in ordered)

    rest = [p for p in files if p not in taken]
    by_size = {}
    for p in rest:
        by_size.setdefault(safe_size(p), []).append(p)
    for size, grp in sorted(by_size.items(), key=lambda kv: -kv[0]):
        if size <= 0 or len(grp) < 2:
            continue
        magics = [p for p in grp if sniff_archive(p)]
        if len(magics) != 1:
            continue
        anchor = magics[0]
        others = sorted([p for p in grp if p != anchor], key=name_key_for_unknown)
        ordered = [(1, anchor)] + [(i, p) for i, p in enumerate(others, start=2)]
        sets.append(ordered)
        taken.update(grp)

    singles = [p for p in files if p not in taken]
    return sets, singles


def build_shadow_dir(members, hint_dir, source_name):
    """用硬链接把分卷映射成 <base>.001/.002…，返回 (影子目录, 入口路径)。"""
    base = sanitize_base(os.path.splitext(source_name)[0])
    if not members or members[0][0] != 1:
        return None, None
    attempts = []
    try:
        attempts.append(tempfile.mkdtemp(prefix='.unzip_shadow_', dir=hint_dir))
    except OSError:
        pass
    try:
        attempts.append(tempfile.mkdtemp(prefix='unzip_shadow_'))
    except OSError:
        pass
    if not attempts:
        return None, None

    for shadow_dir in attempts:
        ok = True
        for idx, src in members:
            dst = os.path.join(shadow_dir, f'{base}.{idx:03d}')
            try:
                if not os.path.exists(dst):
                    os.link(src, dst)
            except OSError:
                try:
                    if not os.path.exists(dst):
                        shutil.copy2(src, dst)
                except OSError:
                    ok = False
                    break
        if ok:
            return shadow_dir, os.path.join(shadow_dir, f'{base}.001')
        shutil.rmtree(shadow_dir, ignore_errors=True)
    return None, None



# --------------------------------------------------------------------------
# 解压失败原因诊断：把 7-Zip 的原始输出归类成人话 + 处理建议
# --------------------------------------------------------------------------
FAIL_RULES = [
    (("wrong password", "cannot open encrypted archive",
      "data error in encrypted file"),
     "密码错误",
     "把正确密码填进密码框；密码多的话写进 passwords/0_temp.txt 并勾选“使用密码库”"),

    (("crc failed", "data error"),
     "数据校验失败（CRC 不匹配）",
     "压缩包已损坏，或分卷缺卷/顺序错乱；建议重新下载，或换用带恢复记录的副本"),

    (("unexpected end of archive", "headers error"),
     "压缩包不完整（内容被截断或缺少分卷）",
     "确认所有分卷都在同一目录、编号连续；单文件的话可能没下载完"),

    (("is not archive", "cannot open file as archive"),
     "不是有效的压缩包",
     "文件可能只是改了后缀；也可能是其他格式（如某些特殊加密包），可换 7-Zip 官方版确认"),

    (("unsupported method", "unsupported compression"),
     "压缩算法不受支持",
     "换用更新版本的 7-Zip，或用该格式的官方客户端解开"),

    (("access is denied", "cannot open the file for writing"),
     "没有读写权限",
     "把文件复制到别的目录再试，或用管理员身份运行本程序"),

    (("cannot find the file", "system cannot find the file",
      "no such file"),
     "找不到文件或缺失分卷",
     "检查分卷是否齐全、处理过程中有没有被移动或删除"),

    (("not enough space", "no space left", "disk is full"),
     "磁盘空间不足",
     "清理目标磁盘，或换一个解压位置"),

    (("cannot allocate memory", "out of memory"),
     "内存不足",
     "关掉一些程序，或分批解压"),

    (("being used by another process",),
     "文件被其他程序占用",
     "关掉正在打开该文件的程序（杀毒、编辑器等）后重试"),
]


def diagnose_failure(raw):
    """返回 (原因, 建议)。raw 是 7-Zip 的原始输出。

    归一化：小写、压空格、can not -> cannot、去掉 the，
    这样不同版本 7-Zip 的措辞差异都能命中同一类。
    """
    low = re.sub(r"\s+", " ", (raw or "").lower())
    low = low.replace("can not", "cannot").replace("could not", "cannot")
    low = low.replace("the ", "")
    for keys, category, advice in FAIL_RULES:
        if any(k in low for k in keys):
            return category, advice
    return ("未知错误（详见原始输出）",
            "把日志里 7-Zip 的原始输出发出来，或换个 7-Zip 版本再试")


def summarize_7z_output(raw, max_lines=3, max_len=300):
    """从 7-Zip 输出里挑几条有信息量的行，用于日志/报告。"""
    keep = []
    for line in (raw or "").splitlines():
        t = line.strip()
        if not t:
            continue
        low = t.lower()
        if low.startswith(("7-zip", "scanning", "extracting", "creating",
                           "everything is ok", "files:", "folders:", "size:",
                           "compressed:", "errors:", "warnings:", "archives:",
                           "sub items", "open errors", "processing archive")):
            continue
        if (t.startswith(("ERROR:", "Error:", "ERRORS:", "WARNING:"))
                or any(k in low for k in ("error", "unexpected", "crc",
                                          "cannot", "unsupported", "wrong password"))):
            keep.append(t)
    if not keep:
        keep = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()][-max_lines:]
    text = " | ".join(keep[-max_lines:])
    return text[:max_len]

# --------------------------------------------------------------------------
# 回调基类
# --------------------------------------------------------------------------
class Reporter:
    """引擎事件回调。GUI / 控制台各实现一份。"""

    def info(self, msg):
        pass

    def success(self, msg):
        pass

    def error(self, msg):
        pass

    def warn(self, msg):
        pass

    def debug(self, msg):
        pass

    def stage(self, title):
        """大阶段标题，例如：识别分卷 / 解压 / 扫描产物。"""
        pass

    def task(self, index, total, name):
        """第 index 个任务（从 1 开始）。"""
        pass

    def rename(self, original, normalized, note=''):
        """分卷映射/改名事件，用于界面右侧“改名”列表。"""
        pass

    def progress(self, percent, label=''):
        """0~100 的总体进度。"""
        pass


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------
class Engine:
    def __init__(self, passwords, seven_zip, app_dir, reporter=None, cancel_event=None,
                 output_dir=None, separate_folder=True, delete_source=False,
                 recursive=True):
        self.passwords = passwords or ['']
        self.seven = seven_zip
        self.app_dir = app_dir
        self.reporter = reporter or Reporter()
        self.cancel = cancel_event
        self.output_dir = output_dir            # None = 解压到压缩包所在目录
        self.separate_folder = separate_folder  # True = 再包一层同名文件夹
        self.delete_source = delete_source      # True = 成功后删除原始压缩包
        self.recursive = recursive              # True = 检测并解压二级（嵌套）压缩
        self._proc = None
        self._total = 1
        self._index = 0
        self.stats = {'ok': 0, 'fail': 0, 'outputs': [], 'deleted': 0, 'nested': 0}
        self.failures = []   # [{file, path, reason, advice, raw}]

    # ---------- 基础工具 ----------
    def _cancelled(self):
        return bool(self.cancel and self.cancel.is_set())

    def _pct(self, sub):
        base = (self._index - 1 + max(0.0, min(1.0, sub))) / max(self._total, 1)
        self.reporter.progress(round(base * 100, 1))

    def setup_password_library(self):
        lib_path = os.path.join(self.app_dir, 'passwords')
        if not os.path.isdir(lib_path):
            os.makedirs(lib_path)
        for f in ('0_temp.txt', '1_all.txt'):
            p = os.path.join(lib_path, f)
            if not os.path.exists(p):
                open(p, 'w').close()
        return lib_path

    def save_password(self, password):
        if not password:
            return
        lib_path = self.setup_password_library()
        main_lib = os.path.join(lib_path, '1_all.txt')
        try:
            with open(main_lib, 'r+', encoding='utf-8') as f:
                existing = {line.strip() for line in f}
                if password not in existing:
                    f.seek(0, 2)
                    f.write(f"\n{password}")
                    self.reporter.info("新密码 '***' 已自动保存到 '1_all.txt'。")
        except Exception as e:
            self.reporter.error(f"自动保存密码时出错: {e}")


    # ---------- 失败记录 ----------
    def note_failure(self, path, raw=''):
        """记录一次失败：归类原因 + 给出建议，并输出到界面日志。"""
        reason, advice = diagnose_failure(raw)
        detail = summarize_7z_output(raw)
        item = {'file': os.path.basename(path), 'path': path,
                'reason': reason, 'advice': advice, 'raw': detail}
        self.failures.append(item)
        self.reporter.error(f"解压失败：{item['file']} —— {reason}")
        self.reporter.info(f"        建议：{advice}")
        if detail:
            self.reporter.debug(f"        7-Zip: {detail}")
        return item

    def write_failure_report(self, directory, items=None):
        """把失败明细写成文本报告，返回路径。"""
        items = self.failures if items is None else items
        if not items:
            return None
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, '解压失败报告.txt')
            with open(path, 'w', encoding='utf-8-sig') as f:
                f.write('大肥鱼解压 · 解压失败报告\n')
                f.write('生成时间：' + time.strftime('%Y-%m-%d %H:%M:%S') + '\n')
                f.write(f'失败数量：{len(items)}\n')
                f.write('=' * 58 + '\n\n')
                for i, it in enumerate(items, 1):
                    f.write(f'{i}. {it["file"]}\n')
                    f.write(f'   原因：{it["reason"]}\n')
                    f.write(f'   建议：{it["advice"]}\n')
                    if it.get('raw'):
                        f.write(f'   7-Zip：{it["raw"]}\n')
                    if it.get('path'):
                        f.write(f'   路径：{it["path"]}\n')
                    f.write('\n')
                f.write('提示：解压失败的压缩包已移动到 _failed_archives 子目录。\n')
            return path
        except Exception as e:
            self.reporter.warn(f'写失败报告时出错: {e}')
            return None

    # ---------- 解压 ----------
    def _run_7z(self, src, password, out_dir):
        """流式调用 7-Zip，顺便把百分比推给界面。返回 (returncode, 输出文本)。"""
        cmd = [self.seven, 'x', src, f'-p{password}', f'-o{out_dir}', '-y', '-bsp1']
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, bufsize=0)
        except FileNotFoundError:
            return None, f"严重错误: 无法找到 7-Zip 程序: {self.seven}"
        except Exception as e:
            return None, f"未知子进程错误: {e}"

        self._proc = proc
        chunks, buf, last = [], b'', -1
        try:
            while True:
                if self._cancelled():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break
                data = proc.stdout.read(2048)
                if not data:
                    break
                chunks.append(data)
                buf += data
                while True:
                    idxs = [i for i in (buf.find(b'\r'), buf.find(b'\n')) if i >= 0]
                    if not idxs:
                        break
                    cut = min(idxs)
                    line, buf = buf[:cut], buf[cut + 1:]
                    text = line.decode('utf-8', 'ignore').strip()
                    if not text:
                        continue
                    m = re.search(r'(\d{1,3})%', text)
                    if m:
                        pct = int(m.group(1))
                        if pct != last:
                            last = pct
                            self._pct(pct / 100.0)
        finally:
            self._proc = None
            # 退出/取消时不要长时间等待，最多给 3 秒，再强杀
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
        return proc.returncode, b''.join(chunks).decode('utf-8', 'ignore')

    def attempt_decompression(self, file_to_decompress, output_dir):
        """尝试解压。返回 (status, message, password, raw_output)。"""
        passwords_to_try = [""] + [p for p in self.passwords if p]
        last_output = ""
        for password in passwords_to_try:
            if self._cancelled():
                return 'cancelled', "已取消", None, ""
            code, out = self._run_7z(file_to_decompress, password, output_dir)
            if code is None:
                return 'fatal_error', out, None, out
            if code == 0:
                self.save_password(password)
                return ('success',
                        f"密码正确，成功解压: {os.path.basename(file_to_decompress)}",
                        password, out)
            last_output = out
            low = out.lower()
            if "password" in low:
                continue
            break

        low = last_output.lower()
        if self._cancelled():
            return 'cancelled', "已取消", None, ""
        if "password" in low:
            return 'password_failure', "密码错误: 所有密码均尝试失败。", None, last_output
        if "cannot open" in low and "as archive" in low:
            return 'not_archive', "非压缩包: 文件不是有效压缩格式。", None, last_output
        return ('fatal_error',
                f"7-Zip致命错误: {summarize_7z_output(last_output)}",
                None, last_output)

    # ---------- 目录分析 ----------
    def normalize_split_archive_names(self, directory):
        """暂存区内就地规范化分卷名（安全：只动解压产物）。"""
        self.reporter.debug("开始分卷文件名规范化...")
        try:
            sets, _singles = scan_directory_for_sets(directory)
            for members in sets:
                if len(members) < 2:
                    continue
                anchor = members[0][1]
                info = parse_split_name(os.path.basename(anchor))
                if info:
                    base = sanitize_base(info[0])
                else:
                    base = sanitize_base(os.path.splitext(os.path.basename(anchor))[0], 'recovered')
                for idx, path in members:
                    current = os.path.basename(path)
                    target = f'{base}.{idx:03d}'
                    if current == target:
                        continue
                    dst = os.path.join(directory, target)
                    if os.path.exists(dst) and os.path.abspath(dst) != os.path.abspath(path):
                        continue
                    try:
                        os.rename(path, dst)
                        self.reporter.rename(current, target, '产物内规范化')
                    except OSError as e:
                        self.reporter.error(f"重命名文件时出错: {e}")
        except Exception as e:
            self.reporter.error(f"文件名规范化阶段出现意外错误: {e}")

    def detect_and_rename_disguised(self, directory):
        """识别二级（嵌套）压缩包：把伪装了扩展名的包的扩展名改回真实格式。

        返回识别到的数量。Office/ePub/JAR 等本来就是 ZIP 容器的格式会被跳过，
        分卷（.001/.002…）交给分卷逻辑处理，这里也不动。
        """
        count = 0
        try:
            for path in list_files(directory):
                name = os.path.basename(path)
                if parse_split_name(name):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in SKIP_RENAME_EXT:
                    continue
                kind = sniff_archive(path)
                if not kind:
                    continue
                real_ext = EXT_BY_KIND.get(kind)
                if not real_ext or ext == real_ext:
                    continue
                stem = os.path.splitext(name)[0]
                target = f"{stem}{real_ext}"
                dst = os.path.join(directory, target)
                i = 1
                while os.path.exists(dst):
                    target = f"{stem}_{i}{real_ext}"
                    dst = os.path.join(directory, target)
                    i += 1
                try:
                    os.rename(path, dst)
                    count += 1
                    self.stats['nested'] += 1
                    self.reporter.rename(name, target, '二级压缩')
                    self.reporter.info(f"检测到二级压缩: '{name}' 实为 {kind}，"
                                       f"已重命名为 '{target}'")
                except OSError as e:
                    self.reporter.warn(f"重命名 '{name}' 失败: {e}")
        except Exception as e:
            self.reporter.warn(f"二级压缩识别阶段出错: {e}")
        return count

    def analyze_directory(self, directory):
        self.normalize_split_archive_names(directory)
        if self.recursive:
            self.detect_and_rename_disguised(directory)
        self.reporter.debug("开始压缩包探测...")
        new_tasks, processed = [], set()
        all_files = list_files(directory)

        split_pattern = re.compile(r'\.(?:part\d{1,3}(?:\.rar)?|[zr]\d{2,3}|00[1-9]\d*)$', re.IGNORECASE)
        file_groups = {}
        for f_path in all_files:
            match = split_pattern.search(f_path)
            base_name = f_path[:match.start()] if match else os.path.splitext(f_path)[0]
            file_groups.setdefault(base_name, []).append(f_path)

        for base_name, file_list in file_groups.items():
            parts = [f for f in file_list if split_pattern.search(f)]
            if not parts:
                continue
            first_part_pattern = re.compile(r'\.(?:part0*1(?:\.rar)?|[zr]01|001)$', re.IGNORECASE)
            entry_point = next((f for f in parts if first_part_pattern.search(f)), None)
            if entry_point:
                new_tasks.append({'type': 'split', 'entry_path': entry_point, 'all_parts': parts})
                processed.update(parts)
                self.reporter.debug(f"识别出分卷任务: {os.path.basename(entry_point)} "
                                    f"(共 {len(parts)} 个部分)")

        remaining = [f for f in all_files if f not in processed]
        passwords_to_try = [""] + self.passwords
        for f_path in remaining:
            for password in passwords_to_try:
                probe = [self.seven, 'l', f_path, f'-p{password}']
                try:
                    result = subprocess.run(probe, capture_output=True, text=True,
                                            encoding='utf-8', errors='ignore')
                    if "Type =" in result.stdout:
                        new_tasks.append({'type': 'single', 'path': f_path})
                        self.reporter.debug(f"识别出独立压缩包任务: {os.path.basename(f_path)}")
                        break
                except Exception:
                    continue
        return new_tasks

    # ---------- 队列引擎 ----------
    def run_queue(self, staging_dir, tasks):
        queue = deque(tasks)
        ok = fail = 0
        while queue:
            if self._cancelled():
                break
            task = queue.popleft()
            entry = task.get('entry_path') or task.get('path')
            if not os.path.exists(entry):
                self.reporter.warn(f"任务文件已不存在，跳过: {os.path.basename(entry)}")
                continue
            self.reporter.stage("递归解压")
            self.reporter.info(f"正在处理任务: {os.path.basename(entry)}")
            status, message, _pwd, raw = self.attempt_decompression(entry, staging_dir)
            if status == 'cancelled':
                break
            if status == 'success':
                ok += 1
                self.reporter.success(message)
                to_delete = task.get('all_parts') or [task.get('path')]
                for f_path in to_delete:
                    try:
                        if f_path and os.path.exists(f_path):
                            os.remove(f_path)
                    except OSError:
                        pass
                new_tasks = self.analyze_directory(staging_dir)
                if new_tasks:
                    existing = {t.get('entry_path') or t.get('path') for t in queue}
                    uniq = [t for t in new_tasks
                            if (t.get('entry_path') or t.get('path')) not in existing]
                    if uniq:
                        queue.extend(uniq)
                        self.reporter.info(f"分析完毕，发现并添加了 {len(uniq)} 个新任务到队列。")
            else:
                fail += 1
                self.note_failure(entry, raw)
                failed_dir = os.path.join(staging_dir, '_failed_archives')
                os.makedirs(failed_dir, exist_ok=True)
                try:
                    shutil.move(entry, os.path.join(failed_dir, os.path.basename(entry)))
                    self.reporter.info("已将失败的压缩包隔离到 _failed_archives 目录。")
                except Exception as e:
                    self.reporter.error(f"隔离失败的压缩包时出错: {e}")
        return ok, fail

    # ---------- 单文件 ----------
    def _unique_dir(self, path):
        """避免和已有目录重名：xxx / xxx_1 / xxx_2 …"""
        if not os.path.exists(path):
            return path
        for i in range(1, 1000):
            cand = f"{path}_{i}"
            if not os.path.exists(cand):
                return cand
        return f"{path}_{''.join(random.choices(string.ascii_lowercase + string.digits, k=6))}"

    def _move_into(self, staging, dest_dir):
        """把暂存区内容直接倒进目标目录，重名时自动加序号。"""
        os.makedirs(dest_dir, exist_ok=True)
        for name in os.listdir(staging):
            s = os.path.join(staging, name)
            d = os.path.join(dest_dir, name)
            if os.path.exists(d):
                stem, ext = os.path.splitext(name)
                i = 1
                while os.path.exists(d):
                    d = os.path.join(dest_dir, f"{stem} ({i}){ext}")
                    i += 1
            shutil.move(s, d)

    def process_single_file(self, file_path):
        local_start = len(self.failures)
        src = os.path.abspath(file_path)
        src_dir = os.path.dirname(src)
        base_name = os.path.splitext(os.path.basename(src))[0]
        shadow_dir = None
        members = None
        try:
            # 先算一次分卷集合：既用于拼合，也用于“完成后删除压缩包”
            if not self._cancelled():
                members = collect_split_group(src)

            with tempfile.TemporaryDirectory(prefix=f"{base_name}_temp_",
                                             dir=src_dir) as staging:
                self.reporter.stage("识别与初次解压")
                self.reporter.info(f"为 '{os.path.basename(file_path)}' 创建暂存区: {staging}")
                status, message, _p1, raw1 = self.attempt_decompression(src, staging)
                first_raw = raw1

                if status != 'success':
                    if status != 'cancelled':
                        self.reporter.error(message)
                    if members and not self._cancelled():
                        self.reporter.stage("分卷拼合")
                        self.reporter.info(f"单文件解压失败，检测到分卷集合（共 {len(members)} 卷），尝试拼合：")
                        shadow_base = sanitize_base(os.path.splitext(os.path.basename(src))[0])
                        for idx, p in members:
                            self.reporter.rename(os.path.basename(p),
                                                 f"{shadow_base}.{idx:03d}", '分卷映射')
                            self.reporter.info(f"       .{idx:03d} <- {os.path.basename(p)}")
                        shadow_dir, entry = build_shadow_dir(
                            members, src_dir, os.path.basename(src))
                        if entry:
                            self.reporter.info(f"已建立影子目录（硬链接，不复制数据）: {shadow_dir}")
                            self.clear_dir(staging)
                            status, message, _p2, raw2 = self.attempt_decompression(entry, staging)
                            if status != 'success':
                                first_raw = raw2

                if status == 'cancelled':
                    self.reporter.warn("已取消当前文件。")
                    return
                if status != 'success':
                    self.note_failure(src, first_raw)
                    self.reporter.error(f"初始文件 '{os.path.basename(file_path)}' 解压失败，跳过此文件。")
                    self.stats['fail'] += 1
                    return

                self.reporter.success(message)
                nested_before = self.stats['nested']
                if self.recursive:
                    self.reporter.stage("扫描解压产物")
                    tasks = self.analyze_directory(staging)
                    if not tasks:
                        self.reporter.info("未在第一层产物中发现二级压缩包。")
                    else:
                        self.reporter.info(f"分析完成，发现 {len(tasks)} 个二级压缩任务。")
                    ok, fail = self.run_queue(staging, tasks)
                else:
                    self.reporter.info("已关闭二级解压：只解第一层。")
                    ok, fail = 0, 0
                nested_found = self.stats['nested'] - nested_before
                if nested_found:
                    self.reporter.success(f"二级压缩：本次识别并重命名 {nested_found} 个嵌套包（已解压）")
                if self._cancelled():
                    self.reporter.warn("已取消。")
                    return

                produced = sum(len(fs) for _r, _d, fs in os.walk(staging))
                target_root = self.output_dir or src_dir
                try:
                    os.makedirs(target_root, exist_ok=True)
                except OSError as e:
                    self.reporter.error(f"无法创建解压目录 '{target_root}': {e}")
                    self.stats['fail'] += 1
                    return

                try:
                    if self.separate_folder:
                        dest = self._unique_dir(os.path.join(target_root, base_name))
                        shutil.move(staging, dest)
                    else:
                        dest = target_root
                        self._move_into(staging, dest)
                    self.stats['ok'] += 1
                    self.stats['fail'] += fail
                    self.stats['outputs'].append(dest)
                    self.reporter.success(f"成功处理压缩包: {ok + 1} 个 (包含初始文件)")
                    self.reporter.info(f"最终文件已输出到: {dest}")
                    if fail:
                        self.reporter.error(f"解压失败压缩包: {fail} 个 (详情请见 _failed_archives 子目录)")
                except Exception as e:
                    self.reporter.error(f"最后移动暂存区时发生致命错误: {e}")
                    self.stats['fail'] += 1
                    return

                if self.delete_source:
                    if produced <= 0:
                        self.reporter.warn("解出内容为空，为安全起见不删除原始压缩包。")
                    else:
                        victims = [p for _i, p in members] if members else [src]
                        for v in victims:
                            try:
                                os.remove(v)
                                self.stats['deleted'] += 1
                                self.reporter.info(f"已删除原始压缩包: {os.path.basename(v)}")
                            except OSError as e:
                                self.reporter.warn(f"删除 '{os.path.basename(v)}' 失败: {e}")
        except Exception as e:
            self.reporter.error(f"处理文件 '{os.path.basename(file_path)}' 时发生未预料的严重错误: {e}")
            self.stats['fail'] += 1
        finally:
            if shadow_dir:
                shutil.rmtree(shadow_dir, ignore_errors=True)
            self._pct(1.0)

    def clear_dir(self, directory):
        for entry in os.listdir(directory):
            p = os.path.join(directory, entry)
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)
            except OSError:
                pass

    # ---------- 目录 ----------
    def process_directory(self, directory):
        sets, singles = scan_directory_for_sets(directory)
        if sets:
            self.reporter.info(f"目录中发现 {len(sets)} 组分卷集合，将整组处理。")
        for members in sets:
            if self._cancelled():
                return
            self.reporter.info(f"分卷组（{len(members)} 卷）入口: {os.path.basename(members[0][1])}")
            self.process_single_file(members[0][1])
        for f in singles:
            if self._cancelled():
                return
            self.reporter.info(f"独立文件: {os.path.basename(f)}")
            self.process_single_file(f)

    # ---------- 入口 ----------
    def run(self, paths):
        """处理一批文件/文件夹，返回统计。同一分卷集合只处理一次。"""
        units, seen = [], set()
        for path in paths:
            if os.path.isfile(path):
                group = collect_split_group(path)
                key = tuple(p for _i, p in group) if group else (os.path.abspath(path),)
                if key in seen:
                    self.reporter.info(f"该文件所属的分卷集合已在队列中，跳过重复项: "
                                       f"{os.path.basename(path)}")
                    continue
                seen.add(key)
                units.append(path)
            elif os.path.isdir(path):
                sets, singles = scan_directory_for_sets(path)
                for members in sets:
                    key = tuple(p for _i, p in members)
                    if key in seen:
                        continue
                    seen.add(key)
                    units.append(members[0][1])
                for f in singles:
                    units.append(f)
        self._total = max(len(units), 1)
        for i, path in enumerate(units, start=1):
            if self._cancelled():
                break
            self._index = i
            self.reporter.task(i, self._total, os.path.basename(path))
            self._pct(0.0)
            self.process_single_file(path)
        self._pct(1.0)
        self.stats['failures'] = list(self.failures)
        if self.failures:
            base = self.output_dir
            if not base:
                for p in paths:
                    if os.path.exists(p):
                        base = p if os.path.isdir(p) else os.path.dirname(os.path.abspath(p))
                        break
            if base:
                rep = self.write_failure_report(base)
                if rep:
                    self.stats['report'] = rep
                    self.reporter.error(f"失败报告已生成：{rep}")
        return self.stats

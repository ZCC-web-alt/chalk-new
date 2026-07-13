from typing import List, Tuple, Optional
import re

import fitz  # PyMuPDF — 同时用于文本和图片提取，CJK 支持远优于 pypdf


# 图片过滤阈值：过滤掉太小的图标/装饰性图片
MIN_IMAGE_WIDTH = 100
MIN_IMAGE_HEIGHT = 100
MAX_IMAGE_AREA = 5000 * 5000  # 防止异常大的图片
MAX_ASPECT_RATIO = 3.0  # 宽高比超过此值的视为装饰条/碎片，过滤掉


def _is_valid_image(width: int, height: int) -> bool:
    """判断图片尺寸是否有效（过滤掉小图标、装饰性元素、窄长条碎片）"""
    if width <= 0 or height <= 0:
        return False
    if width < MIN_IMAGE_WIDTH or height < MIN_IMAGE_HEIGHT:
        return False
    if width * height > MAX_IMAGE_AREA:
        return False
    # 过滤极端比例的图片（如细长条装饰线、水平条带碎片）
    ratio = max(width, height) / min(width, height)
    if ratio > MAX_ASPECT_RATIO:
        return False
    return True


# ─────────────────────────────────────────────────────────────
# 文本清洗：去除 PDF 提取中的乱码和噪声
# ─────────────────────────────────────────────────────────────

# 控制字符（保留 \n \r \t）
_CTRL_CHAR_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')

# 连续特殊符号（3 个以上非空白非标点非 CJK 的符号）
_NOISE_SYMBOL_RE = re.compile(r'[^\w\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u2000-\u206f\u0080-\u024f\u0400-\u04ff\u0600-\u06ff\u0900-\u097f]{3,}')

# 常见乱码模式：重复的 Latin Extended / Supplement 字符（ÿþ 等）
_GARBLED_PATTERN_RE = re.compile(
    r'[\u0080-\u00ff]{4,}'   # 连续4+个 Latin-1 Supplement 字符（如 ÿþÿþ）
    r'|[\u0100-\u024f]{4,}'  # 连续4+个 Latin Extended 字符
    r'|[\ufffd]{3,}'         # 连续3+个替换字符 U+FFFD（解码失败标志）
)

# CJK 字体映射乱码特征：同一汉字连续重复 5+ 次（如「的的的的的」）
# 正常文本极少出现，但 ToUnicode 表缺失时 fitz 会出现这种模式
_CJK_REPEAT_RE = re.compile(r'([\u4e00-\u9fff])\1{4,}')

# 常见科学/数学 Unicode 字符（不算乱码）
_KNOWN_SYMBOLS = set('.,;:!?()-–—/\\=+×÷±≤≥≠≈∞αβγδεζηθλμσφψωΔΩ°℃%‰'
                     '，。；：！？（）—……、·《》【】""''→←↑↓↔⇒⇐⇑⇓'
                     '₂₃₄₅₆₇₈₉₀₁⁰¹²³⁴⁵⁶⁷⁸⁹')

# 乱码行特征：可打印字符占比极低
def _is_garbled_line(line: str, threshold: float = 0.5) -> bool:
    """
    判断一行文本是否为乱码。

    判据：
      1. 行长度 < 2 → 跳过（太短无法判断）
      2. 可打印字符（含 CJK、拉丁、数字、常见标点）占比 < threshold → 视为乱码
      3. 连续特殊符号过多 → 视为乱码
      4. 匹配常见乱码模式（如 ÿþÿþ） → 视为乱码
    """
    if len(line) < 2:
        return False

    # 快速检测常见乱码模式
    if _GARBLED_PATTERN_RE.search(line):
        return True

    # CJK 字体映射乱码：同一汉字连续重复 5+ 次
    if _CJK_REPEAT_RE.search(line):
        return True

    # 统计可打印字符
    printable = 0
    for ch in line:
        if ch.isalnum() or ch.isspace():
            printable += 1
        elif '\u4e00' <= ch <= '\u9fff':  # CJK
            printable += 1
        elif ch in _KNOWN_SYMBOLS:
            printable += 1

    ratio = printable / len(line)
    if ratio < threshold:
        return True

    # 连续特殊符号检测
    if _NOISE_SYMBOL_RE.search(line):
        return True

    return False


def clean_pdf_text(text: str) -> str:
    """
    清洗 PDF 提取文本，去除乱码和噪声。

    处理步骤：
      1. 去除控制字符（保留换行/制表符）
      2. 逐行过滤乱码行
      3. 合并多余空行
    """
    if not text:
        return ""

    # 1. 去除控制字符
    text = _CTRL_CHAR_RE.sub('', text)

    # 2. 逐行过滤乱码
    lines = text.splitlines()
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            clean_lines.append('')
            continue
        if _is_garbled_line(stripped):
            clean_lines.append('')  # 乱码行替换为空行，保持段落结构
            continue
        clean_lines.append(line)

    # 3. 合并连续空行（最多保留 2 个空行，保持段落分隔）
    result_lines = []
    empty_count = 0
    for line in clean_lines:
        if not line.strip():
            empty_count += 1
            if empty_count <= 2:
                result_lines.append(line)
            # 超过 2 个连续空行则跳过
        else:
            empty_count = 0
            result_lines.append(line)

    return '\n'.join(result_lines).strip()


def is_garbled_text(text: str, sample_chars: int = 500) -> bool:
    """判断一段文本是否为 pypdf 乱码。

    检测策略：
      1. 采样前 sample_chars 个字符
      2. 在非 ASCII、非 CJK、非常见符号的字符中，统计 Indic/Thai 等乱码区间的数量
      3. 如果这类"异常 Unicode"字符 >= 3 个，认为是 pypdf 的 Identity-H CID→错误 Unicode 映射

    典型特征：SimSun/SimHei/FangSong 等 Identity-H 编码字体缺少 ToUnicode 时，
    pypdf 会把 CID 映射到印地语/泰语/藏文等 Unicode 区间，而非正确的 CJK 字符。
    正常科学论文中极少出现这些文字。
    """
    if not text or len(text) < 20:
        return False

    sample = text[:sample_chars]
    # pypdf 乱码的典型替代区间（正常科学论文不会出现这些文字）
    garbled_ranges = [
        (0x0900, 0x0D7F),  # Devanagari / Bengali / Gurmukhi / Gujarati / Oriya / Tamil / Telugu / Kannada / Malayalam / Sinhala / Thai / Lao
        (0x0F00, 0x0FFF),  # 藏文
        (0x1000, 0x109F),  # 缅甸文
        (0x10A0, 0x10FF),  # 格鲁吉亚文
        (0xA800, 0xAFFF),  # Syloti Nagri / Phags Pa
    ]
    garbled_count = 0
    for ch in sample:
        cp = ord(ch)
        for lo, hi in garbled_ranges:
            if lo <= cp <= hi:
                garbled_count += 1
                break

    # 阈值：>= 3 个异常 Unicode 字符即判定为乱码
    # 正常科学论文中几乎不会出现 Indic/Thai/藏文等字符
    return garbled_count >= 3


def extract_text_from_pdf(path: str) -> str:
    """使用 PyMuPDF 提取 PDF 文本。

    相比 pypdf，fitz 对以下场景表现更好：
      - CJK 字体映射（中文论文常见）
      - 图片多的 PDF（不会因内嵌图片中断提取）
      - 复杂编码/ToUnicode 缺失的 PDF
    """
    try:
        doc = fitz.open(path)
    except Exception:
        return ""

    parts: List[str] = []
    for page in doc:
        try:
            # TEXT = 0 (纯文本), 保持原始排版
            text = page.get_text("text") or ""
        except Exception:
            text = ""
        if text:
            parts.append(text)

    doc.close()
    raw_text = "\n".join(parts)
    return clean_pdf_text(raw_text)


def split_text_into_chunks(text: str, max_chars: int = 800, overlap_chars: int = 200) -> List[str]:
    """
    按段落+长度切分，支持 overlap 窗口，用于向量检索。
    overlap 确保相邻 chunk 有重叠内容，减少论点断裂。
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    chunks: List[str] = []
    buf: List[str] = []
    size = 0
    for line in lines:
        if size + len(line) > max_chars and buf:
            chunks.append("\n".join(buf))
            overlap_buf = []
            overlap_size = 0
            for l in reversed(buf):
                if overlap_size + len(l) > overlap_chars:
                    break
                overlap_buf.insert(0, l)
                overlap_size += len(l)
            buf = overlap_buf + [line]
            size = overlap_size + len(line)
        else:
            buf.append(line)
            size += len(line)
    if buf:
        chunks.append("\n".join(buf))
    return chunks



def extract_images_from_pdf(
    path: str, pages: Optional[List[int]] = None, filter_small: bool = True
) -> List[Tuple[bytes, str, int, int]]:
    """提取 PDF 中的完整图片（自动合并被切片的 tiles），返回 [(bytes, ext, width, height), ...]

    很多 PDF 由排版软件生成，图片被切成多条水平 tiles（每个是独立 XObject），
    直接用 get_images() 会返回碎片。本函数通过聚类 bbox 自动合并同一图片的所有 tiles，
    再用页面渲染 (get_pixmap) 提取完整图片。

    Args:
        path: PDF 文件路径
        pages: 页码列表（从1开始），None 表示所有页面
        filter_small: 是否过滤掉小图标/装饰性图片（默认 True）
    """
    imgs: List[Tuple[bytes, str, int, int]] = []
    doc = fitz.open(path)

    # 计算需要遍历的页索引（0-based）
    if pages:
        indices = [p - 1 for p in pages if 1 <= p <= len(doc)]
    else:
        indices = list(range(len(doc)))

    for idx in indices:
        page = doc[idx]
        img_infos = page.get_image_info(xrefs=True)
        if not img_infos:
            continue

        # ── 步骤1: 收集所有图片 bbox ───
        # 过滤极端小的和明显是装饰条/碎片的窄长条
        bboxes: List[Tuple[float, float, float, float]] = []
        for info in img_infos:
            bbox = info.get("bbox")
            if not bbox:
                continue
            x0, y0, x1, y1 = bbox
            w, h = x1 - x0, y1 - y0
            # 过滤极端小的（0/负尺寸）
            if w < 1 or h < 1:
                continue
            # 过滤窄长条（装饰线、水平条带碎片）
            # tiles 可能本身很窄，但宽高比不应超过 5（真正的 tile 是宽矮的，但不会极端窄长）
            if max(w, h) / min(w, h) > 5:
                continue
            bboxes.append((x0, y0, x1, y1))

        if not bboxes:
            continue

        # ── 步骤2: 聚类 —— 合并 x 范围重叠、y 相邻的 tiles ──────
        # 算法：按 x0 排序，对 x 范围重叠的 tiles 做 y 方向连通合并
        merged = _merge_image_tiles(bboxes)

        # ── 步骤3: 对每个合并区域，用页面渲染提取完整图片 ──────
        for rect in merged:
            try:
                clip = fitz.Rect(rect)
                # 确保 clip 在页面范围内
                clip = clip & page.rect
                if clip.is_empty or clip.width < 1 or clip.height < 1:
                    continue

                # 预过滤：合并后的区域如果仍是窄长条，跳过渲染
                if clip.width > 0 and clip.height > 0:
                    ratio = max(clip.width, clip.height) / min(clip.width, clip.height)
                    if ratio > MAX_ASPECT_RATIO:
                        continue

                pix = page.get_pixmap(clip=clip, dpi=150)
                img_bytes = pix.tobytes("png")
                w, h = pix.width, pix.height

                if filter_small and not _is_valid_image(w, h):
                    continue

                imgs.append((img_bytes, "png", w, h))
            except Exception:
                continue

    doc.close()
    return imgs


def _merge_image_tiles(
    bboxes: List[Tuple[float, float, float, float]],
    x_overlap_thresh: float = 0.6,
    y_gap_thresh: float = 5.0,
) -> List[Tuple[float, float, float, float]]:
    """将相邻/重叠的 image tiles 合并为完整的图片区域。

    策略：
      1. 对所有 bbox 按 y0 排序
      2. 依次检查每个 bbox 是否与已有聚类"连通"：
         - x 范围重叠比例 >= x_overlap_thresh（同一列的 tiles）
         - y 方向间距 <= y_gap_thresh（上下紧邻）
      3. 连通则合并，否则新建聚类
      4. 返回每个聚类的合并 bbox

    Args:
        bboxes: [(x0, y0, x1, y1), ...] 列表
        x_overlap_thresh: x 范围重叠比例阈值（0-1），低于此视为不同图片
        y_gap_thresh: y 方向最大间距（pt），超过此视为不同图片
    """
    if not bboxes:
        return []

    # 按 y0 排序
    sorted_boxes = sorted(bboxes, key=lambda b: (b[1], b[0]))

    # 每个聚类: List[(x0,y0,x1,y1)]
    clusters: List[List[Tuple[float, float, float, float]]] = [[sorted_boxes[0]]]

    def _x_overlap_ratio(a, b) -> float:
        """计算两个 bbox 在 x 方向的重叠比例（取较短宽度的比例）。"""
        overlap = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        wa = a[2] - a[0]
        wb = b[2] - b[0]
        min_w = min(wa, wb)
        if min_w <= 0:
            return 0.0
        return overlap / min_w

    def _y_adjacent(cluster, box) -> bool:
        """判断 box 是否与 cluster 中任意元素 y 方向相邻。"""
        for member in cluster:
            # box 在 member 正下方
            if abs(box[1] - member[3]) <= y_gap_thresh:
                return True
            # box 在 member 正上方
            if abs(member[1] - box[3]) <= y_gap_thresh:
                return True
            # y 范围重叠
            if box[1] < member[3] and box[3] > member[1]:
                return True
        return False

    for box in sorted_boxes[1:]:
        placed = False
        for cluster in clusters:
            # 检查与聚类中任意成员是否 x 重叠 + y 相邻
            for member in cluster:
                x_ratio = _x_overlap_ratio(member, box)
                if x_ratio >= x_overlap_thresh and _y_adjacent(cluster, box):
                    cluster.append(box)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            clusters.append([box])

    # 合并每个聚类的 bbox
    result: List[Tuple[float, float, float, float]] = []
    for cluster in clusters:
        x0 = min(b[0] for b in cluster)
        y0 = min(b[1] for b in cluster)
        x1 = max(b[2] for b in cluster)
        y1 = max(b[3] for b in cluster)
        result.append((x0, y0, x1, y1))

    return result

